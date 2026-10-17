import pytest

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


def test_approval_revoked_refuses_and_releases(tmp_path):
    result = execute_run(load_scenario("approval-revoked"), 42, tmp_path / "revoked")
    assert result.evaluation.verdict == "pass"
    assert result.report["business_outcome"] == "refused"
    check = next(a for a in result.evaluation.assertions if a.id == "expected_business_outcome")
    revocation = next(e for e in result.report["events"] if e["event_type"] == "approval.revoked")
    assert revocation["event_id"] in check.evidence_event_ids
    assert any(e["event_type"] == "reservation.released" for e in result.report["events"])


def test_missing_revocation_is_detected(tmp_path, monkeypatch):
    from agent_rehearsal.privileged import ReviewerConsole

    monkeypatch.setattr(ReviewerConsole, "revoke_approval", lambda *args: "missing-event")
    result = execute_run(load_scenario("approval-revoked"), 42, tmp_path / "missing")
    assert result.evaluation.verdict == "fail"


@pytest.mark.parametrize(
    "scenario_id", ["duplicate-po", "response-lost", "approval-revoked", "supplier-changed"]
)
def test_disruption_reruns_are_deterministic(tmp_path, scenario_id):
    from agent_rehearsal.report import canonical_semantic_trace

    scenario = load_scenario(scenario_id)
    a = execute_run(scenario, 42, tmp_path / "a").report
    b = execute_run(scenario, 42, tmp_path / "b").report
    assert canonical_semantic_trace(a) == canonical_semantic_trace(b)


@pytest.mark.parametrize(
    "bad_tool", ["release_reservation", "create_purchase_order", "wrong_intent"]
)
def test_reconciliation_rejects_unsafe_next_action(tmp_path, bad_tool):
    import json
    import sqlite3

    from agent_rehearsal.evaluator import evaluate_run

    result = execute_run(load_scenario("response-lost"), 42, tmp_path / "lost")
    with sqlite3.connect(result.run_dir / "run.sqlite") as conn:
        if bad_tool == "wrong_intent":
            conn.execute(
                "UPDATE tool_calls SET arguments_json = ? "
                "WHERE tool = 'get_purchase_order_by_intent'",
                (json.dumps({"logical_intent_id": "PI-OTHER"}),),
            )
        else:
            conn.execute(
                "UPDATE tool_calls SET tool = ? WHERE tool = 'get_purchase_order_by_intent'",
                (bad_tool,),
            )
    check = next(
        a
        for a in evaluate_run(result.run_dir / "run.sqlite").assertions
        if a.id == "unknown_outcome_reconciled"
    )
    assert check.status == "fail"
