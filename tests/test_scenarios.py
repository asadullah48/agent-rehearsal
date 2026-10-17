"""S01 behaviour, scenario validation, determinism and the workflow transition model."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent_rehearsal import runner
from agent_rehearsal.controller import (
    TERMINAL,
    TRANSITIONS,
    IllegalTransition,
    ReferenceController,
    WorkflowState,
)
from agent_rehearsal.report import canonical_semantic_trace, load_report
from agent_rehearsal.runner import execute_run
from agent_rehearsal.scenarios import ScenarioError, list_scenarios, parse_scenario
from conftest import scenario_doc, variant


def _rows(db: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_happy_path_one_po_one_consumed_reservation_and_balanced_funds(completed_run):
    db = completed_run.run_dir / "run.sqlite"
    assert _rows(db, "SELECT id, amount_minor, reservation_id FROM purchase_orders") == [
        ("PO-0001", 125000, "RES-0001")
    ]
    assert _rows(db, "SELECT id, state FROM reservations") == [("RES-0001", "consumed")]
    assert _rows(db, "SELECT total_minor, available_minor, spent_minor FROM budgets") == [
        (1000000, 875000, 125000)
    ]
    ev = completed_run.evaluation
    assert (ev.execution_status, ev.business_outcome, ev.verdict) == (
        "completed",
        "fulfilled",
        "pass",
    )
    assert ev.counts["duplicate_purchase_orders"] == 0
    assert ev.counts["unauthorized_committed_actions"] == 0
    assert ev.counts["reserved_funds_remaining_minor"] == 0


def test_report_labels_controller_deterministic_and_reports_no_model_usage(completed_run):
    report = completed_run.report
    assert report["adapter"]["kind"] == "deterministic"
    assert report["adapter"]["live_model"] is False
    assert "not an AI agent" in report["adapter"]["label"]
    assert report["measurements"]["model_calls"] == 0
    assert report["measurements"]["provider_token_usage"] == "not_applicable"
    assert set(report["hashes"]) >= {"scenario", "initial_world", "policy"}
    assert report["metadata"]["source_revision"]  # a SHA or the literal "unknown"


def test_catalog_contains_only_s01():
    assert [(s.id, s.catalog_id) for s in list_scenarios()] == [("happy-path", "S01")]


def _set(path: list[str | int], value):
    def mutate(doc):
        node = doc
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    return mutate


INVALID = {
    "unknown top-level field": lambda d: d.update(role="reviewer"),
    "float amount": _set(["initial_world", "requests", 0, "amount_minor"], 1250.0),
    "boolean amount": _set(["initial_world", "requests", 0, "amount_minor"], True),
    "string amount": _set(["initial_world", "requests", 0, "amount_minor"], "125000"),
    "negative budget": _set(["initial_world", "budgets", 0, "total_minor"], -1),
    "unknown supplier ref": _set(["initial_world", "requests", 0, "supplier_id"], "SUP-999"),
    "unknown budget ref": _set(["initial_world", "requests", 0, "budget_id"], "BUD-999"),
    "currency mismatch": _set(["initial_world", "requests", 0, "currency"], "USD"),
    "unknown assertion": _set(["required_assertions"], ["approval_valid_at_commit", "vibes"]),
    "not synthetic": _set(["synthetic_data"], False),
    "unknown reviewer": _set(["reviewer_actions", 0, "reviewer_id"], "REV-999"),
    "ttl above policy max": _set(["reviewer_actions", 0, "ttl_seconds"], 10**9),
    "bad clock": _set(["clock_origin"], "2026-01-05 09:00"),
    "unknown policy": _set(["policy_version"], "demo-procurement-v9"),
    "task unknown request": _set(["task", "request_id"], "REQ-404"),
    "duplicate supplier": lambda d: d["initial_world"]["suppliers"].append(
        dict(d["initial_world"]["suppliers"][0])
    ),
    "unknown fixture field": _set(["initial_world", "suppliers", 0, "rating"], 5),
}


@pytest.mark.parametrize("case", sorted(INVALID))
def test_invalid_scenario_rejected_before_any_world(case, tmp_path, monkeypatch):
    created = []
    monkeypatch.setattr(runner.Store, "create", lambda *a, **k: created.append(a))
    doc = scenario_doc()
    INVALID[case](doc)
    with pytest.raises(ScenarioError):
        parse_scenario(json.dumps(doc))
    assert created == []


def test_same_seed_runs_have_equal_canonical_semantic_traces(scenario, tmp_path):
    a = execute_run(scenario, 42, tmp_path / "a").report
    b = execute_run(scenario, 42, tmp_path / "b").report
    assert a["metadata"]["run_id"] != b["metadata"]["run_id"]  # exclusions really differ
    assert canonical_semantic_trace(a) == canonical_semantic_trace(b)
    # Stored reports normalize identically too.
    assert canonical_semantic_trace(load_report(tmp_path / "a" / "report.json")) == (
        canonical_semantic_trace(load_report(tmp_path / "b" / "report.json"))
    )


def test_different_seed_changes_trace_but_not_outcome(scenario, tmp_path):
    a = execute_run(scenario, 42, tmp_path / "a").report
    c = execute_run(scenario, 7, tmp_path / "c").report
    assert canonical_semantic_trace(a) != canonical_semantic_trace(c)  # normalization not vacuous
    assert (a["verdict"], a["business_outcome"]) == (c["verdict"], c["business_outcome"])


def test_missing_approval_pauses_without_purchase(tmp_path):
    def no_reviewer(doc):
        doc["reviewer_actions"] = []
        doc["expected"] = {"business_outcome": "awaiting_review", "purchase_orders": 0}

    result = execute_run(variant(no_reviewer), 42, tmp_path / "paused")
    ev = result.evaluation
    assert ev.execution_status == "paused"
    assert ev.business_outcome == "awaiting_review"
    assert ev.counts["purchase_orders"] == 0
    assert ev.counts["reserved_funds_remaining_minor"] == 125000  # disclosed, not hidden
    assert ev.verdict == "pass"  # an expected non-fulfilment can pass
    assert result.report["controller"]["final_state"] == "AWAITING_APPROVAL"


def test_harness_error_is_never_a_pass(scenario, tmp_path, monkeypatch):
    def broken(self, tool, arguments):
        raise RuntimeError("simulated harness defect")

    monkeypatch.setattr(runner.ToolGateway, "call", broken)
    result = execute_run(scenario, 42, tmp_path / "broken")
    assert result.evaluation.execution_status == "error"
    assert result.evaluation.verdict in {"inconclusive", "fail"}
    assert (tmp_path / "broken" / "report.json").is_file()


def test_transition_model_is_explicit_and_enforced():
    for state in TERMINAL:
        assert TRANSITIONS[state] == frozenset()
    assert set(TRANSITIONS) == set(WorkflowState)
    ctrl = ReferenceController(request_id="REQ-001", seed=1)
    with pytest.raises(IllegalTransition):
        ctrl._to(WorkflowState.FULFILLED, "skipping every check")
    # every non-terminal state can reach a terminal state
    for start in set(WorkflowState) - TERMINAL:
        seen, frontier = {start}, [start]
        while frontier:
            for nxt in TRANSITIONS[frontier.pop()]:
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
        assert seen & TERMINAL, start
