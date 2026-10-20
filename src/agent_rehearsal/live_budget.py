"""Shared, durable application budget. All monetary arithmetic uses integer nanodollars."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

MODEL = "claude-haiku-4-5-20251001"
NANO = 1_000_000_000


class LiveStop(RuntimeError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def dollars(value: str) -> int:
    try:
        number = Decimal(value) * NANO
    except InvalidOperation as exc:
        raise ValueError("budget must be a positive USD decimal") from exc
    if not number.is_finite() or number <= 0 or number != number.to_integral_value():
        raise ValueError("budget must be positive USD with at most nine decimal places")
    if number > 10**15:
        raise ValueError("budget is too large")
    return int(number)


@dataclass(frozen=True)
class Pricing:
    model: str
    input_nano: int
    output_nano: int
    source: str
    checked: str

    def __post_init__(self):
        if (
            self.model != MODEL
            or type(self.input_nano) is not int
            or type(self.output_nano) is not int
            or not min(self.input_nano, self.output_nano) > 0
            or max(self.input_nano, self.output_nano) > 10**9
            or not isinstance(self.source, str)
            or not self.source.startswith("https://platform.claude.com/")
            or not isinstance(self.checked, str)
        ):
            raise ValueError("missing or unsupported model pricing")
        date.fromisoformat(self.checked)

    @classmethod
    def load(cls, path: Path) -> Pricing:
        doc = json.loads(path.read_text(encoding="utf-8"))
        try:
            return cls(**doc)
        except TypeError as exc:
            raise ValueError("missing or unsupported model pricing") from exc

    def cost(self, inputs: int, outputs: int) -> int:
        return inputs * self.input_nano + outputs * self.output_nano


class LedgerCapMismatch(ValueError):
    def __init__(self, path: Path, existing: int, requested: int):
        super().__init__(
            f"ledger {path} is locked at cap ${existing / NANO:.9f}; requested "
            f"${requested / NANO:.9f}. Caps are never changed. Use a new --ledger path "
            f"for a separate budget, e.g. --ledger {path.parent / 'eval-<name>.sqlite'}"
        )
        self.path, self.existing, self.requested = path, existing, requested


def _summarize(rows) -> dict:
    return {
        "calls": len(rows),
        "reserved_nano_usd": sum(r["reserved"] for r in rows),
        "charged_nano_usd": sum(r["charged"] for r in rows),
        "actual_known_nano_usd": sum(r["actual"] or 0 for r in rows),
        "actual_cost_complete": all(r["actual"] is not None for r in rows),
        "open_reservations": sum(r["status"] == "open" for r in rows),
        "uncertain_calls": sum(r["status"] == "uncertain" for r in rows),
    }


def read_ledger(path: Path) -> dict | None:
    """Read-only view of a ledger; None when no initialised ledger exists at path."""
    if not path.is_file():
        return None
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"budget", "calls"} <= tables:
            return None
        cap = conn.execute("SELECT cap FROM budget").fetchone()
        if cap is None:
            return None
        rows = conn.execute("SELECT * FROM calls").fetchall()
        return {"path": str(path.resolve()), "cap_nano_usd": cap[0], **_summarize(rows)}
    finally:
        conn.close()


class BudgetLedger:
    """One path for all concurrent runs; the cap cannot be changed by reopening it.

    Open reservations remain charged across process death. No reset/release API.
    A new budget requires an explicitly chosen new ledger.
    """

    def __init__(self, path: Path, cap: int):
        if type(cap) is not int or not 0 < cap <= 10**15:
            raise ValueError("invalid ledger cap")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS budget (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), cap INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS calls (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                reserved INTEGER NOT NULL CHECK(reserved>0),
                charged INTEGER NOT NULL CHECK(charged>=0),
                actual INTEGER, status TEXT NOT NULL,
                input_bound INTEGER NOT NULL, max_tokens INTEGER NOT NULL);
        """)
        with self.transaction():
            self.conn.execute("INSERT OR IGNORE INTO budget VALUES (1, ?)", (cap,))
            existing = self.conn.execute("SELECT cap FROM budget").fetchone()[0]
            if existing != cap:
                raise LedgerCapMismatch(path, existing, cap)

    @contextmanager
    def transaction(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def reserve(self, call_id: str, run_id: str, amount: int, inputs: int, outputs: int) -> None:
        with self.transaction():
            if self.conn.execute("SELECT 1 FROM calls WHERE status='uncertain' LIMIT 1").fetchone():
                raise LiveStop("stopped_uncertain", "shared ledger has uncertain billing; stopped")
            if self.conn.execute("SELECT 1 FROM calls WHERE id=?", (call_id,)).fetchone():
                raise LiveStop("stopped_uncertain", "dispatch already reserved; never redispatch")
            cap = self.conn.execute("SELECT cap FROM budget").fetchone()[0]
            charged = self.conn.execute("SELECT COALESCE(SUM(charged),0) FROM calls").fetchone()[0]
            if charged + amount > cap:
                raise LiveStop("stopped_budget", "shared model budget exhausted before dispatch")
            self.conn.execute(
                "INSERT INTO calls VALUES (?, ?, ?, ?, NULL, 'open', ?, ?)",
                (call_id, run_id, amount, amount, inputs, outputs),
            )

    def settle(self, call_id: str, actual: int | None, *, uncertain: bool = False) -> None:
        if actual is not None and (type(actual) is not int or actual < 0):
            raise ValueError("invalid actual cost")
        with self.transaction():
            row = self.conn.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
            if row is None or row["status"] != "open":
                raise ValueError("reservation missing or already settled")
            uncertain = uncertain or actual is None or actual > row["reserved"]
            # If pricing/token assumptions were exceeded, disclose the full known liability.
            charged = max(row["reserved"], actual or 0) if uncertain else actual
            self.conn.execute(
                "UPDATE calls SET charged=?, actual=?, status=? WHERE id=?",
                (charged, actual, "uncertain" if uncertain else "settled", call_id),
            )
        if uncertain:
            raise LiveStop("stopped_uncertain", "billing uncertain or reservation bound exceeded")

    def summary(self, run_id: str | None = None) -> dict:
        rows = self.conn.execute(
            "SELECT * FROM calls" + (" WHERE run_id=?" if run_id else ""),
            (run_id,) if run_id else (),
        ).fetchall()
        return _summarize(rows)

    def close(self) -> None:
        self.conn.close()
