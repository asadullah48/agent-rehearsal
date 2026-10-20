"""Command line interface.

Exit codes: 0 all required assertions pass; 1 evaluation failed; 2 invalid input,
harness error, inconclusive evaluation, or evidence that disagrees with itself.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from agent_rehearsal.adapters import load_scripted
from agent_rehearsal.controller import ADAPTER
from agent_rehearsal.evaluator import evaluate_run
from agent_rehearsal.evidence import EXPORT_POINTS, verify_ledger
from agent_rehearsal.playback import load_playback, render, verify_against_report
from agent_rehearsal.recovery import CRASH_POINTS
from agent_rehearsal.report import load_report
from agent_rehearsal.runner import (
    DB_NAME,
    REPORT_NAME,
    OutputDirectoryNotEmpty,
    execute_run,
    resume_run,
)
from agent_rehearsal.scenarios import (
    DEFAULT_SCENARIOS_DIR,
    ScenarioError,
    list_scenarios,
    load_scenario,
)

EXIT_PASS, EXIT_FAIL, EXIT_INVALID = 0, 1, 2
VERDICT_EXIT = {"pass": EXIT_PASS, "fail": EXIT_FAIL, "inconclusive": EXIT_INVALID}
LABEL = f"Controller: {ADAPTER['label']}"


def _err(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return EXIT_INVALID


def _print_assertions(assertions: list[dict[str, object]]) -> None:
    for a in assertions:
        refs = ",".join(a["evidence_event_ids"]) or "-"  # type: ignore[arg-type]
        print(f"  [{str(a['status']).upper():>14}] {a['id']}: {a['observed']}  (evidence: {refs})")


def cmd_scenarios_list(args: argparse.Namespace) -> int:
    try:
        scenarios = list_scenarios(args.scenarios_dir)
    except ScenarioError as exc:
        return _err(str(exc))
    print(LABEL)
    for s in scenarios:
        print(
            f"{s.id:<16} {s.catalog_id}  v{s.version}  expected={s.expected.business_outcome}"
            f"  {s.title}"
        )
    return EXIT_PASS


def cmd_run(args: argparse.Namespace) -> int:
    if (args.adapter == "scripted") != (args.script is not None):
        return _err("--adapter scripted requires --script; reference does not accept --script")
    try:
        controller = load_scripted(args.script) if args.script is not None else None
    except (OSError, ValueError) as exc:
        return _err(str(exc))
    try:
        scenario = load_scenario(args.scenario, args.scenarios_dir)  # before any world exists
    except ScenarioError as exc:
        return _err(str(exc))
    output: Path = args.output
    try:
        if output.exists() and (not output.is_dir() or any(output.iterdir())):
            raise OutputDirectoryNotEmpty(f"refusing to write into nonempty path: {output}")
        result = execute_run(
            scenario,
            args.seed,
            output,
            crash_at=args.crash_at,
            crash_during_export=args.crash_during_export,
            controller=controller,
        )
    except OutputDirectoryNotEmpty as exc:
        return _err(str(exc))
    report = result.report
    print(f"Controller: {report['adapter']['label']}")
    print(f"Scenario: {scenario.id} ({scenario.catalog_id}) seed={args.seed}")
    print(f"Run directory: {output}")
    print(f"Execution status: {report['execution_status']}")
    print(f"Business outcome: {report['business_outcome']}")
    print(f"Evaluation verdict: {report['verdict']}")
    _print_assertions(report["assertions"])
    m = report["measurements"]
    print(
        f"Measurements: proposed_actions={m['proposed_actions']} model_calls={m['model_calls']} "
        f"simulated_elapsed_seconds={m['simulated_elapsed_seconds']} "
        f"wall_clock_ms={m['wall_clock_ms']} (wall clock is measurement only)"
    )
    if report["execution_status"] == "error":
        return EXIT_INVALID
    return VERDICT_EXIT[report["verdict"]]


def cmd_resume(args: argparse.Namespace) -> int:
    try:
        result = resume_run(args.run_dir, crash_during_export=args.crash_during_export)
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        return _err(str(exc))
    print(f"Controller: {result.report['adapter']['label']}")
    print(f"Business outcome: {result.report['business_outcome']}")
    print(f"Evaluation verdict: {result.evaluation.verdict}")
    return VERDICT_EXIT[result.evaluation.verdict]


def cmd_replay(args: argparse.Namespace) -> int:
    run_dir: Path = args.run_dir
    try:
        entries = load_playback(run_dir / DB_NAME)
    except FileNotFoundError as exc:
        return _err(str(exc))
    print("RECORDED PLAYBACK - renders saved events and recorded responses only.")
    print(
        "No tool, controller, or model is invoked. This is not crash recovery or durable execution."
    )
    print(render(entries))
    report_path = run_dir / REPORT_NAME
    if not report_path.is_file():
        return _err(f"{report_path} missing; playback could not be cross-checked")
    problems = verify_against_report(entries, load_report(report_path))
    if problems:
        for p in problems:
            print(f"MISMATCH: {p}", file=sys.stderr)
        return EXIT_INVALID
    calls = sum(1 for e in entries if e.kind == "tool_call")
    events = sum(1 for e in entries if e.kind == "event")
    print(f"Playback complete: {calls} recorded tool calls, {events} events; matches report.json.")
    return EXIT_PASS


def cmd_evaluate(args: argparse.Namespace) -> int:
    run_dir: Path = args.run_dir
    try:
        fresh = evaluate_run(run_dir / DB_NAME)
        stored = load_report(run_dir / REPORT_NAME)
    except (FileNotFoundError, ValueError) as exc:
        return _err(str(exc))
    fresh_doc = fresh.to_dict()
    print("Fresh evaluation from persisted evidence (controller not invoked).")
    print(f"Execution status: {fresh.execution_status}")
    print(f"Business outcome: {fresh.business_outcome}")
    print(f"Evaluation verdict: {fresh.verdict}")
    _print_assertions(fresh_doc["assertions"])
    event_ids = {
        e.record["event_id"] for e in load_playback(run_dir / DB_NAME) if e.kind == "event"
    }
    unresolved = sorted(
        {ref for a in fresh_doc["assertions"] for ref in a["evidence_event_ids"]} - event_ids
    )
    stored_statuses = {a["id"]: a["status"] for a in stored["assertions"]}
    fresh_statuses = {a["id"]: a["status"] for a in fresh_doc["assertions"]}
    agrees = stored["verdict"] == fresh.verdict and stored_statuses == fresh_statuses
    print(f"Recorded verdict: {stored['verdict']}; agreement: {'yes' if agrees else 'NO'}")
    if unresolved:
        return _err(f"evidence references do not resolve: {', '.join(unresolved)}")
    if fresh.verdict == "fail":
        if not agrees:
            print(
                "warning: report.json recorded a different result; persisted evidence fails now",
                file=sys.stderr,
            )
        return EXIT_FAIL
    if not agrees:
        return _err("fresh evaluation disagrees with report.json")
    return VERDICT_EXIT[fresh.verdict]


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        anchor = json.loads((args.run_dir / "evidence-anchor.json").read_text(encoding="utf-8"))
        head = args.expected_head if args.expected_head is not None else anchor["head"]
        count = args.expected_count if args.expected_count is not None else anchor["count"]
        if not isinstance(head, str) or len(head) != 64 or not isinstance(count, int) or count < 0:
            return _err("invalid evidence anchor")
        ok, index, reason = verify_ledger(args.run_dir / "evidence.jsonl", head, count)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return _err(str(exc))
    if ok:
        head = str(index - 1) if index else "none"
        print(f"PASS: {index} records verified (head index {head})")
    else:
        print(f"FAIL: {reason}; zero-based record index {index}")
    print("Tamper-evident (not tamper-proof); retain the final digest separately.")
    return EXIT_PASS if ok else EXIT_FAIL


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent_rehearsal",
        description="Offline procurement simulator with a deterministic reference controller "
        "and an independent evaluator. Synthetic data only.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    from agent_rehearsal.live_eval import (
        DEFAULT_LEDGERS_DIR,
        DEFAULT_PRICE,
        cmd_live_eval,
        cmd_live_spend,
    )

    live = sub.add_parser("live-eval", help="manual opt-in live evaluation; estimates by default")
    live.add_argument("--trials", type=int, required=True)
    live.add_argument("--budget-usd", required=True)
    live.add_argument("--approved", action="store_true", help="explicit approval of displayed cap")
    live.add_argument("--pricing", type=Path, default=DEFAULT_PRICE)
    live.add_argument(
        "--ledger",
        type=Path,
        help="budget ledger for this evaluation (cap locked once set); default: a new one",
    )
    live.add_argument("--ledgers-dir", type=Path, default=DEFAULT_LEDGERS_DIR)
    live.add_argument("--output", type=Path, default=Path(".rehearsal/live-eval"))
    live.set_defaults(func=cmd_live_eval)

    spend = sub.add_parser("live-spend", help="read-only total spend across all budget ledgers")
    spend.add_argument("--ledgers-dir", type=Path, default=DEFAULT_LEDGERS_DIR)
    spend.add_argument("--ledger", type=Path, action="append", help="extra ledger to include")
    spend.set_defaults(func=cmd_live_spend)

    scen = sub.add_parser("scenarios", help="scenario catalog")
    scen_sub = scen.add_subparsers(dest="scenarios_command", required=True)
    lst = scen_sub.add_parser("list", help="list available scenarios")
    lst.add_argument("--scenarios-dir", type=Path, default=DEFAULT_SCENARIOS_DIR)
    lst.set_defaults(func=cmd_scenarios_list)

    run = sub.add_parser("run", help="deterministic run of a scenario")
    run.add_argument("--scenario", required=True)
    run.add_argument("--seed", type=int, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--scenarios-dir", type=Path, default=DEFAULT_SCENARIOS_DIR)
    run.add_argument("--crash-at", choices=CRASH_POINTS)
    run.add_argument("--crash-during-export", choices=EXPORT_POINTS)
    run.add_argument("--adapter", choices=("reference", "scripted"), default="reference")
    run.add_argument("--script", type=Path, help="offline JSON decision script")
    run.set_defaults(func=cmd_run)
    resume = sub.add_parser("resume", help="recover an interrupted local run")
    resume.add_argument("--run-dir", type=Path, required=True)
    resume.add_argument("--crash-during-export", choices=EXPORT_POINTS)
    resume.set_defaults(func=cmd_resume)

    rep = sub.add_parser("replay", help="recorded playback of a finished run")
    rep.add_argument("--run-dir", type=Path, required=True)
    rep.set_defaults(func=cmd_replay)

    ev = sub.add_parser("evaluate", help="re-evaluate a finished run from persisted evidence")
    ev.add_argument("--run-dir", type=Path, required=True)
    ev.set_defaults(func=cmd_evaluate)
    verify = sub.add_parser("verify", help="verify the tamper-evident event chain")
    verify.add_argument("--run-dir", type=Path, required=True)
    verify.add_argument("--expected-head", help="separately retained final SHA-256")
    verify.add_argument("--expected-count", type=int, help="separately retained record count")
    verify.set_defaults(func=cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse uses 2 for usage errors, matching our contract
        return int(exc.code) if isinstance(exc.code, int) else EXIT_INVALID
    return int(args.func(args))


if __name__ == "__main__":  # `python -m agent_rehearsal.cli` previously exited silently
    raise SystemExit(main())
