import json
import sqlite3

import pytest

from agent_rehearsal.cli import main
from agent_rehearsal.report import canonical_semantic_trace
from agent_rehearsal.runner import execute_run
from agent_rehearsal.scenarios import load_scenario
from test_cli import cli

POINTS = (
    "before-reservation",
    "after-reservation",
    "after-approval",
    "before-purchase",
    "after-purchase-commit",
    "after-purchase-response",
)


def crash(output, scenario, point):
    result = cli(
        "run", "--scenario", scenario, "--seed", "42", "--output", str(output), "--crash-at", point
    )
    assert result.returncode == 86, result.stdout + result.stderr
    assert not (output / "report.json").exists()
    with sqlite3.connect(output / "run.sqlite") as conn:
        assert not conn.execute("SELECT 1 FROM events WHERE event_type='run.completed'").fetchone()


def check_resumed(output, scenario):
    result = cli("resume", "--run-dir", str(output))
    assert result.returncode == 0, result.stdout + result.stderr
    for command in ("verify", "evaluate", "replay"):
        assert main([command, "--run-dir", str(output)]) == 0
    report = json.loads((output / "report.json").read_text())
    assert report["business_outcome"] == load_scenario(scenario).expected.business_outcome
    checks = {a["id"]: a["status"] for a in report["assertions"]}
    for name in ("one_po_per_intent", "budget_conserved", "reservation_state_consistent"):
        assert checks[name] == "pass"
    with sqlite3.connect(output / "run.sqlite") as conn:
        assert conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0] <= 1
    return report


@pytest.mark.parametrize("point", POINTS)
def test_real_process_crash_resume_and_trace_determinism(tmp_path, point):
    baseline = execute_run(load_scenario("happy-path"), 42, tmp_path / "baseline")
    traces = []
    for name in ("a", "b"):
        output = tmp_path / name
        crash(output, "happy-path", point)
        report = check_resumed(output, "happy-path")
        assert report["business_outcome"] == baseline.report["business_outcome"]
        traces.append(canonical_semantic_trace(report))
    assert traces[0] == traces[1]


@pytest.mark.parametrize(
    "scenario", ["response-lost", "approval-revoked", "supplier-changed", "duplicate-po"]
)
@pytest.mark.parametrize("point", ["after-reservation", "after-approval"])
def test_existing_disruptions_survive_process_restart(tmp_path, scenario, point):
    crash(tmp_path / "run", scenario, point)
    check_resumed(tmp_path / "run", scenario)


def test_ambiguous_purchase_reconciles_before_any_new_mutation(tmp_path):
    output = tmp_path / "run"
    crash(output, "happy-path", "after-purchase-commit")
    with sqlite3.connect(output / "run.sqlite") as conn:
        cursor = conn.execute("SELECT MAX(seq) FROM tool_calls").fetchone()[0]
    check_resumed(output, "happy-path")
    with sqlite3.connect(output / "run.sqlite") as conn:
        later = conn.execute(
            "SELECT tool FROM tool_calls WHERE seq > ? ORDER BY seq", (cursor,)
        ).fetchall()
    assert later[0][0] == "get_purchase_order_by_intent"
    assert not any(r[0] in ("create_purchase_order", "release_reservation") for r in later)


@pytest.mark.parametrize("target", ["ledger", "database"])
def test_resume_refuses_corrupt_evidence(tmp_path, target):
    output = tmp_path / "run"
    crash(output, "happy-path", "after-reservation")
    if target == "ledger":
        p = output / "evidence.jsonl"
        p.write_text(p.read_text().replace("HARNESS", "FORGED"))
    else:
        with sqlite3.connect(output / "run.sqlite") as conn:
            conn.execute(
                "UPDATE events SET actor_id='FORGED' WHERE seq=(SELECT MIN(seq) FROM events)"
            )
    assert main(["resume", "--run-dir", str(output)]) == 2
    assert not (output / "report.json").exists()


def test_resume_completed_run_does_not_repeat_actions(completed_run):
    before = (completed_run.run_dir / "evidence.jsonl").read_bytes()
    assert main(["resume", "--run-dir", str(completed_run.run_dir)]) == 0
    assert before == (completed_run.run_dir / "evidence.jsonl").read_bytes()


def test_resume_missing_run_is_invalid_and_does_not_create_it(tmp_path):
    output = tmp_path / "absent"
    assert main(["resume", "--run-dir", str(output)]) == 2
    assert not output.exists()
