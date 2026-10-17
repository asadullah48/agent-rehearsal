"""SQLite persistence: one database per run, foreign keys on, explicit transactions.

Every world mutation and the events describing it are written inside a single
``BEGIN IMMEDIATE`` transaction. Tool calls (including rejected ones) and their
recorded responses are journaled in that same transaction, sharing one sequence
counter with events so playback has a total order without re-executing anything.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_rehearsal import SCHEMA_VERSION
from agent_rehearsal.canonical import canonical_json, format_ts, parse_ts, sha256_hex
from agent_rehearsal.models import Actor

if TYPE_CHECKING:
    from agent_rehearsal.policy import Policy
    from agent_rehearsal.scenarios import Scenario

SCHEMA = """
CREATE TABLE run_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE evidence_chain (
    record_index INTEGER PRIMARY KEY,
    record_json TEXT NOT NULL
);
CREATE TABLE fault_firings (
    fault_id TEXT PRIMARY KEY,
    occurrence INTEGER NOT NULL,
    effect TEXT NOT NULL,
    event_id TEXT NOT NULL
);

CREATE TABLE suppliers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'suspended')),
    version INTEGER NOT NULL CHECK (version >= 1)
);

CREATE TABLE budgets (
    id TEXT PRIMARY KEY,
    currency TEXT NOT NULL,
    total_minor INTEGER NOT NULL CHECK (total_minor >= 0),
    available_minor INTEGER NOT NULL CHECK (available_minor >= 0),
    spent_minor INTEGER NOT NULL CHECK (spent_minor >= 0),
    version INTEGER NOT NULL CHECK (version >= 1)
);

CREATE TABLE requests (
    id TEXT PRIMARY KEY,
    supplier_id TEXT NOT NULL REFERENCES suppliers(id),
    budget_id TEXT NOT NULL REFERENCES budgets(id),
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    currency TEXT NOT NULL,
    description TEXT NOT NULL,
    proposal_hash TEXT NOT NULL
);

CREATE TABLE reservations (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES requests(id),
    budget_id TEXT NOT NULL REFERENCES budgets(id),
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    state TEXT NOT NULL CHECK (state IN ('active', 'consumed', 'released')),
    version INTEGER NOT NULL CHECK (version >= 1),
    created_at TEXT NOT NULL
);
-- At most one live (active or consumed) reservation per request.
CREATE UNIQUE INDEX one_live_reservation_per_request
    ON reservations(request_id) WHERE state IN ('active', 'consumed');

CREATE TABLE approvals (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES requests(id),
    supplier_id TEXT NOT NULL REFERENCES suppliers(id),
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    currency TEXT NOT NULL,
    proposal_hash TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    CHECK (expires_at > granted_at)
);

CREATE TABLE purchase_orders (
    id TEXT PRIMARY KEY,
    logical_intent_id TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL REFERENCES requests(id),
    reservation_id TEXT NOT NULL UNIQUE REFERENCES reservations(id),
    approval_id TEXT NOT NULL REFERENCES approvals(id),
    supplier_id TEXT NOT NULL REFERENCES suppliers(id),
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    currency TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    operation_key TEXT NOT NULL
);

CREATE TABLE idempotency (
    scope TEXT NOT NULL,
    operation_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    PRIMARY KEY (scope, operation_key)
);

CREATE TABLE tool_calls (
    seq INTEGER PRIMARY KEY,
    call_id TEXT NOT NULL UNIQUE,
    actor_id TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    tool TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    operation_key TEXT,
    status TEXT NOT NULL CHECK (status IN ('ok', 'error')),
    response_json TEXT NOT NULL,
    simulated_at TEXT NOT NULL
);

CREATE TABLE events (
    seq INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL,
    simulated_at TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    causation_id TEXT,
    policy_version TEXT NOT NULL,
    operation_key TEXT
);

