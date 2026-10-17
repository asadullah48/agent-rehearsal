"""Generate a file-only, read-only viewer from freshly executed offline fixtures."""

from __future__ import annotations

import argparse
import json
from html import escape
from pathlib import Path

from agent_rehearsal.adapters import load_scripted
from agent_rehearsal.runner import execute_run, prepare_output_dir
from agent_rehearsal.scenarios import DEFAULT_SCENARIOS_DIR, list_scenarios, load_scenario

SCRIPTED_LABEL = "scripted offline agent (replayed decisions, no model calls)"


def controller_label(report: dict) -> str:
    return (
        SCRIPTED_LABEL
        if report["adapter"]["id"] == "scripted-offline"
        else report["adapter"]["label"]
    )


STYLE = """*{box-sizing:border-box}body{margin:auto;padding:16px;max-width:960px;
font:16px/1.6 system-ui;background:#101827;color:#ecf2ff;overflow-wrap:anywhere}
a{color:#9bdcff}nav{display:flex;flex-wrap:wrap;gap:12px}article,details{
border:1px solid #718096;border-radius:8px;padding:12px;margin:12px 0;min-width:0}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px}summary{cursor:pointer}
ol{padding-left:24px}li{padding:8px 0}h1{font-size:clamp(24px,6vw,36px)}
"""


def _page(title: str, body: str) -> str:
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{STYLE}</style><body>"
        f"<h1>{escape(title)}</h1>{body}</body></html>"
    )


def _json(value: object) -> str:
    return escape(json.dumps(value, indent=2, ensure_ascii=True))


def generate_demo(output: Path) -> None:
    """Refuse overwrite; keep complete original bundles beside their static views."""
    prepare_output_dir(output)
    links = []
    cases = [(s.id, s, None) for s in list_scenarios()]
    for slug, script in (
        ("false-success", "false-success.json"),
        ("stale-approval", "revoked-approval.json"),
    ):
        cases.append(
            (
                slug,
                load_scenario("approval-revoked"),
                load_scripted(DEFAULT_SCENARIOS_DIR.parent / "scripts" / script),
            )
        )
    for slug, scenario, controller in cases:
        result = execute_run(scenario, 42, output / "runs" / slug, controller=controller)
        report = result.report
        links.append(f'<a href="{slug}.html">{escape(scenario.title)} - {slug}</a>')
        body = '<nav><a href="index.html">All recorded cases</a></nav>'
        body += "<p>Predefined case, already executed. This page cannot change a run.</p>"
        body += f"<p>{escape(controller_label(report))}. Synthetic data only.</p>"
        body += (
            f"<p>Execution: {escape(report['execution_status'])}; "
            f"business outcome: {escape(report['business_outcome'])}; "
            f"independent verdict: {escape(report['verdict'])}.</p>"
        )
        body += "<h2>Assertions</h2>"
        for assertion in report["assertions"]:
            body += f"<article><pre>{_json(assertion)}</pre></article>"
        body += "<h2>Recorded event timeline</h2><ol>"
        for event in report["events"]:
            body += (
                f"<li><details><summary>{escape(event['event_id'])}: "
                f"{escape(event['event_type'])} · {escape(event['simulated_at'])}"
                f"</summary><pre>{_json(event)}</pre></details></li>"
            )
        body += "</ol><h2>Recorded tool calls</h2>"
        for call in report["tool_calls"]:
            body += (
                f"<details><summary>{escape(call['tool'])} · {escape(call['status'])}"
                f"</summary><pre>{_json(call)}</pre></details>"
            )
        body += "<h2>Original evidence</h2><nav>"
        for name in (
            "report.json",
            "summary.md",
            "run.sqlite",
            "evidence.jsonl",
            "evidence-anchor.json",
            "manifest.json",
        ):
            body += f'<a href="runs/{slug}/{name}">{name}</a>'
        body += "</nav><p>tamper-evident (not tamper-proof). Keep a trusted digest separately.</p>"
        (output / f"{slug}.html").write_text(
            _page(f"{slug}: {scenario.title}", body), encoding="utf-8"
        )
    body = (
        "<p>Offline procurement rehearsal · synthetic fixtures · seed 42.</p>"
        "<p>deterministic reference controller (not an AI agent).</p>"
        "<p>Select a predefined, already-executed case. Selection only opens saved evidence.</p>"
        f"<nav>{' '.join(links)}</nav>"
        "<p>Execution status, business outcome and independent verdict are distinct. "
        "A blocked purchase can be the correct outcome.</p>"
        "<p>Evidence is tamper-evident (not tamper-proof). Local verification does not "
        "authenticate provenance. These are local synthetic observations.</p>"
    )
    (output / "index.html").write_text(
        _page("Agent Rehearsal — recorded demo", body), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    generate_demo(args.output)
    print("Generated static demo and seven offline evidence bundles (one intentional FAIL).")


if __name__ == "__main__":
    main()
