# Agent Rehearsal — private portfolio card draft

Unpublished draft. Repository stays private; publication is not authorized.

Every proposed card claim below is paired with its evidence. Counts describe
local synthetic exercises, never real customers or production reliability.

| Proposed card claim | Evidence |
| --- | --- |
| Offline procurement simulator using a deterministic reference controller (not an AI agent) | `test_report_labels_controller_deterministic_and_reports_no_model_usage`; `report.json`: `adapter`, `measurements.model_calls` |
| Five predefined recorded cases, browsable in a static read-only HTML viewer | `python -m agent_rehearsal.demo --output .rehearsal/demo`; `test_demo_real_runs_private_and_relative` |
| Separate execution status, business outcome and independent evaluation verdict | `report.json`: `execution_status`, `business_outcome`, `verdict`, `assertions`; `python -m agent_rehearsal evaluate --run-dir .rehearsal/demo/runs/happy-path` |
| Expandable recorded event timeline and tool responses | `report.json`: `events`, `tool_calls`; generated case HTML; `test_demo_real_runs_private_and_relative` |
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