CREATE TABLE workflow_transitions (
    seq INTEGER PRIMARY KEY,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    reason TEXT NOT NULL,
    simulated_at TEXT NOT NULL
);
"""

HARNESS = Actor(actor_id="HARNESS", role="harness")


class Tx:
    """Helpers available inside one open transaction."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._savepoints = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def row(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        cur = self.conn.execute(sql, params)
        found = cur.fetchone()
        return dict(found) if found is not None else None

    def meta(self, key: str) -> str:
        found = self.row("SELECT value FROM run_meta WHERE key = ?", (key,))
        if found is None:
            raise KeyError(key)
        return str(found["value"])

    def set_meta(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO run_meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def next_seq(self) -> int:
        seq = int(self.meta("next_seq"))
        self.set_meta("next_seq", str(seq + 1))
        return seq

    def next_id(self, prefix: str, width: int = 4) -> str:
        key = f"counter:{prefix}"
        try:
            value = int(self.meta(key)) + 1
        except KeyError:
            value = 1
        self.set_meta(key, str(value))
        return f"{prefix}-{value:0{width}d}"

    def now(self) -> str:
        return self.meta("now")

    def tick(self, seconds: int | None = None) -> str:
        """Advance the synthetic clock and return the new simulated time."""
        step = int(self.meta("tick_seconds")) if seconds is None else seconds
        new_now = format_ts(parse_ts(self.now()) + timedelta(seconds=step))
        self.set_meta("now", new_now)
        return new_now

    def add_event(
        self,
        actor: Actor,
        event_type: str,
        payload: dict[str, Any],
        *,
        causation_id: str | None = None,
        operation_key: str | None = None,
    ) -> str:
        seq = self.next_seq()
        event_id = self.next_id("EV", width=6)
        self.execute(
            "INSERT INTO events(seq, event_id, run_id, simulated_at, actor_id, actor_role, "
            "event_type, payload_json, causation_id, policy_version, operation_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                seq,
                event_id,
                self.meta("run_id"),
                self.now(),
                actor.actor_id,
                actor.role,
                event_type,
                canonical_json(payload),
                causation_id,
                self.meta("policy_version"),
                operation_key,
            ),
        )
        event = self.row("SELECT * FROM events WHERE event_id = ?", (event_id,))
        previous = self.row(
            "SELECT record_index, record_json FROM evidence_chain "
            "ORDER BY record_index DESC LIMIT 1"
        )
        record = {
            "index": previous["record_index"] + 1 if previous else 0,
            "event": event,
            "previous_hash": json.loads(previous["record_json"])["hash"] if previous else "0" * 64,
        }
        record["hash"] = sha256_hex(record)
        self.execute(
            "INSERT INTO evidence_chain VALUES (?, ?)", (record["index"], canonical_json(record))
        )
        return event_id

    def record_tool_call(
        self,
        *,
        seq: int,
        call_id: str,
        actor: Actor,
        tool: str,
        arguments: dict[str, Any],
        operation_key: str | None,
        status: str,
        response: dict[str, Any],
        simulated_at: str,
    ) -> None:
        self.execute(
            "INSERT INTO tool_calls(seq, call_id, actor_id, actor_role, tool, arguments_json, "
            "operation_key, status, response_json, simulated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                seq,
                call_id,
                actor.actor_id,
                actor.role,
                tool,
                canonical_json(arguments),
                operation_key,
                status,
                canonical_json(response),
                simulated_at,
            ),
        )

    @contextmanager
    def savepoint(self) -> Iterator[None]:
        self._savepoints += 1
        name = f"sp{self._savepoints}"
        self.execute(f"SAVEPOINT {name}")
        try:
            yield
        except BaseException:
            self.execute(f"ROLLBACK TO {name}")
            self.execute(f"RELEASE {name}")
            raise
        self.execute(f"RELEASE {name}")


