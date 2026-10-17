"""Recorded playback.

Reads saved tool calls, recorded responses, events and workflow transitions from
``run.sqlite`` (opened read-only) and renders them in their original order. It
imports nothing that can execute a tool, drive the controller or call a model.
Playback is NOT crash recovery and NOT durable execution.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_rehearsal.canonical import canonical_json


@dataclass(frozen=True)
class PlaybackEntry:
    seq: int
    kind: str  # "tool_call" | "event" | "transition"
    simulated_at: str
    record: dict[str, Any]


def load_playback(db_path: Path) -> list[PlaybackEntry]:
    if not db_path.is_file():
        raise FileNotFoundError(f"run database not found: {db_path}")
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        entries: list[PlaybackEntry] = []
        for c in conn.execute("SELECT * FROM tool_calls"):
            rec = dict(c)
            rec["arguments"] = json.loads(rec.pop("arguments_json"))
            rec["response"] = json.loads(rec.pop("response_json"))
            entries.append(PlaybackEntry(rec["seq"], "tool_call", rec["simulated_at"], rec))
        for e in conn.execute("SELECT * FROM events"):
            rec = dict(e)
            rec["payload"] = json.loads(rec.pop("payload_json"))
            entries.append(PlaybackEntry(rec["seq"], "event", rec["simulated_at"], rec))
        for t in conn.execute("SELECT * FROM workflow_transitions"):
            rec = dict(t)
            entries.append(PlaybackEntry(rec["seq"], "transition", rec["simulated_at"], rec))
    finally:
        conn.close()
    return sorted(entries, key=lambda x: x.seq)


def render(entries: list[PlaybackEntry]) -> str:
    lines = []
    for entry in entries:
        r = entry.record
        head = f"[{entry.seq:04d}] {entry.simulated_at}"
        if entry.kind == "tool_call":
            resp = r["response"]
            lines.append(
                f"{head} CALL  {r['call_id']} {r['actor_role']}:{r['actor_id']} "
                f"{r['tool']} {canonical_json(r['arguments'])}"
            )
            if resp["status"] == "ok":
                replay = " (idempotent replay)" if resp.get("idempotent_replay") else ""
                lines.append(
                    f"{' ' * 28}-> ok{replay} events={resp['event_ids']} "
                    f"data={canonical_json(resp['data'])}"
                )
            else:
                err = resp["error"]
                lines.append(
                    f"{' ' * 28}-> error {err['code']}/{err['reason_code']} "
                    f"certainty={err['certainty']}: {err['message']}"
                )
        elif entry.kind == "event":
            lines.append(
                f"{head} EVENT {r['event_id']} {r['event_type']} by "
                f"{r['actor_role']}:{r['actor_id']} {canonical_json(r['payload'])}"
            )
        else:
            lines.append(f"{head} STATE {r['from_state']} -> {r['to_state']} ({r['reason']})")
    return "\n".join(lines)


def verify_against_report(entries: list[PlaybackEntry], report: dict[str, Any]) -> list[str]:
    """Compare recorded database evidence with the saved report; return mismatches."""
    problems = []
    db_calls = [e.record for e in entries if e.kind == "tool_call"]
    rep_calls = report.get("tool_calls", [])
    if [c["call_id"] for c in db_calls] != [c["call_id"] for c in rep_calls]:
        problems.append("tool call order differs between run.sqlite and report.json")
    for db_call, rep_call in zip(db_calls, rep_calls, strict=False):
        if canonical_json(db_call["response"]) != canonical_json(rep_call["response"]):
            problems.append(f"{db_call['call_id']}: recorded response differs from report")
    db_events = [e.record["event_id"] for e in entries if e.kind == "event"]
    if db_events != [e["event_id"] for e in report.get("events", [])]:
        problems.append("event order differs between run.sqlite and report.json")
    return problems
