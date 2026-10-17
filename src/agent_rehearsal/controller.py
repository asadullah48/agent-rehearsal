"""Deterministic reference controller.

This is NOT an AI agent and makes no model calls. It is a fixed, seeded state
machine that proposes typed tool calls. It holds no reference to the store,
gateway or reviewer console: the runner executes each proposal through the
gateway and sends back the recorded result.
"""

from __future__ import annotations

import random
from collections.abc import Generator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agent_rehearsal.models import ToolResult

ADAPTER = {
    "id": "reference-deterministic",
    "version": "0.1.0",
    "kind": "deterministic",
    "live_model": False,
    "label": "Deterministic reference controller (not an AI agent; no model calls)",
}


class WorkflowState(StrEnum):
    NEW = "NEW"
    VALIDATED = "VALIDATED"
    RESERVED = "RESERVED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    READY_TO_COMMIT = "READY_TO_COMMIT"
    RECONCILING = "RECONCILING"
    FULFILLED = "FULFILLED"
    REFUSED = "REFUSED"
    ESCALATED = "ESCALATED"


S = WorkflowState
TERMINAL = frozenset({S.FULFILLED, S.REFUSED, S.ESCALATED})
TRANSITIONS: dict[WorkflowState, frozenset[WorkflowState]] = {
    S.NEW: frozenset({S.VALIDATED, S.REFUSED, S.ESCALATED}),
    S.VALIDATED: frozenset({S.RESERVED, S.REFUSED, S.ESCALATED}),
    S.RESERVED: frozenset({S.AWAITING_APPROVAL, S.READY_TO_COMMIT, S.REFUSED, S.ESCALATED}),
    S.AWAITING_APPROVAL: frozenset({S.READY_TO_COMMIT, S.REFUSED, S.ESCALATED}),
    S.READY_TO_COMMIT: frozenset({S.FULFILLED, S.RECONCILING, S.REFUSED, S.ESCALATED}),
    S.RECONCILING: frozenset({S.FULFILLED, S.READY_TO_COMMIT, S.ESCALATED}),
    S.FULFILLED: frozenset(),
    S.REFUSED: frozenset(),
    S.ESCALATED: frozenset(),
}


