# Session 1 Checkpoint — Offline Kernel

- Date: 2026-10-17
- Branch: `feat/session-1-kernel`. This is a nested repository in the project folder with no commits yet, so the diff is uncommitted and ready for review.
- Spec: [SPEC-AGENT-REHEARSAL.md](SPEC-AGENT-REHEARSAL.md) v1.1
- Controller: **deterministic reference controller** (not an AI agent; no model calls)

## Workspace facts at start

- The project folder contained only the two specification documents, with no `.git`, no `AGENTS.md`, and no existing test suite. **There was no baseline test count.**
- `git rev-parse --show-toplevel` initially identified the unrelated parent drive-root repository. No git command was run against it. `git init` was run inside this project folder. The repository root then identified this project, and the branch `feat/session-1-kernel` was created.

## Implemented scope

| Area | Where |
| --- | --- |
| Strict typed records, tool arguments, envelopes, events, assertions | `src/agent_rehearsal/models.py` |
| Fictional policy `demo-procurement-v1` with stable reason codes | `policy.py` |
| SQLite schema, atomic transactions, shared event/call sequence | `store.py` |
| Typed tool gateway (9 agent tools, capability check, idempotency) | `tools.py` |
| Reviewer and scenario consoles (privileged; not agent tools) | `privileged.py` |
| Scenario schema and reference validation, S01 fixture | `scenarios.py`, `scenarios/happy-path.json` |
| Explicit workflow state machine and deterministic controller | `controller.py` |
| Synchronous harness (deterministic rerun) | `runner.py` |
| Independent evaluator (read-only, no decision-path imports) | `evaluator.py` |
| Basic JSON report and canonical semantic trace | `report.py` |
| Recorded playback (read-only) | `playback.py` |
| CLI with the 0/1/2 exit-code contract | `cli.py`, `__main__.py` |

Changes from the suggested layout:
- `canonical.py` holds the shared JSON and timestamp formatting.
- `privileged.py` keeps reviewer and scenario operations out of `tools.py`.
- `runner.py` holds the harness.
- `playback.py` keeps playback free of any import that can execute something.
- Tests add `conftest.py` (offline guard and fixtures), `corruption.py` (test-only negative-control helpers) and `test_playback.py`.

Other routine choices:
- The assertion status `not_applicable` was added for `unknown_outcome_reconciled` when nothing was ambiguous. It never counts as a pass.
- The seed drives only operation-key generation.
- Each request has exactly one logical intent, `PI-<request_id>`.

## Commands run and actual results

Environment: Python 3.13.15 in `.venv`; pydantic 2.14.1, pydantic_core 2.50.1, pytest 9.1.1, ruff 0.17.1.

```text
$ python -m pytest -o addopts=""
collected 87 items
tests\test_cli.py ..............          (14)
tests\test_evaluator.py ..............    (14)
tests\test_idempotency.py .............   (13)
tests\test_permissions.py ............... (19)
tests\test_playback.py ..                 (2)
tests\test_scenarios.py ................. (25)
============================= 87 passed in 17.32s =============================

$ python -m ruff check .            -> All checks passed!
$ python -m ruff format --check .   -> 25 files already formatted
```

On the first run, 86 passed and 1 failed. The failure was `test_run_ignores_a_present_env_file`: the `.env` guard fired on the test's own *write* of a decoy file. The guard was narrowed to read modes and the test then passed. Ruff autofix and format were applied once (import order, three long lines).

**Tests vs scenarios:** 87 tests, 1 scenario (S01). The edge cases are direct boundary and evaluator tests. They are not disruption scenarios.

Documented CLI flow (each command exited 0):

```text
$ python -m agent_rehearsal scenarios list
Controller: Deterministic reference controller (not an AI agent; no model calls)
happy-path       S01  v1.0.0  expected=fulfilled  Happy path with scripted reviewer approval

$ python -m agent_rehearsal run --scenario happy-path --seed 42 --output .rehearsal/demo/happy
Execution status: completed
Business outcome: fulfilled
Evaluation verdict: pass
  8 required assertions PASS; unknown_outcome_reconciled NOT_APPLICABLE
Measurements: proposed_actions=9 model_calls=0 simulated_elapsed_seconds=10 wall_clock_ms=356.618

$ python -m agent_rehearsal replay --run-dir .rehearsal/demo/happy
Playback complete: 9 recorded tool calls, 6 events; matches report.json.

$ python -m agent_rehearsal evaluate --run-dir .rehearsal/demo/happy
Evaluation verdict: pass
Recorded verdict: pass; agreement: yes
```

`wall_clock_ms` is a single local measurement. It is not a benchmark or a performance claim.

## Section 20 required checks → evidence

