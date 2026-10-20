"""Scenario-only reviewer for live controllers; never reads model prose.

The only model-derived input is the structured ``request_id`` of the review request.
Existing reviewer_actions define authorization rules, not consumable live steps.
Each rule permits only its initial-world proposal (every bound field and the digest),
with its configured TTL. A valid approval is reused; a revoked or expired approval is
never renewed. The check, grant and ``review.decided`` evidence commit atomically, and
recovery replays the recorded decision instead of deciding again.
"""

from __future__ import annotations

import json

from agent_rehearsal.live_budget import LiveStop
from agent_rehearsal.models import Actor, ToolResult
from agent_rehearsal.policy import approval_problem, proposal_fields, proposal_hash
from agent_rehearsal.privileged import ReviewerConsole
from agent_rehearsal.scenarios import Scenario
from agent_rehearsal.store import HARNESS, Store, Tx

DECIDED_FROM = ["scenario.reviewer_actions", "scenario.initial_world", "requests", "approvals"]


class LiveReviewer:
    def __init__(self, store: Store, scenario: Scenario):
        self.store = store
        self.scenario = scenario

    def review(self, request_id: str, step: int) -> ToolResult:
        with self.store.transaction() as tx:
            # A crash may occur after this decision commits but before journal completion.
            prior = tx.row(
                "SELECT * FROM events WHERE event_type='review.decided' "
                "AND json_extract(payload_json, '$.step') = ?",
                (step,),
            )
            if prior:
                payload = json.loads(prior["payload_json"])
                if payload["request_id"] != request_id:
                    raise ValueError("review recovery request differs")
                event_id, now = prior["event_id"], prior["simulated_at"]
            else:
                actor, payload = self._decide(tx, request_id, step)
                event_id = tx.add_event(actor, "review.decided", payload)
                now = tx.now()
        if payload["decision"] == "unhandled":
            raise LiveStop("review_unhandled", f"{request_id}: {payload['reason']}")
        return ToolResult(
            call_id=f"review-{step}",
            tool="request_review",
            status="ok",
            data=payload,
            error=None,
            event_ids=[event_id],
            simulated_at=now,
        )

    def _decide(self, tx: Tx, request_id: str, step: int) -> tuple[Actor, dict]:
        payload = {
            "step": step,
            "request_id": request_id,
            "decided_from": DECIDED_FROM,
            "rule": None,
            "decision": "unhandled",
            "reason": "no scenario review rule",
            "approval_id": None,
        }
        rules = [a for a in self.scenario.reviewer_actions if a.request_id == request_id]
        expected = next(
            (r for r in self.scenario.initial_world.requests if r.id == request_id), None
        )
        if len(rules) > 1:
            payload["reason"] = "ambiguous scenario review rules"
        if len(rules) != 1 or expected is None:
            return HARNESS, payload
        rule = rules[0]
        actor = Actor(actor_id=rule.reviewer_id, role="reviewer")
        fixture = expected.model_dump(mode="json")
        want = proposal_fields(fixture)
        digest = proposal_hash(fixture)
        payload["rule"] = {
            **rule.model_dump(mode="json"),
            "match": "exact_initial_proposal",
            "expected_proposal": want,
            "expected_digest": digest,
            "policy_version": self.scenario.policy_version,
            "renewal": "never",
        }
        payload["decision"] = "rejected"
        req = tx.row("SELECT * FROM requests WHERE id=?", (request_id,))
        if req is None:
            payload["reason"] = "request missing from world"
            return actor, payload
        seen = proposal_fields(req)
        payload["observed_digest"] = proposal_hash(req)
        payload["mismatched_fields"] = sorted(k for k in want if seen[k] != want[k])
        if payload["mismatched_fields"] or payload["observed_digest"] != digest:
            payload["reason"] = "proposal differs from scenario expectation"
            return actor, payload
        if tx.meta("policy_version") != self.scenario.policy_version:
            payload["reason"] = "policy differs from scenario expectation"
            return actor, payload
        # Same ordering as the get_approval tool, so both report the same approval.
        approval = tx.row(
            "SELECT * FROM approvals WHERE request_id=? ORDER BY granted_at DESC, id DESC",
            (request_id,),
        )
        if approval is not None:
            problem = approval_problem(approval, req, tx.meta("policy_version"), tx.now())
            if problem:
                payload["reason"] = problem
            elif approval["reviewer_id"] != rule.reviewer_id:
                payload["reason"] = "reviewer differs from scenario rule"
            else:
                payload.update(
                    decision="approved",
                    reason="existing valid approval",
                    approval_id=approval["id"],
                )
            return actor, payload
        approval_id = ReviewerConsole(self.store, actor)._grant_approval(
            tx, request_id, rule.ttl_seconds
        )
        payload.update(decision="approved", reason="exact match", approval_id=approval_id)
        return actor, payload
