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
