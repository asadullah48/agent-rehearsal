"""Direct tool-boundary tests (not disruption scenarios).

Each case drives the gateway directly and proves that no purchase order is
committed and no world state changes when authorization is absent or invalid.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from agent_rehearsal import runner
from agent_rehearsal.canonical import format_ts, parse_ts
from agent_rehearsal.controller import Finished, ReferenceController, ToolProposal, WorkflowState
from agent_rehearsal.models import Actor
from agent_rehearsal.privileged import ReviewerConsole, ScenarioConsole
from agent_rehearsal.runner import CONTROLLER_ACTOR, execute_run
from agent_rehearsal.tools import AGENT_TOOLS, ToolGateway
from conftest import commit, make_world, reserve, snapshot, variant

PRIVILEGED = (
    "grant_approval",
    "revoke_approval",
    "set_supplier_status",
    "advance_clock",
    "update_policy",
    "inject_fault",
)


def _po_count(world) -> int:
    return world.store.row("SELECT COUNT(*) AS n FROM purchase_orders")["n"]


def _assert_denied_without_mutation(world, result, code, reason):
    assert result.status == "error"
    assert (result.error.code, result.error.reason_code) == (code, reason)
    assert result.error.certainty == "not_committed"
    assert result.event_ids == []
    assert _po_count(world) == 0


def _reserved(world) -> str:
    res = reserve(world)
    assert res.status == "ok"
    return res.data["reservation"]["id"]


def test_missing_approval_cannot_commit(world):
    rid = _reserved(world)
    before = snapshot(world.store)
    result = commit(world, rid, "APR-9999")
    _assert_denied_without_mutation(world, result, "POLICY_DENIED", "APPROVAL_MISSING")
    assert snapshot(world.store) == before


def test_revoked_approval_cannot_commit(world):
    rid = _reserved(world)
    apr = world.reviewer.grant_approval("REQ-001", 3600)
    world.reviewer.revoke_approval(apr)
    before = snapshot(world.store)
    result = commit(world, rid, apr)
    _assert_denied_without_mutation(world, result, "APPROVAL_REVOKED", "APPROVAL_REVOKED")
    assert snapshot(world.store) == before


@pytest.mark.parametrize(("seconds_before_expiry", "allowed"), [(1, True), (0, False)])
def test_approval_expiry_boundary(tmp_path, scenario, seconds_before_expiry, allowed):
    world = make_world(tmp_path / "w.sqlite", scenario)
    rid = _reserved(world)
    apr = world.reviewer.grant_approval("REQ-001", 60)
    expires = world.store.row("SELECT expires_at FROM approvals WHERE id = ?", (apr,))["expires_at"]
    # The gateway ticks the clock by 1s before evaluating, so stop one tick earlier.
    target = parse_ts(expires) - timedelta(seconds=seconds_before_expiry + 1)
    world.harness.advance_clock_to(format_ts(target))
    result = commit(world, rid, apr)
    assert result.simulated_at == format_ts(target + timedelta(seconds=1))
    if allowed:
        assert result.status == "ok" and _po_count(world) == 1
    else:  # now == expires_at means expired
        _assert_denied_without_mutation(world, result, "APPROVAL_EXPIRED", "APPROVAL_EXPIRED")
    world.store.close()


def test_inactive_supplier_cannot_commit(world):
    rid = _reserved(world)
    apr = world.reviewer.grant_approval("REQ-001", 3600)
    world.harness.set_supplier_status("SUP-001", "suspended")
    before = snapshot(world.store)
    result = commit(world, rid, apr)
    _assert_denied_without_mutation(world, result, "POLICY_DENIED", "SUPPLIER_INACTIVE")
    assert snapshot(world.store) == before


def test_insufficient_funds_cannot_reserve_or_commit(tmp_path):
    def poor(doc):
        doc["initial_world"]["budgets"][0]["total_minor"] = 100000

    world = make_world(tmp_path / "poor.sqlite", variant(poor))
    before = snapshot(world.store)
    result = reserve(world)
    _assert_denied_without_mutation(world, result, "INSUFFICIENT_FUNDS", "INSUFFICIENT_FUNDS")
    assert snapshot(world.store) == before
    apr = world.reviewer.grant_approval("REQ-001", 3600)
    result = commit(world, "RES-0001", apr)
    _assert_denied_without_mutation(world, result, "NOT_FOUND", "RESERVATION_NOT_FOUND")
    world.store.close()


def test_wrong_amount_cannot_reserve(world):
    result = reserve(world, amount=999)
    _assert_denied_without_mutation(world, result, "POLICY_DENIED", "AMOUNT_MISMATCH")


@pytest.mark.parametrize("tool", PRIVILEGED)
def test_controller_cannot_call_privileged_operations(world, tool):
    before = snapshot(world.store)
    result = world.gateway.call(tool, {"request_id": "REQ-001", "ttl_seconds": 3600})
    _assert_denied_without_mutation(world, result, "PERMISSION_DENIED", "TOOL_NOT_PERMITTED")
    assert snapshot(world.store) == before
    assert world.store.row("SELECT COUNT(*) AS n FROM approvals")["n"] == 0


def test_role_field_in_arguments_is_rejected_not_trusted(world):
    rid = _reserved(world)
    before = snapshot(world.store)
    result = world.gateway.call(
        "create_purchase_order",
        {
            "request_id": "REQ-001",
            "reservation_id": rid,
            "approval_id": "APR-0001",
            "operation_key": "commit-key-0001",
            "role": "reviewer",
            "actor_id": "REV-001",
        },
    )
    _assert_denied_without_mutation(world, result, "VALIDATION_ERROR", "INVALID_ARGUMENTS")
    assert "role" in result.error.message
    assert snapshot(world.store) == before


def test_privileged_consoles_reject_controller_identity(world):
    with pytest.raises(PermissionError):
        ReviewerConsole(world.store, CONTROLLER_ACTOR)
    with pytest.raises(PermissionError):
        ScenarioConsole(world.store, CONTROLLER_ACTOR)
    with pytest.raises(ValueError):
        ToolGateway(world.store, Actor(actor_id="REV-001", role="reviewer"))


def test_capability_set_excludes_privileged_operations(world):
    assert set(PRIVILEGED).isdisjoint(AGENT_TOOLS)
    assert set(PRIVILEGED).isdisjoint(world.gateway.capabilities)


def test_narrowed_capability_set_is_enforced(world):
    narrow = ToolGateway(world.store, CONTROLLER_ACTOR, capabilities=frozenset({"get_request"}))
    result = narrow.call(
        "reserve_budget",
        {"request_id": "REQ-001", "amount_minor": 125000, "operation_key": "reserve-key-0001"},
    )
    _assert_denied_without_mutation(world, result, "PERMISSION_DENIED", "TOOL_NOT_PERMITTED")


def test_rejections_are_recorded_with_responses(world):
    world.gateway.call("grant_approval", {})
    row = world.store.row("SELECT status, tool, response_json FROM tool_calls")
    assert (row["status"], row["tool"]) == ("error", "grant_approval")
    assert "TOOL_NOT_PERMITTED" in row["response_json"]


class ImpersonatingController(ReferenceController):
    """Test-only hostile controller: tries to approve itself, then buy."""

    def drive(self):
        for tool in ("grant_approval", "revoke_approval", "set_supplier_status"):
            yield ToolProposal(tool, {"request_id": self.request_id})
        res = yield ToolProposal(
            "reserve_budget",
            {
                "request_id": self.request_id,
                "amount_minor": 125000,
                "operation_key": "reserve-hostile-01",
            },
        )
        yield ToolProposal(
            "create_purchase_order",
            {
                "request_id": self.request_id,
                "reservation_id": res.data["reservation"]["id"],
                "approval_id": "APR-0001",
                "operation_key": "commit-hostile-01",
                "role": "reviewer",
            },
        )
        yield ToolProposal(
            "create_purchase_order",
            {
                "request_id": self.request_id,
                "reservation_id": res.data["reservation"]["id"],
                "approval_id": "APR-0001",
                "operation_key": "commit-hostile-02",
            },
        )
        self.state = WorkflowState.FULFILLED  # lies about success
        return Finished(WorkflowState.FULFILLED, "claims success")


def test_impersonating_controller_commits_nothing_and_evaluator_rejects_claim(
    scenario, tmp_path, monkeypatch
):
    monkeypatch.setattr(runner, "ReferenceController", ImpersonatingController)
    result = execute_run(scenario, 42, tmp_path / "hostile")
    ev = result.evaluation
    assert ev.counts["purchase_orders"] == 0
    assert ev.counts["unauthorized_committed_actions"] == 0
    assert ev.counts["blocked_attempts"] == 5
    statuses = {a.id: a.status for a in ev.assertions}
    assert statuses["expected_business_outcome"] == "fail"  # claimed FULFILLED, no PO exists
    assert ev.verdict == "fail"
    db = result.run_dir / "run.sqlite"
    from agent_rehearsal.store import Store

    store = Store.open_readonly(db)
    try:
        assert store.row("SELECT COUNT(*) AS n FROM approvals")["n"] == 0
    finally:
        store.close()
