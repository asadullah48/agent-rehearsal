"""Basic JSON evidence report (Session 1).

Session 2 adds the full evidence bundle, Markdown summary, manifest hashes and a
hash-chained event ledger. Hash chaining will be tamper-evident relative to a
trusted digest; it is not tamper-proof.
"""

from __future__ import annotations

import copy
import json
import platform
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from agent_rehearsal import SCHEMA_VERSION, __version__
from agent_rehearsal.canonical import parse_ts
from agent_rehearsal.evaluator import EVALUATOR_VERSION, Evaluation
from agent_rehearsal.models import Event, RecordedToolCall, WorkflowTransition

REPORT_KIND = "agent-rehearsal.run-report"
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Fields removed before comparing two runs semantically. Everything else -
# action order, arguments, responses, events, simulated times, assertions and
# outcomes - must match exactly.
SEMANTIC_EXCLUSIONS = (
    "metadata.run_id (random per run)",
    "metadata.source_revision (depends on git state, not on behaviour)",
    "metadata.python_version (environment detail)",
    "measurements.wall_clock_ms (wall-clock measurement only)",
    "events[].run_id (copy of the random run id)",
)

EXECUTION_MODES = {
    "recorded_playback": "`replay` renders saved events and recorded responses only; it calls "
    "no tool, controller or model. It is not crash recovery or durable execution.",
    "deterministic_rerun": "`run` again with the same scenario and seed starts a new world from "
    "the same fixture; its canonical semantic trace should equal this one.",
    "live_rerun": "live-eval is manual and opt-in; a rerun spends budget on new decisions. "
    "Live resume only replays persisted accepted decisions and refuses an unsaved frontier.",
}


def _source_revision() -> str:
    if not (PROJECT_ROOT / ".git").exists():
        return "unknown"
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and len(sha) == 40 else "unknown"


def _rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql).fetchall()]


def build_report(db_path: Path, evaluation: Evaluation, *, wall_clock_ms: float) -> dict[str, Any]:
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        meta = {r["key"]: r["value"] for r in _rows(conn, "SELECT key, value FROM run_meta")}
        scenario = json.loads(meta["scenario_json"])
        adapter = json.loads(meta["adapter_json"])
        policy = json.loads(meta["policy_json"])
        tool_calls = [
            RecordedToolCall(
                seq=c["seq"],
                call_id=c["call_id"],
                actor_id=c["actor_id"],
                actor_role=c["actor_role"],
                tool=c["tool"],
                arguments=json.loads(c["arguments_json"]),
                operation_key=c["operation_key"],
                status=c["status"],
                response=json.loads(c["response_json"]),
                simulated_at=c["simulated_at"],
            ).model_dump(mode="json")
            for c in _rows(conn, "SELECT * FROM tool_calls ORDER BY seq")
        ]
        events = [
            Event(
                seq=e["seq"],
                event_id=e["event_id"],
                run_id=e["run_id"],
                simulated_at=e["simulated_at"],
                actor_id=e["actor_id"],
                actor_role=e["actor_role"],
                event_type=e["event_type"],
                payload=json.loads(e["payload_json"]),
                causation_id=e["causation_id"],
                policy_version=e["policy_version"],
                operation_key=e["operation_key"],
            ).model_dump(mode="json")
            for e in _rows(conn, "SELECT * FROM events ORDER BY seq")
        ]
        transitions = [
            WorkflowTransition(**t).model_dump(mode="json")
            for t in _rows(conn, "SELECT * FROM workflow_transitions ORDER BY seq")
        ]
        final_state = {
            table: _rows(conn, f"SELECT * FROM {table} ORDER BY id")
            for table in (
                "suppliers",
                "budgets",
                "requests",
                "reservations",
                "approvals",
                "purchase_orders",
            )
        }
    finally:
        conn.close()

    completed = next((e for e in events if e["event_type"] == "run.completed"), None)
    controller = (
        {
            "final_state": completed["payload"]["controller_final_state"],
            "summary": completed["payload"]["controller_summary"],
        }
        if completed
        else {"final_state": "unknown", "summary": "run.completed event missing"}
    )
    start = parse_ts(meta["clock_origin"])
    end = parse_ts(meta["now"])
    return {
        "report_kind": REPORT_KIND,
        "schema_version": SCHEMA_VERSION,
        "synthetic_data_notice": "All suppliers, people, budgets and requests are fictional. "
        "Policy demo-procurement-v1 is a demo rule set, not any real organisation's policy.",
        "metadata": {
            "run_id": meta["run_id"],
            "scenario": {
                "id": scenario["id"],
                "catalog_id": scenario["catalog_id"],
                "version": scenario["version"],
                "title": scenario["title"],
            },
            "seed": int(meta["seed"]),
            "clock_origin": meta["clock_origin"],
            "package_version": __version__,
            "evaluator_version": EVALUATOR_VERSION,
            "source_revision": _source_revision(),
            "python_version": platform.python_version(),
        },
        "adapter": adapter,
        "hashes": {
            "algorithm": "sha256 over canonical JSON (sorted keys, compact separators)",
            "scenario": meta["scenario_hash"],
            "initial_world": meta["initial_world_hash"],
            "policy": meta["policy_hash"],
        },
        "policy": {"policy_version": policy["policy_version"], "currency": policy["currency"]},
        "execution_status": evaluation.execution_status,
        "business_outcome": evaluation.business_outcome,
        "verdict": evaluation.verdict,
        "controller": controller,
        "limits": {
            "max_proposed_actions": policy["max_proposed_actions"],
            "max_attempts_per_operation": policy["max_attempts_per_operation"],
            "limit_reached": completed["payload"].get("limit_reached") if completed else None,
        },
        "measurements": {
            "wall_clock_ms": wall_clock_ms,
            "simulated_started_at": meta["clock_origin"],
            "simulated_finished_at": meta["now"],
            "simulated_elapsed_seconds": int((end - start).total_seconds()),
            "proposed_actions": sum(1 for c in tool_calls if c["actor_role"] == "controller"),
            "model_calls": json.loads(meta.get("live_cost", "{}")).get("calls", 0),
            "provider_token_usage": [
                e["payload"]["usage"]
                for e in events
                if e["event_type"] == "model.response_received"
            ]
            if adapter.get("live_model")
            else "not_applicable",
            "estimated_model_cost": (
                json.loads(meta.get("live_cost", "{}"))
                if adapter.get("live_model")
                else "not_applicable (offline deterministic controller)"
            ),
        },
        "counts": evaluation.counts,
        "evaluation": evaluation.to_dict(),
        "assertions": [a.model_dump(mode="json") for a in evaluation.assertions],
        "tool_calls": tool_calls,
        "events": events,
        "workflow_transitions": transitions,
        "final_state": final_state,
        "execution_modes": EXECUTION_MODES,
        "semantic_comparison_exclusions": list(SEMANTIC_EXCLUSIONS),
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite report: {path}")
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_report(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("report_kind") != REPORT_KIND:
        raise ValueError(f"{path} is not an Agent Rehearsal run report")
    return data


def canonical_semantic_trace(report: dict[str, Any]) -> dict[str, Any]:
    """Normalize a report for same-seed comparison (see SEMANTIC_EXCLUSIONS)."""
    doc = copy.deepcopy(report)
    for key in ("run_id", "source_revision", "python_version"):
        doc["metadata"].pop(key, None)
    doc["measurements"].pop("wall_clock_ms", None)
    for event in doc["events"]:
        event.pop("run_id", None)
    return doc
