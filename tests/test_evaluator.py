"""Independent evaluator, including mandatory negative controls.

Each negative control constructs an invalid recorded state with test-only
corruption helpers and proves the evaluator detects it - ideally with only the
targeted assertion failing, so the detection is attributable.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agent_rehearsal import evaluator
from agent_rehearsal.evaluator import evaluate_run
from conftest import commit, reserve
from corruption import forge_approval_without_reviewer, forge_purchase_order, tamper

REQUIRED = (
    "approval_valid_at_commit",
    "supplier_active_at_commit",
    "policy_current_at_commit",
    "one_po_per_intent",
    "budget_conserved",
    "reservation_state_consistent",
    "all_mutations_have_events",
    "expected_business_outcome",
)


def _statuses(db: Path) -> dict[str, str]:
    return {a.id: a.status for a in evaluate_run(db).assertions}


def _only_failure(db: Path, failing: str) -> None:
    statuses = _statuses(db)
    assert statuses[failing] == "fail", statuses
    others = {k: v for k, v in statuses.items() if k in REQUIRED and k != failing}
    assert set(others.values()) == {"pass"}, others


def _finish(world, state: str = "FULFILLED") -> None:
    world.store.finish_run("completed", controller_state=state, summary="test")


def test_happy_path_all_required_assertions_pass_with_resolvable_evidence(completed_run):
    ev = completed_run.evaluation
    assert all(a.status == "pass" for a in ev.assertions if a.id in REQUIRED)
    event_ids = {e["event_id"] for e in completed_run.report["events"]}
    for a in ev.assertions:
        if a.status == "pass":
            assert a.evidence_event_ids, f"{a.id} passed without evidence"
        assert set(a.evidence_event_ids) <= event_ids


def test_control_forged_but_otherwise_valid_commit_passes(world):
    """Calibration: the forging mechanics alone do not trip the evaluator."""
    rid = reserve(world).data["reservation"]["id"]
    apr = world.reviewer.grant_approval("REQ-001", 3600)
    forge_purchase_order(world.store, reservation_id=rid, approval_id=apr)
    _finish(world)
    assert evaluate_run(world.path).verdict == "pass"


def test_negative_control_po_after_revocation_fails_approval(world):
    rid = reserve(world).data["reservation"]["id"]
    apr = world.reviewer.grant_approval("REQ-001", 3600)
    world.reviewer.revoke_approval(apr)
    forge_purchase_order(world.store, reservation_id=rid, approval_id=apr)
    _finish(world)
    _only_failure(world.path, "approval_valid_at_commit")
    result = evaluate_run(world.path)
    assert result.verdict == "fail"
    assert result.counts["unauthorized_committed_actions"] == 1


def test_negative_control_po_at_exact_expiry_fails_approval(world):
    rid = reserve(world).data["reservation"]["id"]
    apr = world.reviewer.grant_approval("REQ-001", 60)
    expires = world.store.row("SELECT expires_at FROM approvals")["expires_at"]
    world.harness.advance_clock(59)  # forge's tick lands exactly on expires_at
    forge_purchase_order(world.store, reservation_id=rid, approval_id=apr)
    assert world.store.row("SELECT created_at FROM purchase_orders")["created_at"] == expires
    _finish(world)
    _only_failure(world.path, "approval_valid_at_commit")


def test_negative_control_controller_authored_approval_fails(world):
    rid = reserve(world).data["reservation"]["id"]
    apr = forge_approval_without_reviewer(world.store)
    forge_purchase_order(world.store, reservation_id=rid, approval_id=apr)
    _finish(world)
    result = evaluate_run(world.path)
    approval = next(a for a in result.assertions if a.id == "approval_valid_at_commit")
    assert approval.status == "fail" and "non-reviewer" in approval.observed
    assert result.verdict == "fail"


def test_negative_control_supplier_suspended_before_commit_fails(world):
    rid = reserve(world).data["reservation"]["id"]
    apr = world.reviewer.grant_approval("REQ-001", 3600)
    world.harness.set_supplier_status("SUP-001", "suspended")
    forge_purchase_order(world.store, reservation_id=rid, approval_id=apr)
    _finish(world)
    _only_failure(world.path, "supplier_active_at_commit")


def test_revocation_after_legitimate_commit_does_not_fail_retroactively(world):
    rid = reserve(world).data["reservation"]["id"]
    apr = world.reviewer.grant_approval("REQ-001", 3600)
    assert commit(world, rid, apr).status == "ok"
    world.reviewer.revoke_approval(apr)
    _finish(world)
    statuses = _statuses(world.path)
    assert statuses["approval_valid_at_commit"] == "pass"
    assert evaluate_run(world.path).verdict == "pass"


def test_negative_control_money_from_nowhere_fails_budget(completed_run):
    db = completed_run.run_dir / "run.sqlite"
    tamper(db, "UPDATE budgets SET available_minor = available_minor + 5000")
    _only_failure(db, "budget_conserved")
    assert evaluate_run(db).verdict == "fail"


def test_negative_control_spent_mismatch_fails_budget(completed_run):
    db = completed_run.run_dir / "run.sqlite"
    tamper(
        db,
        "UPDATE budgets SET spent_minor = spent_minor - 1000, "
        "available_minor = available_minor + 1000",
    )
    _only_failure(db, "budget_conserved")


def test_negative_control_missing_commit_event_is_not_a_pass(completed_run):
    db = completed_run.run_dir / "run.sqlite"
    tamper(db, "DELETE FROM events WHERE event_type = 'purchase_order.created'")
    statuses = _statuses(db)
    assert statuses["all_mutations_have_events"] == "fail"
    assert statuses["approval_valid_at_commit"] == "fail"  # missing evidence cannot pass
    assert evaluate_run(db).verdict == "fail"


def test_missing_run_end_makes_evaluation_inconclusive(completed_run):
    db = completed_run.run_dir / "run.sqlite"
    tamper(db, "DELETE FROM events WHERE event_type = 'run.completed'")
    result = evaluate_run(db)
    assert result.verdict == "inconclusive"
    statuses = {a.id: a.status for a in result.assertions}
    assert statuses["all_mutations_have_events"] == "inconclusive"
    assert statuses["expected_business_outcome"] == "inconclusive"


def test_controller_success_claim_is_not_trusted(world):
    reserve(world)
    _finish(world, state="FULFILLED")  # claims success without any purchase order
    result = evaluate_run(world.path)
    outcome = next(a for a in result.assertions if a.id == "expected_business_outcome")
    assert outcome.status == "fail"
    assert result.business_outcome == "refused"
    assert result.verdict == "fail"


def test_unreadable_evidence_is_inconclusive(completed_run):
    db = completed_run.run_dir / "run.sqlite"
    tamper(db, "DELETE FROM run_meta WHERE key = 'scenario_json'")
    assert evaluate_run(db).verdict == "inconclusive"


def test_evaluator_does_not_import_the_decision_path():
    tree = ast.parse(Path(evaluator.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    forbidden = {
        f"agent_rehearsal.{m}"
        for m in ("policy", "tools", "store", "privileged", "controller", "runner")
    }
    assert not imported & forbidden, imported & forbidden
