"""Local recovery journal and abrupt, deterministic process-crash injection."""

from __future__ import annotations

import json
import os

from agent_rehearsal.canonical import canonical_json, sha256_hex
from agent_rehearsal.controller import AwaitReview, ToolProposal
from agent_rehearsal.models import ToolError, ToolResult
from agent_rehearsal.store import HARNESS, Store

CRASH_POINTS = (
    "before-reservation",
    "after-reservation",
    "after-approval",
    "before-purchase",
    "after-purchase-commit",
    "after-purchase-response",
)
CRASH_EXIT = 86


def crash_if(configured: str | None, boundary: str) -> None:
    if configured == boundary:
        os._exit(CRASH_EXIT)  # No finally blocks, clean shutdown, or bundle export.


def sync_ledger(store: Store) -> None:
    path = store.path.parent / "evidence.jsonl"
    records = store.rows("SELECT record_json FROM evidence_chain ORDER BY record_index")
    temporary = path.with_suffix(".tmp")
    temporary.write_text("".join(r["record_json"] + "\n" for r in records), encoding="utf-8")
    temporary.replace(path)


def validate_recovery_evidence(store: Store) -> None:
    """The JSONL mirror may lag SQLite, but must be an exact valid prefix."""
    rows = store.rows("SELECT record_json FROM evidence_chain ORDER BY record_index")
    events = store.rows("SELECT * FROM events ORDER BY seq")
    previous = "0" * 64
    if len(rows) != len(events):
        raise ValueError("database event/chain count mismatch")
    records = []
    for index, (row, event) in enumerate(zip(rows, events, strict=True)):
        record = json.loads(row["record_json"])
        body = {k: v for k, v in record.items() if k != "hash"}
        if (
            record.get("index") != index
            or record.get("event") != event
            or record.get("previous_hash") != previous
            or record.get("hash") != sha256_hex(body)
        ):
            raise ValueError(f"database evidence mismatch at index {index}")
        previous = record["hash"]
        records.append(record)
    path = store.path.parent / "evidence.jsonl"
    if not path.is_file():
        raise ValueError("missing recovery evidence.jsonl")
    mirror = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if len(mirror) > len(records) or mirror != records[: len(mirror)]:
        raise ValueError("exported ledger is not a valid database chain prefix")


class DecisionJournal:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.saved = store.rows("SELECT * FROM decision_journal ORDER BY step")
        self.step = 0

    def begin(self, decision: ToolProposal | AwaitReview) -> tuple[dict, bool]:
        self.step += 1
        doc = (
            {"tool": decision.tool, "arguments": decision.arguments}
            if isinstance(decision, ToolProposal)
            else {"review": decision.request_id}
        )
        encoded = canonical_json(doc)
        if self.step <= len(self.saved):
            row = self.saved[self.step - 1]
            if row["decision_json"] != encoded:
                raise ValueError(f"controller decision diverged at step {self.step}")
            return row, True
        with self.store.transaction() as tx:
            cursor = int(tx.meta("next_seq"))
            tx.execute(
                "INSERT INTO decision_journal VALUES (?, ?, ?, NULL)", (self.step, encoded, cursor)
            )
        return {"step": self.step, "cursor": cursor, "reply_json": None}, False

    def complete(self, reply: ToolResult | None) -> None:
        encoded = canonical_json(reply.model_dump(mode="json") if reply else None)
        with self.store.transaction() as tx:
            tx.execute(
                "UPDATE decision_journal SET reply_json = ? WHERE step = ?", (encoded, self.step)
            )
        sync_ledger(self.store)


def unknown_response(store: Store, reply: ToolResult, fault_id: str) -> tuple[ToolResult, str]:
    reply = reply.model_copy(
        update={
            "status": "error",
            "data": None,
            "event_ids": [],
            "error": ToolError(
                code="OUTCOME_UNKNOWN",
                reason_code="OUTCOME_UNKNOWN",
                message="purchase response unavailable after commit",
                certainty="unknown",
            ),
        }
    )
    with store.transaction() as tx:
        event_id = tx.add_event(
            HARNESS, "response.lost", {"call_id": reply.call_id, "fault_id": fault_id}
        )
        tx.execute(
            "UPDATE tool_calls SET status='error', response_json=? WHERE call_id=?",
            (canonical_json(reply.model_dump(mode="json")), reply.call_id),
        )
    return reply, event_id
