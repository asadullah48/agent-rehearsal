"""Decision-stream boundary. Both shipped implementations are fully offline.

This interface is not a security sandbox for third-party Python code. A future
live adapter must be opt-in and must not gain store or reviewer capabilities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import Field

from agent_rehearsal.canonical import sha256_hex
from agent_rehearsal.controller import (
    TRANSITIONS,
    AwaitReview,
    Drive,
    Finished,
    ToolProposal,
    Transition,
    WorkflowState,
)
from agent_rehearsal.models import Strict


@runtime_checkable
class Controller(Protocol):
    state: WorkflowState
    transitions: list[Transition]

    @property
    def metadata(self) -> dict[str, Any]: ...

    @property
    def configuration(self) -> dict[str, Any] | None: ...

    def drive(self) -> Drive: ...


class ToolStep(Strict):
    kind: Literal["tool"]
    tool: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any]
    state_after: WorkflowState | None = None


class ReviewStep(Strict):
    kind: Literal["review"]
    request_id: str


class DecisionScript(Strict):
    schema_version: Literal[1]
    decisions: list[ToolStep | ReviewStep] = Field(min_length=1, max_length=30)
    final_state: Literal["FULFILLED", "REFUSED", "ESCALATED"]


def parse_script(text: str) -> DecisionScript:
    script = DecisionScript.model_validate_json(text)
    state = WorkflowState.NEW
    for step in script.decisions:
        after = step.state_after if isinstance(step, ToolStep) else None
        if after is not None and after != state:
            if after not in TRANSITIONS[state]:
                raise ValueError(f"invalid scripted state transition {state} -> {after}")
            state = after
    final = WorkflowState(script.final_state)
    if final != state and final not in TRANSITIONS[state]:
        raise ValueError(f"invalid scripted terminal transition {state} -> {final}")
    return script


@dataclass
class ScriptedController:
    script: DecisionScript
    state: WorkflowState = WorkflowState.NEW
    transitions: list[Transition] = field(default_factory=list)

    @property
    def configuration(self) -> dict[str, Any]:
        return self.script.model_dump(mode="json")

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "id": "scripted-offline",
            "version": "0.1.0",
            "kind": "scripted",
            "live_model": False,
            "label": "Scripted offline decision replay (not a live AI agent; no model calls)",
            "decisions_hash": sha256_hex(self.configuration),
        }

    def _to(self, state: WorkflowState) -> None:
        if state != self.state:
            self.transitions.append(Transition(self.state, state, "scripted state claim"))
            self.state = state

    def drive(self) -> Drive:
        for step in self.script.decisions:
            if isinstance(step, ReviewStep):
                yield AwaitReview(step.request_id)
            else:
                yield ToolProposal(step.tool, step.arguments)
                if step.state_after is not None:
                    self._to(step.state_after)
        self._to(WorkflowState(self.script.final_state))
        return Finished(self.state, "scripted offline decision replay exhausted")


def load_scripted(path: Path) -> ScriptedController:
    return ScriptedController(parse_script(path.read_text(encoding="utf-8")))
