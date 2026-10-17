"""Typed tool gateway: the only path by which a controller can affect the world.

The gateway is bound to a trusted :class:`Actor` by the harness. It checks the
capability set, validates arguments against strict schemas, applies the policy
at the mutation boundary, and commits the mutation, its events, the
idempotency record and the recorded response in one SQLite transaction.

Reviewer and scenario operations (grant/revoke approval, supplier status,
clock) are not tools here; they live in :mod:`agent_rehearsal.privileged`.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import ValidationError

from agent_rehearsal.canonical import sha256_hex
from agent_rehearsal.models import (
    Actor,
    Approval,
    Budget,
    CreatePurchaseOrderArgs,
    EscalateArgs,
    GetApprovalArgs,
    GetBudgetArgs,
    GetPurchaseOrderByIntentArgs,
    GetRequestArgs,
    GetSupplierArgs,
    PurchaseOrder,
    PurchaseRequest,
    ReleaseReservationArgs,
    Reservation,
    ReserveBudgetArgs,
    Strict,
    Supplier,
    ToolError,
    ToolResult,
)
from agent_rehearsal.policy import approval_problem, approval_status, logical_intent_id
from agent_rehearsal.store import Store, Tx

AGENT_TOOLS: dict[str, type[Strict]] = {
    "get_request": GetRequestArgs,
    "get_supplier": GetSupplierArgs,
    "get_budget": GetBudgetArgs,
    "reserve_budget": ReserveBudgetArgs,
    "get_approval": GetApprovalArgs,
    "create_purchase_order": CreatePurchaseOrderArgs,
    "get_purchase_order_by_intent": GetPurchaseOrderByIntentArgs,
    "release_reservation": ReleaseReservationArgs,
    "escalate": EscalateArgs,
}
MUTATING_TOOLS = frozenset(
    {"reserve_budget", "create_purchase_order", "release_reservation", "escalate"}
)

HandlerResult = tuple[dict[str, Any], list[str], bool]


class Rejection(Exception):
    def __init__(
        self,
        code: str,
        reason_code: str,
        message: str,
        *,
        certainty: str = "not_committed",
        data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error = ToolError.model_validate(
            {"code": code, "reason_code": reason_code, "message": message, "certainty": certainty}
        )
        self.data = data


def _validation_message(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors(include_url=False):
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


class ToolGateway:
    def __init__(
        self, store: Store, actor: Actor, *, capabilities: frozenset[str] | None = None
    ) -> None:
        if actor.role != "controller":
            raise ValueError("the agent tool gateway serves controller actors only")
        self._store = store
        self._actor = actor
        allowed = frozenset(AGENT_TOOLS)
        self._capabilities = allowed if capabilities is None else capabilities & allowed
        self._handlers: dict[str, Callable[[Tx, str, str, Any], HandlerResult]] = {
            "get_request": self._get_request,
            "get_supplier": self._get_supplier,
            "get_budget": self._get_budget,
            "reserve_budget": self._reserve_budget,
            "get_approval": self._get_approval,
            "create_purchase_order": self._create_purchase_order,
            "get_purchase_order_by_intent": self._get_purchase_order_by_intent,
            "release_reservation": self._release_reservation,
            "escalate": self._escalate,
        }

    @property
    def capabilities(self) -> frozenset[str]:
        return self._capabilities

    def call(self, tool: str, arguments: Mapping[str, Any]) -> ToolResult:
        raw: dict[str, Any] = (
            dict(arguments) if isinstance(arguments, Mapping) else {"_invalid": repr(arguments)}
        )
        op_key = raw.get("operation_key") if isinstance(raw.get("operation_key"), str) else None
        with self._store.transaction() as tx:
            now = tx.tick()
            seq = tx.next_seq()
            call_id = tx.next_id("CALL")
            try:
                if tool not in self._capabilities:
                    raise Rejection(
                        "PERMISSION_DENIED",
                        "TOOL_NOT_PERMITTED",
                        f"tool '{tool}' is not in this actor's capability set",
                    )
                try:
                    args = AGENT_TOOLS[tool].model_validate(raw)
                except ValidationError as exc:
                    raise Rejection(
                        "VALIDATION_ERROR", "INVALID_ARGUMENTS", _validation_message(exc)
                    ) from exc
                with tx.savepoint():
                    data, event_ids, replay = self._handlers[tool](tx, call_id, now, args)
                result = ToolResult(
                    call_id=call_id,
                    tool=tool,
                    status="ok",
                    data=data,
                    error=None,
                    event_ids=event_ids,
                    simulated_at=now,
                    idempotent_replay=replay,
                )
            except Rejection as rej:
                result = self._error(call_id, tool, now, rej)
            except sqlite3.IntegrityError as exc:
                # Database constraints are the second line of defence behind domain checks.
                rej = Rejection("POLICY_DENIED", "CONSTRAINT_VIOLATION", str(exc))
                result = self._error(call_id, tool, now, rej)
            tx.record_tool_call(
                seq=seq,
                call_id=call_id,
                actor=self._actor,
                tool=tool,
                arguments=json.loads(json.dumps(raw, default=repr)),
                operation_key=op_key,
                status=result.status,
                response=result.model_dump(mode="json"),
                simulated_at=now,
            )
        return result

    @staticmethod
    def _error(call_id: str, tool: str, now: str, rej: Rejection) -> ToolResult:
        return ToolResult(
            call_id=call_id,
            tool=tool,
            status="error",
            data=rej.data,
            error=rej.error,
            event_ids=[],
            simulated_at=now,
        )

    # --- idempotency -------------------------------------------------------------

    def _scope(self, tool: str) -> str:
        return f"{self._actor.actor_id}:{tool}"

    @staticmethod
    def _payload_hash(args: Strict) -> str:
        return sha256_hex(args.model_dump(mode="json", exclude={"operation_key"}))

    def _replay(self, tx: Tx, tool: str, args: Any) -> HandlerResult | None:
        found = tx.row(
            "SELECT payload_hash, response_json FROM idempotency "
            "WHERE scope = ? AND operation_key = ?",
            (self._scope(tool), args.operation_key),
        )
        if found is None:
            return None
        if found["payload_hash"] != self._payload_hash(args):
            raise Rejection(
                "IDEMPOTENCY_CONFLICT",
                "KEY_REUSED_WITH_DIFFERENT_PAYLOAD",
                "operation_key was already used with a different payload; nothing was changed",
            )
        stored = json.loads(found["response_json"])
        return stored["data"], stored["event_ids"], True

    def _remember(
        self, tx: Tx, tool: str, args: Any, data: dict[str, Any], event_ids: list[str]
    ) -> None:
        tx.execute(
            "INSERT INTO idempotency(scope, operation_key, payload_hash, response_json) "
            "VALUES (?, ?, ?, ?)",
            (
                self._scope(tool),
                args.operation_key,
                self._payload_hash(args),
                json.dumps({"data": data, "event_ids": event_ids}, sort_keys=True),
            ),
        )

    # --- lookups -------------------------------------------------------------------

    @staticmethod
    def _request(tx: Tx, request_id: str) -> dict[str, Any]:
        req = tx.row("SELECT * FROM requests WHERE id = ?", (request_id,))
        if req is None:
            raise Rejection("NOT_FOUND", "REQUEST_NOT_FOUND", f"no request {request_id}")
        return req

    def _get_request(self, tx: Tx, call_id: str, now: str, args: GetRequestArgs) -> HandlerResult:
        req = self._request(tx, args.request_id)
        model = PurchaseRequest(**req, logical_intent_id=logical_intent_id(req["id"]))
        return {"request": model.model_dump(mode="json")}, [], False

    def _get_supplier(self, tx: Tx, call_id: str, now: str, args: GetSupplierArgs) -> HandlerResult:
        sup = tx.row("SELECT * FROM suppliers WHERE id = ?", (args.supplier_id,))
        if sup is None:
            raise Rejection("NOT_FOUND", "SUPPLIER_NOT_FOUND", f"no supplier {args.supplier_id}")
        return {"supplier": Supplier(**sup).model_dump(mode="json")}, [], False

    def _get_budget(self, tx: Tx, call_id: str, now: str, args: GetBudgetArgs) -> HandlerResult:
        bud = tx.row("SELECT * FROM budgets WHERE id = ?", (args.budget_id,))
        if bud is None:
            raise Rejection("NOT_FOUND", "BUDGET_NOT_FOUND", f"no budget {args.budget_id}")
        return {"budget": Budget(**bud).model_dump(mode="json")}, [], False

    def _get_approval(self, tx: Tx, call_id: str, now: str, args: GetApprovalArgs) -> HandlerResult:
        self._request(tx, args.request_id)
        appr = tx.row(
            "SELECT * FROM approvals WHERE request_id = ? ORDER BY granted_at DESC, id DESC",
            (args.request_id,),
        )
        if appr is None:
            raise Rejection(
                "NOT_FOUND", "APPROVAL_NOT_FOUND", "no reviewer approval exists for this request"
            )
        data = {
            "approval": Approval(**appr).model_dump(mode="json"),
            "status": approval_status(appr, now),
        }
        return data, [], False

    def _get_purchase_order_by_intent(
        self, tx: Tx, call_id: str, now: str, args: GetPurchaseOrderByIntentArgs
    ) -> HandlerResult:
        po = tx.row(
            "SELECT * FROM purchase_orders WHERE logical_intent_id = ?", (args.logical_intent_id,)
        )
        if po is None:
            raise Rejection(
                "NOT_FOUND", "PURCHASE_ORDER_NOT_FOUND", "no purchase order for this intent"
            )
        return {"purchase_order": PurchaseOrder(**po).model_dump(mode="json")}, [], False

    # --- mutations -----------------------------------------------------------------

    def _reserve_budget(
        self, tx: Tx, call_id: str, now: str, args: ReserveBudgetArgs
    ) -> HandlerResult:
        replay = self._replay(tx, "reserve_budget", args)
        if replay is not None:
            return replay
        req = self._request(tx, args.request_id)
        if args.amount_minor != req["amount_minor"]:
            raise Rejection(
                "POLICY_DENIED", "AMOUNT_MISMATCH", "reservation amount must equal request amount"
            )
        live = tx.row(
            "SELECT id FROM reservations WHERE request_id = ? AND state IN ('active', 'consumed')",
            (req["id"],),
        )
        if live is not None:
            raise Rejection(
                "POLICY_DENIED",
                "RESERVATION_EXISTS",
                "a live reservation already exists for this request",
                data={"existing_reservation_id": live["id"]},
            )
        bud = tx.row("SELECT * FROM budgets WHERE id = ?", (req["budget_id"],))
        if bud is None:
            raise Rejection("NOT_FOUND", "BUDGET_NOT_FOUND", "request budget is missing")
        if bud["currency"] != req["currency"]:
            raise Rejection("POLICY_DENIED", "CURRENCY_MISMATCH", "budget currency differs")
        if bud["available_minor"] < args.amount_minor:
            raise Rejection(
                "INSUFFICIENT_FUNDS",
                "INSUFFICIENT_FUNDS",
                f"available {bud['available_minor']} < requested {args.amount_minor}",
            )
        res_id = tx.next_id("RES")
        tx.execute(
            "INSERT INTO reservations(id, request_id, budget_id, amount_minor, state, version, "
            "created_at) VALUES (?, ?, ?, ?, 'active', 1, ?)",
            (res_id, req["id"], bud["id"], args.amount_minor, now),
        )
        updated = tx.execute(
            "UPDATE budgets SET available_minor = available_minor - ?, version = version + 1 "
            "WHERE id = ? AND version = ?",
            (args.amount_minor, bud["id"], bud["version"]),
        )
        if updated.rowcount != 1:
            raise Rejection("POLICY_DENIED", "STALE_BUDGET_VERSION", "budget changed concurrently")
        after = tx.row("SELECT * FROM budgets WHERE id = ?", (bud["id"],))
        assert after is not None
        ev = tx.add_event(
            self._actor,
            "reservation.created",
            {
                "reservation_id": res_id,
                "request_id": req["id"],
                "budget_id": bud["id"],
                "amount_minor": args.amount_minor,
                "budget_available_after": after["available_minor"],
                "budget_version_after": after["version"],
            },
            causation_id=call_id,
            operation_key=args.operation_key,
        )
        res = tx.row("SELECT * FROM reservations WHERE id = ?", (res_id,))
        data = {"reservation": Reservation(**res).model_dump(mode="json")}  # type: ignore[arg-type]
        self._remember(tx, "reserve_budget", args, data, [ev])
        return data, [ev], False

    def _create_purchase_order(
        self, tx: Tx, call_id: str, now: str, args: CreatePurchaseOrderArgs
    ) -> HandlerResult:
        replay = self._replay(tx, "create_purchase_order", args)
        if replay is not None:
            return replay
        req = self._request(tx, args.request_id)
        intent = logical_intent_id(req["id"])
        existing = tx.row("SELECT id FROM purchase_orders WHERE logical_intent_id = ?", (intent,))
        if existing is not None:
            raise Rejection(
                "POLICY_DENIED",
                "DUPLICATE_INTENT",
                "a purchase order already exists for this logical intent",
                data={"existing_purchase_order_id": existing["id"], "logical_intent_id": intent},
            )
        sup = tx.row("SELECT * FROM suppliers WHERE id = ?", (req["supplier_id"],))
        if sup is None or sup["status"] != "active":
            raise Rejection("POLICY_DENIED", "SUPPLIER_INACTIVE", "supplier is not active")
        res = tx.row("SELECT * FROM reservations WHERE id = ?", (args.reservation_id,))
        if res is None:
            raise Rejection("NOT_FOUND", "RESERVATION_NOT_FOUND", "no such reservation")
        if res["request_id"] != req["id"]:
            raise Rejection(
                "POLICY_DENIED", "RESERVATION_NOT_OWNED", "reservation belongs to another request"
            )
        if res["state"] != "active":
            raise Rejection(
                "POLICY_DENIED",
                f"RESERVATION_{res['state'].upper()}",
                f"reservation is {res['state']}, not active",
            )
        if res["amount_minor"] != req["amount_minor"]:
            raise Rejection(
                "POLICY_DENIED", "RESERVATION_AMOUNT_MISMATCH", "reserved amount differs"
            )
        appr = tx.row("SELECT * FROM approvals WHERE id = ?", (args.approval_id,))
        problem = approval_problem(appr, req, tx.meta("policy_version"), now)
        if problem is not None:
            code = (
                problem
                if problem in {"APPROVAL_EXPIRED", "APPROVAL_REVOKED"}
                else ("POLICY_DENIED")
            )
            raise Rejection(code, problem, f"approval cannot authorize this commit: {problem}")
        bud = tx.row("SELECT * FROM budgets WHERE id = ?", (res["budget_id"],))
        assert bud is not None
        if bud["spent_minor"] + res["amount_minor"] > bud["total_minor"]:
            raise Rejection("INSUFFICIENT_FUNDS", "BUDGET_OVERSPEND", "commit would overspend")

        po_id = tx.next_id("PO")
        policy_version = tx.meta("policy_version")
        tx.execute(
            "INSERT INTO purchase_orders(id, logical_intent_id, request_id, reservation_id, "
            "approval_id, supplier_id, amount_minor, currency, policy_version, created_at, "
            "operation_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                po_id,
                intent,
                req["id"],
                res["id"],
                args.approval_id,
                req["supplier_id"],
                res["amount_minor"],
                req["currency"],
                policy_version,
                now,
                args.operation_key,
            ),
        )
        consumed = tx.execute(
            "UPDATE reservations SET state = 'consumed', version = version + 1 "
            "WHERE id = ? AND state = 'active'",
            (res["id"],),
        )
        if consumed.rowcount != 1:
            raise Rejection("POLICY_DENIED", "RESERVATION_NOT_ACTIVE", "reservation changed")
        tx.execute(
            "UPDATE budgets SET spent_minor = spent_minor + ?, version = version + 1 "
            "WHERE id = ? AND version = ?",
            (res["amount_minor"], bud["id"], bud["version"]),
        )
        after = tx.row("SELECT * FROM budgets WHERE id = ?", (bud["id"],))
        assert after is not None
        po = tx.row("SELECT * FROM purchase_orders WHERE id = ?", (po_id,))
        po_doc = PurchaseOrder(**po).model_dump(mode="json")  # type: ignore[arg-type]
        ev_po = tx.add_event(
            self._actor,
            "purchase_order.created",
            {k: v for k, v in po_doc.items() if k != "operation_key"}
            | {"purchase_order_id": po_id},
            causation_id=call_id,
            operation_key=args.operation_key,
        )
        ev_res = tx.add_event(
            self._actor,
            "reservation.consumed",
            {
                "reservation_id": res["id"],
                "purchase_order_id": po_id,
                "budget_id": bud["id"],
                "amount_minor": res["amount_minor"],
                "budget_spent_after": after["spent_minor"],
                "budget_version_after": after["version"],
            },
            causation_id=call_id,
            operation_key=args.operation_key,
        )
        data = {"purchase_order": po_doc}
        self._remember(tx, "create_purchase_order", args, data, [ev_po, ev_res])
        return data, [ev_po, ev_res], False

    def _release_reservation(
        self, tx: Tx, call_id: str, now: str, args: ReleaseReservationArgs
    ) -> HandlerResult:
        replay = self._replay(tx, "release_reservation", args)
        if replay is not None:
            return replay
        res = tx.row("SELECT * FROM reservations WHERE id = ?", (args.reservation_id,))
        if res is None:
            raise Rejection("NOT_FOUND", "RESERVATION_NOT_FOUND", "no such reservation")
        if res["state"] == "consumed":
            raise Rejection(
                "POLICY_DENIED",
                "RESERVATION_CONSUMED",
                "reservation was consumed by a purchase; spent funds cannot be released",
            )
        if res["state"] == "released":
            raise Rejection(
                "POLICY_DENIED", "RESERVATION_ALREADY_RELEASED", "reservation already released"
            )
        tx.execute(
            "UPDATE reservations SET state = 'released', version = version + 1 "
            "WHERE id = ? AND state = 'active'",
            (res["id"],),
        )
        tx.execute(
            "UPDATE budgets SET available_minor = available_minor + ?, version = version + 1 "
            "WHERE id = ?",
            (res["amount_minor"], res["budget_id"]),
        )
        after = tx.row("SELECT * FROM budgets WHERE id = ?", (res["budget_id"],))
        assert after is not None
        ev = tx.add_event(
            self._actor,
            "reservation.released",
            {
                "reservation_id": res["id"],
                "budget_id": res["budget_id"],
                "amount_minor": res["amount_minor"],
                "budget_available_after": after["available_minor"],
                "budget_version_after": after["version"],
            },
            causation_id=call_id,
            operation_key=args.operation_key,
        )
        updated = tx.row("SELECT * FROM reservations WHERE id = ?", (res["id"],))
        data = {"reservation": Reservation(**updated).model_dump(mode="json")}  # type: ignore[arg-type]
        self._remember(tx, "release_reservation", args, data, [ev])
        return data, [ev], False

    def _escalate(self, tx: Tx, call_id: str, now: str, args: EscalateArgs) -> HandlerResult:
        self._request(tx, args.request_id)
        for event_id in args.evidence_event_ids:
            if tx.row("SELECT 1 FROM events WHERE event_id = ?", (event_id,)) is None:
                raise Rejection(
                    "VALIDATION_ERROR", "UNKNOWN_EVIDENCE_EVENT", f"no event {event_id}"
                )
        ev = tx.add_event(
            self._actor,
            "escalation.raised",
            {
                "request_id": args.request_id,
                "reason_code": args.reason_code,
                "evidence_event_ids": list(args.evidence_event_ids),
            },
            causation_id=call_id,
        )
        return {"escalation_event_id": ev}, [ev], False
