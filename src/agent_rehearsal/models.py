"""Strict typed records for the simulated procurement world.

Every model forbids unknown fields and uses strict typing, so a float amount, a
boolean posing as an integer, or an extra ``role`` field is rejected rather than
coerced. Money is always integer minor units (fils for the illustrative AED).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from agent_rehearsal.canonical import TIMESTAMP_PATTERN


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


RecordId = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2,5}-[A-Z0-9-]{1,40}$")]
EventId = Annotated[str, StringConstraints(pattern=r"^EV-\d{6}$")]
OperationKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9:_-]{8,128}$")]
ReasonCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
Timestamp = Annotated[str, StringConstraints(pattern=TIMESTAMP_PATTERN)]
PositiveMinor = Annotated[int, Field(gt=0, le=10**12)]
NonNegativeMinor = Annotated[int, Field(ge=0, le=10**12)]


# --- World records -----------------------------------------------------------------


# Literals rather than enums: strict mode accepts the stored string values directly.
SupplierStatus = Literal["active", "suspended"]
ReservationState = Literal["active", "consumed", "released"]


class Supplier(Strict):
    id: RecordId
    name: str
    status: SupplierStatus
    version: int


class Budget(Strict):
    id: RecordId
    currency: Currency
    total_minor: NonNegativeMinor
    available_minor: NonNegativeMinor
    spent_minor: NonNegativeMinor
    version: int


class PurchaseRequest(Strict):
    id: RecordId
    supplier_id: RecordId
    budget_id: RecordId
    amount_minor: PositiveMinor
    currency: Currency
    description: str
    proposal_hash: str
    logical_intent_id: RecordId


class Reservation(Strict):
    id: RecordId
    request_id: RecordId
    budget_id: RecordId
    amount_minor: PositiveMinor
    state: ReservationState
    version: int
    created_at: Timestamp


class Approval(Strict):
    id: RecordId
    request_id: RecordId
    supplier_id: RecordId
    amount_minor: PositiveMinor
    currency: Currency
    proposal_hash: str
    policy_version: str
    reviewer_id: str
    granted_at: Timestamp
    expires_at: Timestamp
    revoked_at: Timestamp | None


class PurchaseOrder(Strict):
    id: RecordId
    logical_intent_id: RecordId
    request_id: RecordId
    reservation_id: RecordId
    approval_id: RecordId
    supplier_id: RecordId
    amount_minor: PositiveMinor
    currency: Currency
    policy_version: str
    created_at: Timestamp
    operation_key: OperationKey


# --- Agent tool arguments (the only inputs a controller may supply) ---------------


class GetRequestArgs(Strict):
    request_id: RecordId


class GetSupplierArgs(Strict):
    supplier_id: RecordId


class GetBudgetArgs(Strict):
    budget_id: RecordId


class ReserveBudgetArgs(Strict):
    request_id: RecordId
    amount_minor: PositiveMinor
    operation_key: OperationKey


class GetApprovalArgs(Strict):
    request_id: RecordId


class CreatePurchaseOrderArgs(Strict):
    request_id: RecordId
    reservation_id: RecordId
    approval_id: RecordId
    operation_key: OperationKey


class GetPurchaseOrderByIntentArgs(Strict):
    logical_intent_id: RecordId


class ReleaseReservationArgs(Strict):
    reservation_id: RecordId
    operation_key: OperationKey


class EscalateArgs(Strict):
    request_id: RecordId
    reason_code: ReasonCode
    evidence_event_ids: list[EventId] = Field(max_length=50)


# --- Tool envelope -------------------------------------------------------------------

ErrorCode = Literal[
    "VALIDATION_ERROR",
    "PERMISSION_DENIED",
    "POLICY_DENIED",
    "INSUFFICIENT_FUNDS",
    "APPROVAL_EXPIRED",
    "APPROVAL_REVOKED",
    "IDEMPOTENCY_CONFLICT",
    "OUTCOME_UNKNOWN",
    "NOT_FOUND",
    "STEP_LIMIT",
]
Certainty = Literal["not_committed", "committed", "unknown"]


class ToolError(Strict):
    code: ErrorCode
    reason_code: ReasonCode
    message: str
    certainty: Certainty


class ToolResult(Strict):
    call_id: str
    tool: str
    status: Literal["ok", "error"]
    data: dict[str, Any] | None
    error: ToolError | None
    event_ids: list[str]
    simulated_at: Timestamp
    idempotent_replay: bool = False


# --- Evidence ------------------------------------------------------------------------

ActorRole = Literal["controller", "reviewer", "harness"]
AssertionStatus = Literal["pass", "fail", "inconclusive", "not_applicable"]
Verdict = Literal["pass", "fail", "inconclusive"]
ExecutionStatus = Literal["running", "paused", "completed", "error"]
BusinessOutcome = Literal["fulfilled", "refused", "awaiting_review", "unresolved"]


class Actor(Strict):
    """Trusted identity bound by the harness when it builds a gateway or console.

    It is never parsed from tool arguments, so a caller cannot claim ``reviewer``.
    """

    actor_id: str
    role: ActorRole


class Event(Strict):
    seq: int
    event_id: EventId
    run_id: str
    simulated_at: Timestamp
    actor_id: str
    actor_role: ActorRole
    event_type: str
    payload: dict[str, Any]
    causation_id: str | None
    policy_version: str
    operation_key: str | None


class RecordedToolCall(Strict):
    seq: int
    call_id: str
    actor_id: str
    actor_role: ActorRole
    tool: str
    arguments: dict[str, Any]
    operation_key: str | None
    status: Literal["ok", "error"]
    response: ToolResult
    simulated_at: Timestamp


class WorkflowTransition(Strict):
    seq: int
    from_state: str
    to_state: str
    reason: str
    simulated_at: Timestamp


class AssertionResult(Strict):
    id: str
    status: AssertionStatus
    expected: str
    observed: str
    evidence_event_ids: list[str]
    evaluator_version: str