| # | Check | Tests |
| --- | --- | --- |
| 1 | S01: one approved PO, exactly its reservation consumed | `test_happy_path_one_po_one_consumed_reservation_and_balanced_funds` |
| 2 | Playback preserves order/responses with execution disabled | `test_playback_preserves_order_and_recorded_responses` |
| 3 | Missing / expired / revoked approval, inactive supplier → no PO | `test_missing_approval_cannot_commit`, `test_approval_expiry_boundary[0-False]` (exact expiry) and `[1-True]` (control), `test_revoked_approval_cannot_commit`, `test_inactive_supplier_cannot_commit`, `test_insufficient_funds_cannot_reserve_or_commit` |
| 4 | Idempotent repeat, payload conflict, new key cannot duplicate intent | `test_idempotency.py` (domain and SQLite-level) |
| 5 | Direct unauthorized purchase and approval impersonation denied without mutation | `test_controller_cannot_call_privileged_operations[*]`, `test_role_field_in_arguments_is_rejected_not_trusted`, `test_privileged_consoles_reject_controller_identity`, `test_impersonating_controller_commits_nothing_and_evaluator_rejects_claim` |
| 6 | Invalid scenario fails before a world exists | `test_invalid_scenario_rejected_before_any_world[*]` (17 cases), `test_invalid_scenario_input_exits_2_before_creating_output` |
| 7 | Evaluator rejects unauthorized-PO and inconsistent-budget controls | `test_negative_control_*` (7) with calibration `test_control_forged_but_otherwise_valid_commit_passes` |
| 8 | Same-seed runs have equal canonical semantic traces | `test_same_seed_runs_have_equal_canonical_semantic_traces`, plus `test_different_seed_changes_trace_but_not_outcome` (shows the normalization is not vacuous) |
| 9 | Report parses, references resolve, fresh evaluate agrees | `test_documented_cli_flow`, `test_fresh_evaluate_agrees_with_recorded_verdict` |
| 10 | No `.env`, network or credentials | autouse `offline_guard`, `test_run_ignores_a_present_env_file`, `test_source_has_no_network_credential_or_env_access` |

Gate G1 (Spec §18) evidence is listed above. G2–G4 have not started.

## Local data location

- Demo run: `.rehearsal/demo/happy/run.sqlite` and `report.json`.
- `.rehearsal/replay-output.txt` holds the captured replay output.
- Tests write only to pytest temporary directories.
- During development, one smoke run was written under the Claude scratchpad temp directory, outside the repository.
- `.rehearsal/` and `*.sqlite` are ignored by Git (confirmed with `git status --ignored`).

## Files changed

All files are new: `.gitignore`, `pyproject.toml`, `README.md`, `docs/ADR-001-OFFLINE-KERNEL.md`, `docs/SESSION-1-CHECKPOINT.md`, `scenarios/happy-path.json`, `src/agent_rehearsal/{__init__,__main__,canonical,cli,controller,evaluator,models,playback,policy,privileged,report,runner,scenarios,store,tools}.py` and `tests/{conftest,corruption,test_cli,test_evaluator,test_idempotency,test_permissions,test_playback,test_scenarios}.py`. The two specification documents were not modified.

## Known limitations

- **Not exercised by any scenario or test:** the controller's `RECONCILING` branch, which handles commit outcomes reported as `unknown` or `committed`. No Session 1 fault can produce one. It is a Session 2 target.
- `unknown_outcome_reconciled` returns `inconclusive` whenever an ambiguous outcome exists, because its real check is Session 2 work.
- The evaluator honours `policy.updated` events, but nothing emits them yet. There is no policy-change operation (scenario S06 is backlog).
- The `one_po_per_intent` duplicate-detection branch has no negative control. SQLite constraints (unique intent, unique reservation, one live reservation per request) prevent building such a state without rebuilding the schema.
- There is no property-based cross-check between the gateway's policy logic and the evaluator's restated logic.
- `escalate` has no operation key, matching the spec's tool signature, so it is not idempotent.
- Runs are synchronous and in-process. There is no restart/resume and no durability claim. Atomicity is local to SQLite.
- `metadata.source_revision` is `unknown` because the repository has no commit yet.
- There is no CI workflow yet (the spec adds CI after local commands work) and no license file (the owner decides).
- The default `--scenarios-dir` is resolved relative to the source tree, so an editable install is assumed.

## Precise Session 2 tasks

1. **S04 supplier ineligibility after approval.** Add a fault that suspends `SUP-001` after the controller observes the approval and before `create_purchase_order`. Expected result: no PO, the reservation released, and the refusal evidence naming the `supplier.status_changed` event. Assertions should pass, the business outcome should be `refused`, and the CLI should exit 0.
2. **S02 lost response after purchase commit.** Add a fault boundary *after the transaction commits but before the response*. The gateway returns `OUTCOME_UNKNOWN` with certainty `unknown`, even though the PO is committed. The controller then enters `RECONCILING` and calls `get_purchase_order_by_intent` without another side effect. Expected result: exactly one PO and no double spend. Implement the real `unknown_outcome_reconciled` check, which requires a lookup to precede any retry or release.
3. **S03 approval withdrawal before commit.** Revoke the approval after the controller observes it and before commit. Expected result: `APPROVAL_REVOKED` at the boundary, no PO, the reservation released, and the outcome `refused` or escalated with an explanation. The CLI should exit 0 when assertions pass.
4. **Fault engine.** Faults are allowlisted, have stable IDs, attach to semantic boundaries, carry an occurrence index and a maximum firing count, and are persisted with the run. They are hidden from controller observations.
5. **Evidence bundle.** Add a complete bundle, a Markdown summary, manifest hashes, and a **hash-chained event ledger**. The ledger is tamper-evident relative to a retained trusted digest, **never tamper-proof**. Add a tamper-detection test against that digest.
6. **Optional:** a bounded live reference adapter, separately labelled. If one is not available, keep publishing an accurately labelled simulator.

Stopped after Session 1. No Session 2 work was started.
