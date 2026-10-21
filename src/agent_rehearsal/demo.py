"""Generate a file-only, read-only viewer from real offline fixture runs."""

from __future__ import annotations

import argparse
import json
import shutil
from html import escape
from pathlib import Path

from agent_rehearsal.adapters import load_scripted
from agent_rehearsal.runner import execute_run, prepare_output_dir
from agent_rehearsal.scenarios import DEFAULT_SCENARIOS_DIR, list_scenarios, load_scenario

SCRIPTED_LABEL = "scripted offline agent (replayed decisions, no model calls)"
STYLE = """*{box-sizing:border-box}html{color-scheme:dark}body{margin:auto;padding:16px;
max-width:1040px;font:16px/1.6 system-ui;background:#101827;color:#ecf2ff;
overflow-wrap:anywhere}a{color:#9bdcff;text-underline-offset:3px}
a:focus-visible,summary:focus-visible{outline:3px solid #ffd980;outline-offset:4px}
:target{outline:3px solid #ffd980;outline-offset:4px;scroll-margin-top:16px}
nav{display:flex;flex-wrap:wrap;gap:12px}.cards{display:grid;
grid-template-columns:repeat(auto-fit,minmax(min(100%,290px),1fr));gap:16px}
article,details{border:1px solid #718096;border-radius:8px;padding:12px;
margin:12px 0;min-width:0}.cards article{margin:0;background:#182438}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px;max-width:100%}
summary{cursor:pointer;padding:4px}ol{padding-left:24px}li{padding:8px 0;min-width:0}
h1{font-size:clamp(24px,6vw,36px)}h2{margin-top:32px}h3{font-size:19px}
dt{font-weight:700}dd{margin:0 0 10px}.badge{display:inline-block;border:1px solid;
padding:2px 9px;border-radius:6px;font-weight:800;letter-spacing:.04em}
.pass{color:#b7f7cd;background:#163e2b}.fail{color:#ffd2d2;background:#571f2c}
.not_applicable,.inconclusive{color:#ffedb5;background:#413516}
.notice{border-left:4px solid #9bdcff;padding-left:12px}footer{margin-top:32px}
"""


def controller_label(report: dict) -> str:
    if report["adapter"]["id"] == "scripted-offline":
        return SCRIPTED_LABEL
    return report["adapter"]["label"]


def _page(title: str, body: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{STYLE}</style></head><body>"
        '<a href="#main">Skip to content</a>'
        f'<header><h1>{escape(title)}</h1></header><main id="main">{body}</main>'
        "<footer><p>Evidence is tamper-evident (not tamper-proof). Keep a trusted digest "
        "separately. Local verification does not authenticate provenance.</p>"
        "</footer></body></html>"
    )


def _json(value: object) -> str:
    return escape(json.dumps(value, indent=2, ensure_ascii=True))


def _badge(status: str) -> str:
    return f'<span class="badge {escape(status)}">{escape(status.upper())}</span>'


def _field(name: str, value: object, *, badge: bool = False) -> str:
    content = _badge(str(value)) if badge else escape(str(value))
    return f'<dt>{escape(name)}</dt><dd data-field="{escape(name)}">{content}</dd>'


def _facts(report: dict) -> str:
    scenario = report["metadata"]["scenario"]
    return (
        "<dl>"
        + _field("Scenario", f"{scenario['catalog_id']} / {scenario['id']}")
        + _field("Controller", controller_label(report))
        + _field("Execution status", report["execution_status"])
        + _field("Business outcome", report["business_outcome"])
        + _field("Evaluation verdict", report["verdict"], badge=True)
        + "</dl>"
    )


def _outcome_note(report: dict) -> str:
    if report["verdict"] == "fail":
        return (
            "Evaluation FAIL: persisted world evidence contradicts the controller's success claim."
        )
    if report["business_outcome"] == "refused":
        return "Expected refusal - evaluation PASS. Blocking this purchase is the correct outcome."
    return (
        "Evaluation PASS: independently evaluated synthetic evidence matches the expected outcome."
    )


