"""Privileged reviewer and scenario operations.

These are NOT agent capabilities. The runner constructs them from scenario data
and never hands them to a controller. Construction checks the trusted actor's
role, so a controller-bound actor cannot obtain a working console.
"""

from __future__ import annotations

import json
from datetime import timedelta

from agent_rehearsal.canonical import format_ts, parse_ts
from agent_rehearsal.models import Actor, SupplierStatus
from agent_rehearsal.policy import proposal_hash
from agent_rehearsal.store import Store, Tx


class ReviewerConsole:
    def __init__(self, store: Store, actor: Actor) -> None:
        if actor.role != "reviewer":
            raise PermissionError("only a reviewer actor may operate the reviewer console")
        self._store = store
        self._actor = actor

    def grant_approval(self, request_id: str, ttl_seconds: int) -> str:
        """Grant an approval bound to the request's current proposal and policy version."""
        with self._store.transaction() as tx:
            return self._grant_approval(tx, request_id, ttl_seconds)

    def _grant_approval(self, tx: Tx, request_id: str, ttl_seconds: int) -> str:
        """Same grant inside the caller's transaction, so a reviewer decision commits with it.

        Keeps the TTL bound and request lookup. Commit-time policy checks stay in the gateway.
        """
        max_ttl = json.loads(tx.meta("policy_json"))["max_approval_ttl_seconds"]
        if not 0 < ttl_seconds <= max_ttl:
            raise ValueError(f"ttl_seconds must be in (0, {max_ttl}]")
        req = tx.row("SELECT * FROM requests WHERE id = ?", (request_id,))
        if req is None:
            raise LookupError(f"no request {request_id}")
        now = tx.tick()
        approval_id = tx.next_id("APR")
        expires_at = format_ts(parse_ts(now) + timedelta(seconds=ttl_seconds))
        bound = {
            "approval_id": approval_id,
            "request_id": req["id"],
            "supplier_id": req["supplier_id"],
            "amount_minor": req["amount_minor"],
            "currency": req["currency"],
            "proposal_hash": proposal_hash(req),
            "policy_version": tx.meta("policy_version"),
            "reviewer_id": self._actor.actor_id,
            "granted_at": now,
            "expires_at": expires_at,
        }
        tx.execute(
            "INSERT INTO approvals(id, request_id, supplier_id, amount_minor, currency, "
            "proposal_hash, policy_version, reviewer_id, granted_at, expires_at, revoked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                approval_id,
                bound["request_id"],
                bound["supplier_id"],
                bound["amount_minor"],
                bound["currency"],
                bound["proposal_hash"],
                bound["policy_version"],
                bound["reviewer_id"],
                now,
                expires_at,
            ),
        )
        tx.add_event(self._actor, "approval.granted", bound)
        return approval_id

    def revoke_approval(self, approval_id: str) -> str:
        """Revoke blocks future commits; it never erases an earlier committed purchase."""
        with self._store.transaction() as tx:
            appr = tx.row("SELECT * FROM approvals WHERE id = ?", (approval_id,))
            if appr is None:
                raise LookupError(f"no approval {approval_id}")
            if appr["revoked_at"] is not None:
                raise ValueError("approval already revoked")
            now = tx.tick()
            tx.execute("UPDATE approvals SET revoked_at = ? WHERE id = ?", (now, approval_id))
            return tx.add_event(
                self._actor,
                "approval.revoked",
                {"approval_id": approval_id, "request_id": appr["request_id"], "revoked_at": now},
            )


class ScenarioConsole:
    """Harness-only world controls used by direct tests (Session 2 builds faults on these)."""

    def __init__(self, store: Store, actor: Actor) -> None:
        if actor.role != "harness":
            raise PermissionError("only the harness may operate the scenario console")
        self._store = store
        self._actor = actor

    def set_supplier_status(self, supplier_id: str, status: SupplierStatus) -> str:
        if status not in ("active", "suspended"):
            raise ValueError(f"unknown supplier status {status!r}")
        with self._store.transaction() as tx:
            sup = tx.row("SELECT * FROM suppliers WHERE id = ?", (supplier_id,))
            if sup is None:
                raise LookupError(f"no supplier {supplier_id}")
            tx.tick()
            tx.execute(
                "UPDATE suppliers SET status = ?, version = version + 1 WHERE id = ?",
                (status, supplier_id),
            )
            return tx.add_event(
                self._actor,
                "supplier.status_changed",
                {
                    "supplier_id": supplier_id,
                    "from_status": sup["status"],
                    "to_status": status,
                    "version_after": sup["version"] + 1,
                },
            )

    def advance_clock(self, seconds: int) -> str:
        if seconds <= 0:
            raise ValueError("seconds must be positive")
        with self._store.transaction() as tx:
            before = tx.now()
            after = tx.tick(seconds)
            return tx.add_event(
                self._actor, "clock.advanced", {"from": before, "to": after, "seconds": seconds}
            )

    def advance_clock_to(self, moment: str) -> str:
        delta = int((parse_ts(moment) - parse_ts(self._store.meta("now"))).total_seconds())
        return self.advance_clock(delta)
