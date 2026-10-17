from agent_rehearsal.runner import execute_run
from agent_rehearsal.scenarios import load_scenario


def test_single_po_control(tmp_path):
    result = execute_run(load_scenario("happy-path"), 42, tmp_path / "single")
    check = next(a for a in result.evaluation.assertions if a.id == "one_po_per_intent")
    assert check.status == "pass"
    assert "0 duplicate attempt(s) blocked" in check.observed


def test_duplicate_po_is_blocked_and_reported(tmp_path):
    result = execute_run(load_scenario("duplicate-po"), 42, tmp_path / "duplicate")
    check = next(a for a in result.evaluation.assertions if a.id == "one_po_per_intent")
    assert result.evaluation.verdict == "pass"
    assert "1 duplicate attempt(s) blocked" in check.observed


def test_supplier_changed_refuses_and_releases(tmp_path):
    result = execute_run(load_scenario("supplier-changed"), 42, tmp_path / "supplier")
    assert result.evaluation.verdict == "pass"
    assert result.report["business_outcome"] == "refused"
    assert any(e["event_type"] == "supplier.status_changed" for e in result.report["events"])
    assert any(e["event_type"] == "reservation.released" for e in result.report["events"])


def test_supplier_fault_missing_is_detected(tmp_path, monkeypatch):
    from agent_rehearsal.privileged import ScenarioConsole

    monkeypatch.setattr(ScenarioConsole, "set_supplier_status", lambda *args: "missing-event")
    result = execute_run(load_scenario("supplier-changed"), 42, tmp_path / "missing")
    assert result.evaluation.verdict == "fail"
    assert (
        next(a for a in result.evaluation.assertions if a.id == "expected_business_outcome").status
        == "fail"
    )


def test_response_lost_reconciles_once(tmp_path):
    result = execute_run(load_scenario("response-lost"), 42, tmp_path / "lost")
    assert result.evaluation.verdict == "pass"
    check = next(a for a in result.evaluation.assertions if a.id == "unknown_outcome_reconciled")
    assert check.status == "pass"
    assert (
        len([e for e in result.report["events"] if e["event_type"] == "purchase_order.created"])
        == 1
    )
    assert any(t["to_state"] == "RECONCILING" for t in result.report["workflow_transitions"])


def test_response_lost_missing_lookup_fails(tmp_path):
    import sqlite3

    from agent_rehearsal.evaluator import evaluate_run

    result = execute_run(load_scenario("response-lost"), 42, tmp_path / "lost")
    with sqlite3.connect(result.run_dir / "run.sqlite") as conn:
        conn.execute("DELETE FROM tool_calls WHERE tool = 'get_purchase_order_by_intent'")
    check = next(
        a
        for a in evaluate_run(result.run_dir / "run.sqlite").assertions
        if a.id == "unknown_outcome_reconciled"
    )
    assert check.status == "fail"
