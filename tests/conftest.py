"""Shared fixtures. Every test runs offline: network and .env access raise."""

from __future__ import annotations

import builtins
import io
import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agent_rehearsal.controller import ADAPTER
from agent_rehearsal.models import Actor
from agent_rehearsal.policy import POLICY
from agent_rehearsal.privileged import ReviewerConsole, ScenarioConsole
from agent_rehearsal.runner import CONTROLLER_ACTOR, RunResult, execute_run
from agent_rehearsal.scenarios import Scenario, load_scenario, parse_scenario
from agent_rehearsal.store import HARNESS, Store
from agent_rehearsal.tools import ToolGateway

WORLD_TABLES = (
    "suppliers",
    "budgets",
    "requests",
    "reservations",
    "approvals",
    "purchase_orders",
    "idempotency",
    "events",
)


@pytest.fixture(autouse=True)
def offline_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access attempted during tests")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    real_open = io.open

    def guarded_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        mode = str(args[0] if args else kwargs.get("mode", "r"))
        reading = not any(flag in mode for flag in "wax")
        if reading and isinstance(file, str | bytes | os.PathLike):
            name = os.path.basename(os.fsdecode(file))
            if name == ".env" or name.startswith(".env."):
                raise AssertionError(f"attempted to read credentials file {file!r}")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(io, "open", guarded_open)
    monkeypatch.setattr(builtins, "open", guarded_open)


@pytest.fixture
def scenario() -> Scenario:
    return load_scenario("happy-path")


def scenario_doc() -> dict[str, Any]:
    return load_scenario("happy-path").model_dump(mode="json")


def variant(mutate: Any) -> Scenario:
    """Build an in-memory scenario variant (validated). Not a scenario file."""
    doc = scenario_doc()
    mutate(doc)
    return parse_scenario(json.dumps(doc))


@dataclass
class World:
    store: Store
    gateway: ToolGateway
    reviewer: ReviewerConsole
    harness: ScenarioConsole
    path: Path


def make_world(path: Path, scenario: Scenario, seed: int = 42) -> World:
    store = Store.create(
        path, run_id="run-test", scenario=scenario, policy=POLICY, seed=seed, adapter=ADAPTER
    )
    return World(
        store=store,
        gateway=ToolGateway(store, CONTROLLER_ACTOR),
        reviewer=ReviewerConsole(store, Actor(actor_id="REV-001", role="reviewer")),
        harness=ScenarioConsole(store, HARNESS),
        path=path,
    )


@pytest.fixture
def world(tmp_path: Path, scenario: Scenario) -> World:
    w = make_world(tmp_path / "world.sqlite", scenario)
    yield w
    w.store.close()


def snapshot(store: Store) -> dict[str, list[dict[str, Any]]]:
    return {t: store.rows(f"SELECT * FROM {t} ORDER BY rowid") for t in WORLD_TABLES}


def reserve(world: World, key: str = "reserve-key-0001", amount: int = 125000) -> Any:
    return world.gateway.call(
        "reserve_budget", {"request_id": "REQ-001", "amount_minor": amount, "operation_key": key}
    )


def commit(world: World, reservation_id: str, approval_id: str, key: str = "commit-key-0001"):
    return world.gateway.call(
        "create_purchase_order",
        {
            "request_id": "REQ-001",
            "reservation_id": reservation_id,
            "approval_id": approval_id,
            "operation_key": key,
        },
    )


@pytest.fixture
def completed_run(tmp_path: Path, scenario: Scenario) -> RunResult:
    return execute_run(scenario, 42, tmp_path / "run")
