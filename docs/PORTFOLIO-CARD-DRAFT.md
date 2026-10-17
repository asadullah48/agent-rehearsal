# Agent Rehearsal — private portfolio card draft

Unpublished draft. Repository stays private; publication is not authorized.

Every proposed card claim below is paired with its evidence. Counts describe
local synthetic exercises, never real customers or production reliability.

| Proposed card claim | Evidence |
| --- | --- |
| Offline procurement simulator using a deterministic reference controller (not an AI agent) | `test_report_labels_controller_deterministic_and_reports_no_model_usage`; `report.json`: `adapter`, `measurements.model_calls` |
| Seven predefined recorded runs over five synthetic fixtures, browsable in a static read-only HTML viewer | `python -m agent_rehearsal.demo --output .rehearsal/demo`; `test_demo_real_runs_private_and_relative`; `test_demo_rendered_values_match_each_report` |
| Independent evaluator catches a false success claim from a scripted offline agent (replayed decisions, no model calls) | `test_demo_false_success_fails_and_stale_approval_is_blocked`; `test_demo_false_success_fail_badge_differs_from_expected_refusal`; false-success `report.json`: `controller.final_state=FULFILLED`, empty `final_state.purchase_orders`, `expected_business_outcome` assertion fails, `verdict=fail`; CLI `evaluate` exits 1 |
| Two stale-approval purchase attempts are blocked; expected refusal is distinct from evaluation FAIL | `test_demo_false_success_fails_and_stale_approval_is_blocked`; `test_demo_rendered_values_match_each_report`; stale-approval `report.json`: `tool_calls[].response.error.reason_code=APPROVAL_REVOKED`, `controller.final_state=REFUSED`, `verdict=pass` |
| Separate execution status, business outcome and independent evaluation verdict | `report.json`: `execution_status`, `business_outcome`, `verdict`, `assertions`; `python -m agent_rehearsal evaluate --run-dir .rehearsal/demo/runs/happy-path` |
| Ordered recorded timeline, tool responses and assertions linked to saved event details | `report.json`: `events`, `tool_calls`, `assertions[].evidence_event_ids`; `test_demo_rendered_values_match_each_report`; `test_demo_every_evidence_link_resolves_to_saved_event` |
| Static HTML escapes unsafe fixture text and has no external resources | `test_demo_escapes_unsafe_fixture_text`; `test_demo_no_external_resources` |
| Generated artifacts omit absolute paths and current Windows user/machine identifiers | `test_demo_real_runs_private_and_relative` scans every generated file including SQLite |
| Evidence is tamper-evident (not tamper-proof) | `test_tamper_fails_at_affected_index`; `test_rewritten_chain_fails_retained_digest`; `python -m agent_rehearsal verify --run-dir .rehearsal/demo/runs/happy-path` |
| Recovery exercised at six named workflow crash boundaries and seven export boundaries | `test_real_process_crash_resume_and_trace_determinism`; `test_export_process_interruption_never_publishes_half_bundle` |

Limits: synthetic fixtures, offline controllers, one local writer. No production
reliability claim. Retain a trusted digest outside the writable bundle: an owner
can rewrite the chain, anchor, manifest and journal. Export publication can
temporarily leave the public directory absent; resume restores/rebuilds it.
All Session 3 limitations remain applicable; see
[Session 3 checkpoint](SESSION-3-CHECKPOINT.md#not-done--limits) and
[Session 4 checkpoint](SESSION-4-CHECKPOINT.md).