def render_case(slug: str, report: dict, summary: str) -> str:
    """Render saved values only; never execute proposals from the browser."""
    body = '<nav aria-label="Cases"><a href="index.html">All recorded cases</a></nav>'
    body += (
        "<p>Predefined case, already executed. This page cannot change a run. "
        "Synthetic data only.</p>"
    )
    body += f"<p>{escape(summary)}</p>{_facts(report)}"
    body += f'<p class="notice">{escape(_outcome_note(report))}</p>'
    blocked = [
        c
        for c in report["tool_calls"]
        if c["tool"] == "create_purchase_order"
        and c["status"] == "error"
        and c["response"]["error"]["certainty"] == "not_committed"
    ]
    body += "<dl>" + _field("PO count", len(report["final_state"]["purchase_orders"]))
    body += _field("Blocked attempt count", len(blocked))
    body += _field("Controller final state claim", report["controller"]["final_state"])
    body += _field("Recorded adapter label", report["adapter"]["label"]) + "</dl>"
    body += (
        "<h2>Blocked purchase attempts</h2>"
        "<p>Rejected purchase commits confirmed not committed.</p>"
    )
    for call in blocked:
        reason = call["response"]["error"]["reason_code"]
        body += (
            f"<details><summary>BLOCKED: {escape(call['call_id'])} - "
            f"{escape(reason)}</summary><pre>{_json(call)}</pre></details>"
        )
    if not blocked:
        body += "<p>None recorded.</p>"
    body += '<section aria-labelledby="assertions"><h2 id="assertions">Assertions</h2>'
    for assertion in report["assertions"]:
        body += f"<article><h3>{escape(assertion['id'])}</h3>{_badge(assertion['status'])}"
        refs = " ".join(
            f'<a class="evidence" href="#event-{escape(ref)}">{escape(ref)}</a>'
            for ref in assertion["evidence_event_ids"]
        )
        body += f"<p>Evidence events: {refs or 'None recorded'}</p>"
        body += (
            f"<details><summary>Assertion JSON</summary><pre>{_json(assertion)}</pre>"
            "</details></article>"
        )
    body += (
        '</section><section aria-labelledby="timeline">'
        '<h2 id="timeline">Recorded event timeline</h2><ol>'
    )
    for event in report["events"]:
        body += (
            f'<li id="event-{escape(event["event_id"])}"><details><summary>'
            f"{escape(event['event_id'])}: {escape(event['event_type'])} - "
            f"{escape(event['simulated_at'])}</summary><pre>{_json(event)}</pre></details></li>"
        )
    body += (
        '</ol></section><section aria-labelledby="tools"><h2 id="tools">Recorded tool calls</h2>'
    )
    for call in report["tool_calls"]:
        body += (
            f"<details><summary>{escape(call['tool'])} - {escape(call['status'])}</summary>"
            f"<pre>{_json(call)}</pre></details>"
        )
    body += '</section><h2>Original evidence</h2><nav aria-label="Evidence downloads">'
    for name in (
        "report.json",
        "summary.md",
        "run.sqlite",
        "evidence.jsonl",
        "evidence-anchor.json",
        "manifest.json",
    ):
        body += f'<a href="runs/{escape(slug)}/{name}">{name}</a>'
    body += "</nav>"
    return _page(f"{slug}: {report['metadata']['scenario']['title']}", body)


def render_index(cases: list[tuple[str, dict, str]]) -> str:
    body = (
        '<p class="notice">Seven recorded runs, five synthetic fixtures, zero model calls. '
        "See the evaluator catch a false success claim, then explore correct refusals "
        "and recovery from disruptions.</p>"
        "<p>Offline procurement rehearsal - synthetic fixtures - seed 42.</p>"
        "<p>deterministic reference controller (not an AI agent).</p>"
        f"<p>Scripted cases use a {SCRIPTED_LABEL}.</p>"
        "<p>Select a predefined, already-executed case. Selection only opens saved evidence.</p>"
        "<p>Execution status, business outcome and independent verdict are distinct. "
        "Expected refusal with PASS is different from evaluation FAIL.</p>"
        '<p><a href="live-model-evaluation.html">Live model evaluation</a> '
        "(separate from these offline runs).</p>"
    )
    for scripted, heading in (
        (True, "Evaluator catches a bad agent"),
        (False, "Controller handles disruptions"),
    ):
        body += f'<section><h2>{heading}</h2><div class="cards">'
        for slug, report, summary in cases:
            if (report["adapter"]["id"] == "scripted-offline") != scripted:
                continue
            body += (
                f'<article data-case="{escape(slug)}"><h3><a href="{escape(slug)}.html">'
                f"{escape(slug)}</a></h3><p>{escape(summary)}</p>{_facts(report)}"
                f"<p>{escape(_outcome_note(report))}</p></article>"
            )
        body += "</div></section>"
    return _page("Agent Rehearsal - recorded demo", body)


def generate_demo(output: Path, live_report: Path | None = None) -> None:
    """Refuse overwrite; keep complete original bundles beside their static views."""
    prepare_output_dir(output)
    cases = [(s.id, s, None, s.description) for s in list_scenarios()]
    for slug, script, summary in (
        (
            "false-success",
            "false-success.json",
            "Revoked approval blocks purchase; replay falsely claims FULFILLED.",
        ),
        (
            "stale-approval",
            "revoked-approval.json",
            "Replay attempts purchase twice after approval revocation; both are blocked.",
        ),
    ):
        cases.append(
            (
                slug,
                load_scenario("approval-revoked"),
                load_scripted(DEFAULT_SCENARIOS_DIR.parent / "scripts" / script),
                summary,
            )
        )
    rendered = []
    for slug, scenario, controller, summary in cases:
        report = execute_run(scenario, 42, output / "runs" / slug, controller=controller).report
        rendered.append((slug, report, summary))
        (output / f"{slug}.html").write_text(render_case(slug, report, summary), encoding="utf-8")
    (output / "index.html").write_text(render_index(rendered), encoding="utf-8")
    if live_report is not None:
        from agent_rehearsal.live_eval import render_live_report

        document = json.loads(live_report.read_text(encoding="utf-8"))
        live_page = render_live_report(document)
        # Byte copy, not re-serialized: the download must match the pinned evidence hash.
        shutil.copyfile(live_report, output / "live-report.json")
    else:
        live_page = _page(
            "Live model evaluation",
            "<p>No live evaluation report supplied. No live-model results are claimed.</p>"
            "<p>The existing cases are scripted or deterministic offline runs.</p>"
            "<h2>Evaluation status</h2>"
            "<details><summary>Recorded status</summary><pre>"
            '{"live_trials": 0, "status": "not_run"}'
            "</pre></details>"
            '<p><a href="index.html">Recorded offline cases</a></p>',
        )
    (output / "live-model-evaluation.html").write_text(live_page, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live-report", type=Path, help="saved live-eval report; no API calls")
    args = parser.parse_args()
    generate_demo(args.output, args.live_report)
    print("Generated static demo and seven offline evidence bundles (one intentional FAIL).")


if __name__ == "__main__":
    main()
