"""TEST-ONLY corruption helpers for evaluator negative controls.

These deliberately bypass the gateway and policy to construct invalid recorded
states. They live under tests/ and are never offered through the product CLI.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_rehearsal.models import Actor
from agent_rehearsal.store import Store

CONTROLLER = Actor(actor_id="CTRL-REF", role="controller")


def open_rw(db_path: Path) -> Store:
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return Store(conn, db_path)


def forge_purchase_order(
    store: Store, *, reservation_id: str, approval_id: str, actor: Actor = CONTROLLER
) -> str:
    """Write a PO plus matching evidence as if the gateway had committed it - with no checks."""
    with store.transaction() as tx:
        now = tx.tick()
        res = tx.row("SELECT * FROM reservations WHERE id = ?", (reservation_id,))
        assert res is not None
        seq = tx.next_seq()
        call_id = tx.next_id("CALL")
        po_id = tx.next_id("PO")
        key = "forged-commit-0001"
        policy_version = tx.meta("policy_version")
        tx.execute(
            "INSERT INTO purchase_orders VALUES (?, ?, ?, ?, ?, 'SUP-001', ?, 'AED', ?, ?, ?)",
            (
                po_id,
                "PI-REQ-001",
                "REQ-001",
                reservation_id,
                approval_id,
                res["amount_minor"],
                policy_version,
                now,
                key,
            ),
        )
        tx.execute(
            "UPDATE reservations SET state='consumed', version=version+1 WHERE id=?",
            (reservation_id,),
        )
        tx.execute(
            "UPDATE budgets SET spent_minor=spent_minor+?, version=version+1 WHERE id=?",
            (res["amount_minor"], res["budget_id"]),
        )
        payload = {
            "purchase_order_id": po_id,
            "id": po_id,
            "logical_intent_id": "PI-REQ-001",
            "request_id": "REQ-001",
            "reservation_id": reservation_id,
            "approval_id": approval_id,
            "supplier_id": "SUP-001",
            "amount_minor": res["amount_minor"],
            "currency": "AED",
            "policy_version": policy_version,
            "created_at": now,
        }
        ev1 = tx.add_event(
            actor, "purchase_order.created", payload, causation_id=call_id, operation_key=key
        )
        ev2 = tx.add_event(
            actor,
            "reservation.consumed",
            {
                "reservation_id": reservation_id,
                "purchase_order_id": po_id,
                "budget_id": res["budget_id"],
                "amount_minor": res["amount_minor"],
            },
            causation_id=call_id,
            operation_key=key,
        )
        tx.record_tool_call(
            seq=seq,
            call_id=call_id,
            actor=actor,
            tool="create_purchase_order",
            arguments={
                "request_id": "REQ-001",
                "reservation_id": reservation_id,
                "approval_id": approval_id,
                "operation_key": key,
            },
            operation_key=key,
            status="ok",
            response={
                "call_id": call_id,
                "tool": "create_purchase_order",
                "status": "ok",
                "data": {"purchase_order": payload},
                "error": None,
                "event_ids": [ev1, ev2],
                "simulated_at": now,
                "idempotent_replay": False,
            },
            simulated_at=now,
        )
    return po_id


def forge_approval_without_reviewer(store: Store) -> str:
    """An approval row plus a grant event authored by the controller (impersonation)."""
    with store.transaction() as tx:
        now = tx.tick()
        req = tx.row("SELECT * FROM requests WHERE id='REQ-001'")
        assert req is not None
        appr_id = tx.next_id("APR")
        expires = "2026-01-06T09:00:00Z"
        tx.execute(
            "INSERT INTO approvals VALUES (?, 'REQ-001', 'SUP-001', ?, 'AED', ?, ?, 'REV-001', ?, "
            "?, NULL)",
            (
                appr_id,
                req["amount_minor"],
                req["proposal_hash"],
                tx.meta("policy_version"),
                now,
                expires,
            ),
        )
        tx.add_event(
            CONTROLLER,
            "approval.granted",
            {
                "approval_id": appr_id,
                "request_id": "REQ-001",
                "supplier_id": "SUP-001",
                "amount_minor": req["amount_minor"],
                "currency": "AED",
                "proposal_hash": req["proposal_hash"],
                "policy_version": tx.meta("policy_version"),
                "reviewer_id": "REV-001",
                "granted_at": now,
                "expires_at": expires,
            },
            causation_id=None,
        )
    return appr_id


def tamper(db_path: Path, sql: str, params: tuple[object, ...] = ()) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()