class IllegalTransition(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolProposal:
    tool: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class AwaitReview:
    request_id: str


@dataclass(frozen=True)
class Finished:
    state: WorkflowState
    summary: str


@dataclass
class Transition:
    from_state: WorkflowState
    to_state: WorkflowState
    reason: str


Decision = ToolProposal | AwaitReview
Drive = Generator[Decision, ToolResult | None, Finished]


@dataclass
class ReferenceController:
    request_id: str
    seed: int
    max_attempts_per_operation: int = 3
    state: WorkflowState = WorkflowState.NEW
    transitions: list[Transition] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    # --- helpers -------------------------------------------------------------------

    def _to(self, new: WorkflowState, reason: str) -> None:
        if new not in TRANSITIONS[self.state]:
            raise IllegalTransition(f"{self.state} -> {new} is not allowed")
        self.transitions.append(Transition(self.state, new, reason))
        self.state = new

    def _end(self, new: WorkflowState, summary: str) -> Finished:
        self._to(new, summary)
        return Finished(new, summary)

    def _key(self, operation: str) -> str:
        # Seeded, so identical seeds yield identical keys; retries reuse the same key.
        return f"{operation}-{self._rng.getrandbits(64):016x}"

    @staticmethod
    def _reason(result: ToolResult) -> str:
        return result.error.reason_code if result.error else "UNKNOWN"

    # --- workflow ------------------------------------------------------------------

    def drive(self) -> Drive:
        rid = self.request_id
        got = yield ToolProposal("get_request", {"request_id": rid})
        assert got is not None
        if got.status != "ok" or got.data is None:
            return self._end(S.REFUSED, f"request lookup failed: {self._reason(got)}")
        request = got.data["request"]

        sup = yield ToolProposal("get_supplier", {"supplier_id": request["supplier_id"]})
        assert sup is not None
        if sup.status != "ok" or sup.data is None or sup.data["supplier"]["status"] != "active":
            return self._end(S.REFUSED, "SUPPLIER_INACTIVE: supplier not active at validation")

        bud = yield ToolProposal("get_budget", {"budget_id": request["budget_id"]})
        assert bud is not None
        if bud.status != "ok" or bud.data is None:
            return self._end(S.REFUSED, f"budget lookup failed: {self._reason(bud)}")
        if bud.data["budget"]["available_minor"] < request["amount_minor"]:
            return self._end(S.REFUSED, "INSUFFICIENT_FUNDS: observed before reserving")
        self._to(S.VALIDATED, "request, supplier and budget checked")

        reserved = yield ToolProposal(
            "reserve_budget",
            {
                "request_id": rid,
                "amount_minor": request["amount_minor"],
                "operation_key": self._key("reserve"),
            },
        )
        assert reserved is not None
        if reserved.status != "ok" or reserved.data is None:
            return self._end(S.REFUSED, f"reservation refused: {self._reason(reserved)}")
        reservation_id = reserved.data["reservation"]["id"]
        self._to(S.RESERVED, f"reserved {reservation_id}")

        approval = yield from self._obtain_approval(request)
        if approval is None:
            return (yield from self._release_and_refuse(reservation_id, "approval not usable"))
        self._to(S.READY_TO_COMMIT, f"approval {approval['id']} active and bound to proposal")

        recheck = yield ToolProposal("get_supplier", {"supplier_id": request["supplier_id"]})
        assert recheck is not None
        if (
            recheck.status != "ok"
            or recheck.data is None
            or (recheck.data["supplier"]["status"] != "active")
        ):
            return (yield from self._release_and_refuse(reservation_id, "SUPPLIER_INACTIVE"))

        commit_key = self._key("commit")
        committed = yield ToolProposal(
            "create_purchase_order",
            {
                "request_id": rid,
                "reservation_id": reservation_id,
                "approval_id": approval["id"],
                "operation_key": commit_key,
            },
        )
        assert committed is not None
        if committed.status == "ok":
            return (yield from self._verify(request["logical_intent_id"]))
        assert committed.error is not None
        if committed.error.certainty != "not_committed":
            # Outcome unknown: reconcile by authoritative intent lookup before anything else.
            self._to(S.RECONCILING, f"commit outcome {committed.error.certainty}")
            return (yield from self._verify(request["logical_intent_id"], reconciling=True))
        return (yield from self._release_and_refuse(reservation_id, self._reason(committed)))

    def _obtain_approval(
        self, request: dict[str, Any]
    ) -> Generator[Decision, ToolResult | None, dict[str, Any] | None]:
        for _ in range(self.max_attempts_per_operation):
            seen = yield ToolProposal("get_approval", {"request_id": request["id"]})
            assert seen is not None
            if seen.status == "ok" and seen.data is not None:
                appr = seen.data["approval"]
                matches = (
                    appr["request_id"] == request["id"]
                    and appr["amount_minor"] == request["amount_minor"]
                    and appr["supplier_id"] == request["supplier_id"]
                    and appr["proposal_hash"] == request["proposal_hash"]
                )
                if seen.data["status"] == "active" and matches:
                    return appr
                return None
            if seen.error is not None and seen.error.reason_code == "APPROVAL_NOT_FOUND":
                if self.state is not S.AWAITING_APPROVAL:
                    self._to(S.AWAITING_APPROVAL, "no reviewer approval yet; pausing")
                yield AwaitReview(request["id"])
                continue
            return None
        return None

    def _verify(self, intent: str, *, reconciling: bool = False) -> Drive:
        found = yield ToolProposal("get_purchase_order_by_intent", {"logical_intent_id": intent})
        assert found is not None
        if found.status == "ok":
            how = "reconciled by intent lookup" if reconciling else "verified by intent lookup"
            return self._end(S.FULFILLED, f"purchase order {how}")
        esc = yield ToolProposal(
            "escalate",
            {
                "request_id": self.request_id,
                "reason_code": "UNRESOLVED_COMMIT_OUTCOME",
                "evidence_event_ids": [],
            },
        )
        assert esc is not None
        return self._end(S.ESCALATED, "commit outcome could not be confirmed")

    def _release_and_refuse(self, reservation_id: str, why: str) -> Drive:
        released = yield ToolProposal(
            "release_reservation",
            {"reservation_id": reservation_id, "operation_key": self._key("release")},
        )
        assert released is not None
        if released.status == "ok":
            return self._end(S.REFUSED, f"{why}; reservation released")
        esc = yield ToolProposal(
            "escalate",
            {
                "request_id": self.request_id,
                "reason_code": "RELEASE_FAILED",
                "evidence_event_ids": [],
            },
        )
        assert esc is not None
        return self._end(S.ESCALATED, f"{why}; release failed, reservation left active")
