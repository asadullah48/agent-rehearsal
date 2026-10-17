"""Fictional policy ``demo-procurement-v1``.

These rules are intentionally strict demo rules. They do not represent the
procurement law or policy of any real government or organisation.

This module is used by the gateway at the mutation boundary. The evaluator
deliberately does NOT import it: it re-derives authorization from evidence.
"""

from __future__ import annotations

from typing import Any

from agent_rehearsal.canonical import parse_ts, sha256_hex
from agent_rehearsal.models import Strict


class Policy(Strict):
    policy_version: str
    currency: str
    max_approval_ttl_seconds: int
    max_proposed_actions: int
    max_attempts_per_operation: int
    rules: tuple[str, ...]


POLICY = Policy(
    policy_version="demo-procurement-v1",
    currency="AED",
    max_approval_ttl_seconds=7 * 24 * 3600,
    max_proposed_actions=30,
    max_attempts_per_operation=3,
    rules=(
        "Every purchase requires an active supplier, adequate reserved funds, and an active "
        "human approval.",
        "Approval binds to request, supplier, amount, currency, proposal hash and policy version.",
        "Only the reviewer role can grant or revoke approval.",
        "Approval is valid only while now < expires_at, unrevoked, and matching the proposal.",
        "Supplier, approval, policy version and reservation ownership are checked at commit.",
        "A committed purchase consumes its reservation exactly once; consumed funds are never "
        "refunded by a release.",
        "At most one purchase order exists per logical purchase intent in a run.",
        "Every mutation records actor, operation identity, policy version, simulated time and "
        "outcome in the same transaction.",
    ),
)

KNOWN_POLICIES: dict[str, Policy] = {POLICY.policy_version: POLICY}


def policy_hash(policy: Policy) -> str:
    return sha256_hex(policy.model_dump(mode="json"))


def proposal_fields(request: dict[str, Any]) -> dict[str, Any]:
    """The exact fields an approval binds to (documented in the README)."""
    return {
        "request_id": request["id"],
        "supplier_id": request["supplier_id"],
        "budget_id": request["budget_id"],
        "amount_minor": request["amount_minor"],
        "currency": request["currency"],
        "description": request["description"],
    }


def proposal_hash(request: dict[str, Any]) -> str:
    return sha256_hex(proposal_fields(request))


def logical_intent_id(request_id: str) -> str:
    """One logical purchase intent per request. Derived by the gateway, never caller-supplied."""
    return f"PI-{request_id}"


def approval_problem(
    approval: dict[str, Any] | None,
    request: dict[str, Any],
    current_policy_version: str,
    now: str,
) -> str | None:
    """Return a stable reason code if the approval cannot authorize a commit at ``now``."""
    if approval is None:
        return "APPROVAL_MISSING"
    if approval["revoked_at"] is not None:
        return "APPROVAL_REVOKED"
    if parse_ts(now) >= parse_ts(approval["expires_at"]):  # equality at expiry means expired
        return "APPROVAL_EXPIRED"
    bound = (
        approval["request_id"] == request["id"]
        and approval["supplier_id"] == request["supplier_id"]
        and approval["amount_minor"] == request["amount_minor"]
        and approval["currency"] == request["currency"]
        and approval["proposal_hash"] == proposal_hash(request)
    )
    if not bound:
        return "APPROVAL_MISMATCH"
    if approval["policy_version"] != current_policy_version:
        return "POLICY_VERSION_MISMATCH"
    return None


def approval_status(approval: dict[str, Any], now: str) -> str:
    if approval["revoked_at"] is not None:
        return "revoked"
    if parse_ts(now) >= parse_ts(approval["expires_at"]):
        return "expired"
    return "active"
