"""Versioned scenario data (JSON, never executable) and its loader.

``load_scenario`` validates the schema and every cross-reference before the
caller creates a run directory or database. Session 1 ships one scenario
(S01 happy-path); disruption scenarios are Session 2 work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, ValidationError

from agent_rehearsal.models import (
    BusinessOutcome,
    Currency,
    PositiveMinor,
    RecordId,
    Strict,
    SupplierStatus,
    Timestamp,
)
from agent_rehearsal.policy import KNOWN_POLICIES

ASSERTION_IDS = (
    "approval_valid_at_commit",
    "supplier_active_at_commit",
    "policy_current_at_commit",
    "one_po_per_intent",
    "budget_conserved",
    "reservation_state_consistent",
    "all_mutations_have_events",
    "expected_business_outcome",
    "unknown_outcome_reconciled",
)
AssertionId = Literal[
    "approval_valid_at_commit",
    "supplier_active_at_commit",
    "policy_current_at_commit",
    "one_po_per_intent",
    "budget_conserved",
    "reservation_state_consistent",
    "all_mutations_have_events",
    "expected_business_outcome",
    "unknown_outcome_reconciled",
]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")]
ReviewerId = Annotated[str, StringConstraints(pattern=r"^REV-[0-9]{3}$")]

DEFAULT_SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "scenarios"


class ScenarioError(ValueError):
    """Scenario input is invalid; no run world has been created."""


class SupplierFixture(Strict):
    id: RecordId
    name: str = Field(min_length=1, max_length=200)
    status: SupplierStatus


class BudgetFixture(Strict):
    id: RecordId
    currency: Currency
    total_minor: PositiveMinor


class RequestFixture(Strict):
    id: RecordId
    supplier_id: RecordId
    budget_id: RecordId
    amount_minor: PositiveMinor
    currency: Currency
    description: str = Field(min_length=1, max_length=500)


class InitialWorld(Strict):
    suppliers: list[SupplierFixture] = Field(min_length=1)
    budgets: list[BudgetFixture] = Field(min_length=1)
    requests: list[RequestFixture] = Field(min_length=1)


class Reviewer(Strict):
    id: ReviewerId
    display_name: str


class ReviewerAction(Strict):
    id: Annotated[str, StringConstraints(pattern=r"^RA-[0-9]{3}$")]
    trigger: Literal["on_awaiting_approval"]
    action: Literal["grant_approval"]
    reviewer_id: ReviewerId
    request_id: RecordId
    ttl_seconds: int = Field(gt=0)


class Task(Strict):
    request_id: RecordId


class Expected(Strict):
    business_outcome: BusinessOutcome
    purchase_orders: int = Field(ge=0)


class Fault(Strict):
    id: Slug
    effect: Literal["supplier_suspension", "response_loss", "approval_revocation"]
    boundary: Literal["before_purchase", "after_purchase_commit"]
    occurrence: int = Field(default=1, ge=1)
    max_firings: Literal[1] = 1


class Scenario(Strict):
    schema_version: Literal[1]
    id: Slug
    catalog_id: Annotated[str, StringConstraints(pattern=r"^S[0-9]{2}$")]
    version: Annotated[str, StringConstraints(pattern=r"^\d+\.\d+\.\d+$")]
    title: str
    description: str
    synthetic_data: Literal[True]
    policy_version: str
    clock_origin: Timestamp
    tick_seconds: int = Field(ge=1, le=3600)
    initial_world: InitialWorld
    task: Task
    reviewers: list[Reviewer]
    reviewer_actions: list[ReviewerAction]
    expected: Expected
    required_assertions: list[AssertionId] = Field(min_length=1)
    duplicate_po_attempt: bool = False
    faults: list[Fault] = Field(default_factory=list)


def _unique(ids: list[str], what: str) -> None:
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ScenarioError(f"duplicate {what} ids: {', '.join(dupes)}")


def validate_references(scenario: Scenario) -> None:
    world = scenario.initial_world
    policy = KNOWN_POLICIES.get(scenario.policy_version)
    if policy is None:
        raise ScenarioError(f"unknown policy_version {scenario.policy_version!r}")
    _unique([s.id for s in world.suppliers], "supplier")
    _unique([b.id for b in world.budgets], "budget")
    _unique([r.id for r in world.requests], "request")
    _unique([r.id for r in scenario.reviewers], "reviewer")
    _unique([a.id for a in scenario.reviewer_actions], "reviewer action")
    _unique(list(scenario.required_assertions), "required assertion")
    _unique([f.id for f in scenario.faults], "fault")
    for fault in scenario.faults:
        boundary = "after_purchase_commit" if fault.effect == "response_loss" else "before_purchase"
        if fault.boundary != boundary:
            raise ScenarioError(f"fault {fault.id}: effect requires {boundary}")
    suppliers = {s.id for s in world.suppliers}
    budgets = {b.id: b for b in world.budgets}
    requests = {r.id for r in world.requests}
    reviewers = {r.id for r in scenario.reviewers}
    for bud in world.budgets:
        if bud.currency != policy.currency:
            raise ScenarioError(f"budget {bud.id} currency differs from policy currency")
    for req in world.requests:
        if req.supplier_id not in suppliers:
            raise ScenarioError(f"request {req.id} references unknown supplier {req.supplier_id}")
        if req.budget_id not in budgets:
            raise ScenarioError(f"request {req.id} references unknown budget {req.budget_id}")
        if req.currency != budgets[req.budget_id].currency:
            raise ScenarioError(f"request {req.id} currency differs from its budget")
    if scenario.task.request_id not in requests:
        raise ScenarioError(f"task references unknown request {scenario.task.request_id}")
    for act in scenario.reviewer_actions:
        if act.reviewer_id not in reviewers:
            raise ScenarioError(f"reviewer action {act.id} names unknown reviewer")
        if act.request_id not in requests:
            raise ScenarioError(f"reviewer action {act.id} references unknown request")
        if act.ttl_seconds > policy.max_approval_ttl_seconds:
            raise ScenarioError(f"reviewer action {act.id} ttl exceeds policy maximum")


def parse_scenario(text: str, *, source: str = "<memory>") -> Scenario:
    try:
        scenario = Scenario.model_validate_json(text)
    except ValidationError as exc:
        first = exc.errors(include_url=False)[:5]
        details = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in first)
        raise ScenarioError(f"{source}: invalid scenario: {details}") from exc
    validate_references(scenario)
    return scenario


def load_scenario(scenario_id: str, scenarios_dir: Path = DEFAULT_SCENARIOS_DIR) -> Scenario:
    if not isinstance(scenario_id, str) or not scenario_id.replace("-", "").isalnum():
        raise ScenarioError(f"invalid scenario id {scenario_id!r}")
    path = scenarios_dir / f"{scenario_id}.json"
    if not path.is_file():
        raise ScenarioError(f"scenario not found: {path}")
    scenario = parse_scenario(path.read_text(encoding="utf-8"), source=str(path))
    if scenario.id != scenario_id:
        raise ScenarioError(f"{path}: id {scenario.id!r} does not match file name")
    return scenario


def list_scenarios(scenarios_dir: Path = DEFAULT_SCENARIOS_DIR) -> list[Scenario]:
    return [load_scenario(p.stem, scenarios_dir) for p in sorted(scenarios_dir.glob("*.json"))]
