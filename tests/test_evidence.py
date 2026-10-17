"""Ledger tampering is detected against an independently retained anchor."""

import json
import sqlite3

import pytest

from agent_rehearsal.cli import main
from agent_rehearsal.evidence import verify_ledger


def anchor(run):
    return json.loads((run.run_dir / "evidence-anchor.json").read_text())


def test_untouched_log_and_cli_verify(completed_run):
    run = completed_run
    a = anchor(run)
    assert verify_ledger(run.run_dir / "evidence.jsonl", a["head"], a["count"])[0]
    assert main(["verify", "--run-dir", str(run.run_dir)]) == 0
    with sqlite3.connect(run.run_dir / "run.sqlite") as conn:
        events = conn.execute("SELECT * FROM events ORDER BY seq").fetchall()
        records = conn.execute(
            "SELECT record_json FROM evidence_chain ORDER BY record_index"
        ).fetchall()
    assert len(events) == len(records) == a["count"]
    assert [json.loads(r[0])["event"]["event_id"] for r in records] == [e[1] for e in events]


@pytest.mark.parametrize(
    "change,index", [("edit", 1), ("delete", 1), ("reorder", 1), ("truncate", -1)]
)
def test_tamper_fails_at_affected_index(completed_run, change, index):
    run = completed_run
    a = anchor(run)
    p = run.run_dir / "evidence.jsonl"
    lines = p.read_text().splitlines()
    if change == "edit":
        r = json.loads(lines[index])
        r["event"]["actor_id"] = "FORGED"
        lines[index] = json.dumps(r)
    elif change == "delete":
        del lines[index]
    elif change == "reorder":
        lines[index], lines[index + 1] = lines[index + 1], lines[index]
    else:
        lines.pop()
        index = len(lines)
    p.write_text("\n".join(lines) + "\n")
    ok, affected, _ = verify_ledger(p, a["head"], a["count"])
    assert not ok
    assert affected == index
    assert main(["verify", "--run-dir", str(run.run_dir)]) == 1


def test_rewritten_chain_fails_retained_digest(completed_run):
    from agent_rehearsal.canonical import canonical_json, sha256_hex
    from agent_rehearsal.evidence import GENESIS

    p = completed_run.run_dir / "evidence.jsonl"
    a = anchor(completed_run)
    records = [json.loads(line) for line in p.read_text().splitlines()]
    records[0]["event"]["actor_id"] = "FORGED"
    previous = GENESIS
    for record in records:
        record["previous_hash"] = previous
        body = {k: v for k, v in record.items() if k != "hash"}
        record["hash"] = sha256_hex(body)
        previous = record["hash"]
    p.write_text("".join(canonical_json(r) + "\n" for r in records))
    assert not verify_ledger(p, a["head"], a["count"])[0]
    # A file owner can replace the local anchor too: this is not tamper-proof.
    assert verify_ledger(p, previous, a["count"])[0]


def test_event_chain_rolls_back_with_event(world):
    from agent_rehearsal.store import HARNESS

    before = world.store.rows("SELECT * FROM evidence_chain")
    with pytest.raises(RuntimeError), world.store.transaction() as tx:
        tx.add_event(HARNESS, "test.rollback", {})
        raise RuntimeError("rollback")
    assert world.store.rows("SELECT * FROM evidence_chain") == before
    assert not world.store.rows("SELECT * FROM events WHERE event_type = 'test.rollback'")