class Store:
    def __init__(self, conn: sqlite3.Connection, path: Path) -> None:
        self.conn = conn
        self.path = path

    # --- construction ----------------------------------------------------------

    @staticmethod
    def _connect(target: str, *, uri: bool = False) -> sqlite3.Connection:
        conn = sqlite3.connect(target, uri=uri, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @classmethod
    def create(
        cls,
        path: Path,
        *,
        run_id: str,
        scenario: Scenario,
        policy: Policy,
        seed: int,
        adapter: dict[str, Any],
    ) -> Store:
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing database: {path}")
        conn = cls._connect(str(path))
        conn.executescript(SCHEMA)
        store = cls(conn, path)
        scenario_doc = scenario.model_dump(mode="json")
        world_doc = scenario.initial_world.model_dump(mode="json")
        policy_doc = policy.model_dump(mode="json")
        meta = {
            "schema_version": str(SCHEMA_VERSION),
            "run_id": run_id,
            "scenario_id": scenario.id,
            "scenario_version": scenario.version,
            "scenario_hash": sha256_hex(scenario_doc),
            "scenario_json": canonical_json(scenario_doc),
            "initial_world_hash": sha256_hex(world_doc),
            "initial_world_json": canonical_json(world_doc),
            "policy_version": policy.policy_version,
            "initial_policy_version": policy.policy_version,
            "policy_hash": sha256_hex(policy_doc),
            "policy_json": canonical_json(policy_doc),
            "seed": str(seed),
            "clock_origin": scenario.clock_origin,
            "now": scenario.clock_origin,
            "tick_seconds": str(scenario.tick_seconds),
            "adapter_json": canonical_json(adapter),
            "execution_status": "running",
            "next_seq": "1",
        }
        from agent_rehearsal.policy import proposal_hash  # local: keeps store import-light

        with store.transaction() as tx:
            for key, value in meta.items():
                tx.set_meta(key, value)
            for sup in scenario.initial_world.suppliers:
                tx.execute(
                    "INSERT INTO suppliers(id, name, status, version) VALUES (?, ?, ?, 1)",
                    (sup.id, sup.name, sup.status),
                )
            for bud in scenario.initial_world.budgets:
                tx.execute(
                    "INSERT INTO budgets(id, currency, total_minor, available_minor, "
                    "spent_minor, version) VALUES (?, ?, ?, ?, 0, 1)",
                    (bud.id, bud.currency, bud.total_minor, bud.total_minor),
                )
            for req in scenario.initial_world.requests:
                doc = req.model_dump(mode="json")
                tx.execute(
                    "INSERT INTO requests(id, supplier_id, budget_id, amount_minor, currency, "
                    "description, proposal_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        req.id,
                        req.supplier_id,
                        req.budget_id,
                        req.amount_minor,
                        req.currency,
                        req.description,
                        proposal_hash(doc),
                    ),
                )
            tx.add_event(
                HARNESS,
                "run.started",
                {
                    "scenario_id": scenario.id,
                    "scenario_version": scenario.version,
                    "seed": seed,
                    "adapter_id": adapter["id"],
                    "initial_world_hash": meta["initial_world_hash"],
                    "policy_hash": meta["policy_hash"],
                },
            )
        return store

    @classmethod
    def open_readonly(cls, path: Path) -> Store:
        if not path.is_file():
            raise FileNotFoundError(f"run database not found: {path}")
        uri = path.resolve().as_uri() + "?mode=ro"
        return cls(cls._connect(uri, uri=True), path)

    # --- access ----------------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[Tx]:
        self.conn.execute("BEGIN IMMEDIATE")
        tx = Tx(self.conn)
        try:
            yield tx
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def row(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        found = self.conn.execute(sql, params).fetchone()
        return dict(found) if found is not None else None

    def meta(self, key: str) -> str:
        found = self.row("SELECT value FROM run_meta WHERE key = ?", (key,))
        if found is None:
            raise KeyError(key)
        return str(found["value"])

    def all_meta(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self.rows("SELECT key, value FROM run_meta")}

    def close(self) -> None:
        self.conn.close()

    # --- harness bookkeeping (trusted; never exposed to the controller) --------

    def record_transition(self, from_state: str, to_state: str, reason: str) -> None:
        with self.transaction() as tx:
            tx.execute(
                "INSERT INTO workflow_transitions(seq, from_state, to_state, reason, "
                "simulated_at) VALUES (?, ?, ?, ?, ?)",
                (tx.next_seq(), from_state, to_state, reason, tx.now()),
            )

    def finish_run(
        self,
        execution_status: str,
        *,
        controller_state: str,
        summary: str,
        limit_reached: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        with self.transaction() as tx:
            tx.set_meta("execution_status", execution_status)
            payload = {
                "execution_status": execution_status,
                "controller_final_state": controller_state,
                "controller_summary": summary,
                "limit_reached": limit_reached,
            }
            payload.update(extra or {})
            return tx.add_event(HARNESS, "run.completed", payload)


def load_json(text: str) -> Any:
    return json.loads(text)
