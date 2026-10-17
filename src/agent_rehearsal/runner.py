"""Synchronous run harness (deterministic rerun from fixture + seed).

The harness is the trusted party: it binds actor identities, executes each
controller proposal through the gateway, applies scripted reviewer actions when
the controller pauses for review, journals workflow transitions, and closes the
run. It then evaluates from the persisted database and writes ``report.json``.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_rehearsal.canonical import canonical_json
from agent_rehearsal.controller import (
    ADAPTER,
    AwaitReview,
    Finished,
    ReferenceController,
    ToolProposal,
)
from agent_rehearsal.evaluator import Evaluation, evaluate_run
from agent_rehearsal.evidence import export_bundle
from agent_rehearsal.models import Actor, ToolResult
from agent_rehearsal.policy import KNOWN_POLICIES
from agent_rehearsal.privileged import ReviewerConsole, ScenarioConsole
from agent_rehearsal.recovery import (
    DecisionJournal,
    crash_if,
    sync_ledger,
    unknown_response,
    validate_recovery_evidence,
)
from agent_rehearsal.report import build_report, load_report, write_report
from agent_rehearsal.scenarios import Scenario, parse_scenario
from agent_rehearsal.store import HARNESS, Store
from agent_rehearsal.tools import ToolGateway

CONTROLLER_ACTOR = Actor(actor_id="CTRL-REF", role="controller")
DB_NAME = "run.sqlite"
REPORT_NAME = "report.json"


class OutputDirectoryNotEmpty(FileExistsError):
    pass


@dataclass(frozen=True)
class RunResult:
    run_dir: Path
    evaluation: Evaluation
    report: dict[str, Any]


def prepare_output_dir(output: Path) -> None:
    if output.exists():
        if not output.is_dir():
            raise OutputDirectoryNotEmpty(f"output path exists and is not a directory: {output}")
        if any(output.iterdir()):
            raise OutputDirectoryNotEmpty(f"refusing to write into nonempty directory: {output}")
    output.mkdir(parents=True, exist_ok=True)


def execute_run(
    scenario: Scenario, seed: int, output: Path, *, crash_at: str | None = None
) -> RunResult:
    """Run an already validated scenario. Callers must validate input first."""
    wall_start = time.perf_counter()
    prepare_output_dir(output)
    policy = KNOWN_POLICIES[scenario.policy_version]
    store = Store.create(
        output / DB_NAME,
        run_id=f"run-{uuid.uuid4().hex[:12]}",
        scenario=scenario,
        policy=policy,
        seed=seed,
        adapter=ADAPTER,
    )
    sync_ledger(store)
    try:
        _drive(
            store,
            scenario,
            seed,
            policy.max_proposed_actions,
            policy.max_attempts_per_operation,
            crash_at=crash_at,
        )
    finally:
        store.close()

    wall_ms = round((time.perf_counter() - wall_start) * 1000, 3)
    evaluation = evaluate_run(output / DB_NAME)
    report = build_report(output / DB_NAME, evaluation, wall_clock_ms=wall_ms)
    write_report(output / REPORT_NAME, report)
    export_bundle(output, report)
    return RunResult(output, evaluation, report)


def resume_run(output: Path) -> RunResult:
    store = Store.open_existing(output / DB_NAME)
    try:
        validate_recovery_evidence(store)
        scenario = parse_scenario(store.meta("scenario_json"))
        seed = int(store.meta("seed"))
        policy = KNOWN_POLICIES[scenario.policy_version]
        if store.meta("execution_status") == "running":
            _drive(
                store,
                scenario,
                seed,
                policy.max_proposed_actions,
                policy.max_attempts_per_operation,
                resuming=True,
            )
    finally:
        store.close()
    evaluation = evaluate_run(output / DB_NAME)
    report = build_report(output / DB_NAME, evaluation, wall_clock_ms=0)
    if (output / REPORT_NAME).exists():
        report = load_report(output / REPORT_NAME)
    else:
        write_report(output / REPORT_NAME, report)
    export_bundle(output, report)
    return RunResult(output, evaluation, report)


def _drive(
    store: Store,
    scenario: Scenario,
    seed: int,
    max_actions: int,
    max_attempts: int,
    *,
    crash_at: str | None = None,
    resuming: bool = False,
) -> None:
    gateway = ToolGateway(store, CONTROLLER_ACTOR)
    consoles = {
        r.id: ReviewerConsole(store, Actor(actor_id=r.id, role="reviewer"))
        for r in scenario.reviewers
    }
    pending = list(scenario.reviewer_actions)
    controller = ReferenceController(
        request_id=scenario.task.request_id,
        seed=seed,
        max_attempts_per_operation=max_attempts,
        duplicate_po_attempt=scenario.duplicate_po_attempt,
    )
    journaled = len(store.rows("SELECT * FROM workflow_transitions"))
    decisions = DecisionJournal(store)

    def journal() -> None:
        nonlocal journaled
        for t in controller.transitions[journaled:]:
            store.record_transition(t.from_state.value, t.to_state.value, t.reason)
        journaled = max(journaled, len(controller.transitions))

    proposals = 0
    purchase_occurrence = 0
    drive = controller.drive()
    reply = None
    try:
        while True:
            try:
                decision = drive.send(reply)
            except StopIteration as stop:
                journal()
                finished: Finished = stop.value
                store.finish_run(
                    "completed", controller_state=finished.state.value, summary=finished.summary
                )
                return
            journal()
            saved, recovering = decisions.begin(decision)
            if isinstance(decision, ToolProposal):
                proposals += 1
                if decision.tool == "create_purchase_order":
                    purchase_occurrence += 1
                if saved["reply_json"] is not None:
                    reply = ToolResult.model_validate_json(saved["reply_json"])
                    continue
                if proposals > max_actions:
                    drive.close()
                    store.finish_run(
                        "completed",
                        controller_state=controller.state.value,
                        summary="proposed-action limit reached; outcome unresolved",
                        limit_reached="max_proposed_actions",
                    )
                    return
                if decision.tool == "create_purchase_order":
                    for fault in scenario.faults:
                        if fault.boundary != "before_purchase":
                            continue
                        if fault.occurrence != purchase_occurrence:
                            continue
                        if store.rows(
                            "SELECT * FROM fault_firings WHERE fault_id = ?", (fault.id,)
                        ):
                            continue
                        req = store.rows(
                            "SELECT * FROM requests WHERE id = ?", (scenario.task.request_id,)
                        )[0]
                        if fault.effect == "approval_revocation":
                            approval = store.rows(
                                "SELECT * FROM approvals WHERE id = ?",
                                (decision.arguments["approval_id"],),
                            )[0]
                            event_id = consoles[approval["reviewer_id"]].revoke_approval(
                                approval["id"]
                            )
                        else:
                            event_id = ScenarioConsole(store, HARNESS).set_supplier_status(
                                req["supplier_id"], "suspended"
                            )
                        with store.transaction() as tx:
                            tx.execute(
                                "INSERT INTO fault_firings VALUES (?, ?, ?, ?)",
                                (fault.id, purchase_occurrence, fault.effect, event_id),
                            )
                if decision.tool == "reserve_budget":
                    crash_if(crash_at, "before-reservation")
                if decision.tool == "create_purchase_order":
                    crash_if(crash_at, "before-purchase")
                previous = (
                    store.rows(
                        "SELECT * FROM tool_calls WHERE seq >= ? AND tool = ? "
                        "AND arguments_json = ? ORDER BY seq",
                        (saved["cursor"], decision.tool, canonical_json(decision.arguments)),
                    )
                    if recovering
                    else []
                )
                if recovering and decision.tool == "create_purchase_order" and not previous:
                    intent = f"PI-{decision.arguments['request_id']}"
                    found = gateway.call(
                        "get_purchase_order_by_intent", {"logical_intent_id": intent}
                    )
                    if found.status == "ok":
                        raise ValueError(
                            "purchase exists without its recorded call; manual review required"
                        )
                    if found.error is None or found.error.reason_code != "PURCHASE_ORDER_NOT_FOUND":
                        raise ValueError("recovery lookup unresolved; purchase not retried")
                reply = (
                    ToolResult.model_validate_json(previous[0]["response_json"])
                    if previous
                    else gateway.call(decision.tool, decision.arguments)
                )
                if decision.tool == "create_purchase_order" and reply.status == "ok":
                    crash_if(crash_at, "after-purchase-commit")
                if decision.tool == "create_purchase_order" and reply.status == "ok":
                    for fault in scenario.faults:
                        if (
                            fault.boundary != "after_purchase_commit"
                            or fault.occurrence != purchase_occurrence
                        ):
                            continue
                        if store.rows(
                            "SELECT * FROM fault_firings WHERE fault_id = ?", (fault.id,)
                        ):
                            continue
                        reply, event_id = unknown_response(store, reply, fault.id)
                        with store.transaction() as tx:
                            tx.execute(
                                "INSERT INTO fault_firings VALUES (?, ?, ?, ?)",
                                (fault.id, purchase_occurrence, fault.effect, event_id),
                            )
                if previous and decision.tool == "create_purchase_order" and reply.status == "ok":
                    reply, _ = unknown_response(store, reply, "process-crash-recovery")
                decisions.complete(reply)
                if decision.tool == "reserve_budget":
                    crash_if(crash_at, "after-reservation")
                if decision.tool == "get_approval" and reply.status == "ok":
                    crash_if(crash_at, "after-approval")
                if decision.tool == "create_purchase_order":
                    crash_if(crash_at, "after-purchase-response")
            elif isinstance(decision, AwaitReview):
                due = [
                    a
                    for a in pending
                    if a.trigger == "on_awaiting_approval" and a.request_id == decision.request_id
                ]
                if not due:
                    drive.close()
                    store.finish_run(
                        "paused",
                        controller_state=controller.state.value,
                        summary="awaiting reviewer approval; no scripted reviewer action remains",
                    )
                    return
                action = due[0]
                pending.remove(action)
                if saved["reply_json"] is not None:
                    reply = None
                    continue
                grants = (
                    store.rows(
                        "SELECT * FROM events WHERE seq >= ? AND event_type='approval.granted'",
                        (saved["cursor"],),
                    )
                    if recovering
                    else []
                )
                if not grants:
                    consoles[action.reviewer_id].grant_approval(
                        action.request_id, action.ttl_seconds
                    )
                reply = None
                decisions.complete(reply)
            else:  # pragma: no cover - defensive
                raise TypeError(f"unknown controller decision {decision!r}")
    except Exception as exc:
        if resuming:
            raise
        # A broken harness must still leave evidence; the evaluator then reports at
        # best "inconclusive" because execution_status is "error".
        drive.close()
        journal()
        store.finish_run(
            "error",
            controller_state=controller.state.value,
            summary=f"harness error: {type(exc).__name__}: {exc}",
        )
