"""Tamper-evident (not tamper-proof) event ledger and local evidence bundle.

Keep the final digest separately to detect an attacker rewriting the whole chain.
The ledger attests to recorded bytes, not the truth of simulated events.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent_rehearsal.canonical import canonical_json, sha256_hex
from agent_rehearsal.store import Store

GENESIS = "0" * 64


def export_bundle(run_dir: Path, report: dict) -> None:
    store = Store.open_readonly(run_dir / "run.sqlite")
    try:
        records = [
            json.loads(r["record_json"])
            for r in store.rows("SELECT record_json FROM evidence_chain ORDER BY record_index")
        ]
    finally:
        store.close()
    ledger = run_dir / "evidence.jsonl"
    ledger.write_text("".join(canonical_json(r) + "\n" for r in records), encoding="utf-8")
    anchor = {"count": len(records), "head": records[-1]["hash"] if records else GENESIS}
    (run_dir / "evidence-anchor.json").write_text(canonical_json(anchor) + "\n", encoding="utf-8")
    summary = (
        "# Agent Rehearsal evidence\n\nDeterministic controller; synthetic data only.\n\n"
        f"Execution: {report['execution_status']}\n\nOutcome: {report['business_outcome']}\n\n"
        f"Evaluation: {report['verdict']}\n\nEvents: {anchor['count']}\n\n"
        f"Final SHA-256: `{anchor['head']}`\n\n"
        "Tamper-evident (not tamper-proof). Retain the digest separately; a file owner can "
        "rewrite the entire chain and local anchor. No proof of external truth.\n"
    )
    (run_dir / "summary.md").write_text(summary, encoding="utf-8")
    names = ("run.sqlite", "report.json", "evidence.jsonl", "evidence-anchor.json", "summary.md")
    manifest = {name: hashlib.sha256((run_dir / name).read_bytes()).hexdigest() for name in names}
    (run_dir / "manifest.json").write_text(canonical_json(manifest) + "\n", encoding="utf-8")


def verify_ledger(path: Path, expected_head: str, expected_count: int) -> tuple[bool, int, str]:
    """Return success, first affected zero-based index, and explanation."""
    previous = GENESIS
    count = 0
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        try:
            record = json.loads(line)
            if not isinstance(record, dict) or set(record) != {
                "index",
                "event",
                "previous_hash",
                "hash",
            }:
                return False, index, "invalid record schema"
            if record["index"] != index or record["previous_hash"] != previous:
                return False, index, "index or previous hash mismatch"
            body = {k: v for k, v in record.items() if k != "hash"}
            if record["hash"] != sha256_hex(body):
                return False, index, "record hash mismatch"
            previous = record["hash"]
            count += 1
        except (ValueError, TypeError):
            return False, index, "invalid JSON record"
    if count != expected_count or previous != expected_head:
        return False, min(count, expected_count), "retained count or final digest mismatch"
    return True, count, "chain verified"
