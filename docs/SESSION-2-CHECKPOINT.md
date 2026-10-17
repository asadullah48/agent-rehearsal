# Session 2 checkpoint

Date: 2026-10-17 (Asia/Karachi). Branch: `feat/session-2-disruptions`.
Starting main revision: `4ef6aed`. Main was not changed.

## Done

- Confirmed the Git root is `D:/agent-rehearsal` and the starting tree was clean.
  Created the requested feature branch. No Git command targeted the parent repo.
- Read the spec, Session 1 checkpoint and README. No repository AGENTS.md or
  CLAUDE.md was found. Applied the user's supplied instructions.
- Identified the three unambiguous core disruptions in a 10-line pre-code summary:
  S02 response lost, S03 approval revoked, S04 supplier changed.
- A: added S11 `duplicate-po`; the deterministic controller deliberately attempts
  a second PO with a fresh key. The gateway blocks it and the evaluator reports
  the blocked attempt. S01 remains the single-PO positive control.
- B: shipped S04, S02 and S03 individually, with green-suite commits after each.
  Each has JSON scenario data, controller handling, independent evaluator checks,
  generic CLI support, and expected-pass / expected-failure tests.
- Faults have stable IDs, semantic boundaries, occurrence indices, and maximum
  firing count one. Firings are persisted and hidden from the controller.
- S02 records response loss after the local purchase transaction commits. The
  delivered ambiguous response is persisted; the controller reconciles by intent
  lookup without another purchase/release. The evaluator checks matching intent,
  lookup result versus authoritative PO, and ordering before retry/release.
- C: every event appends a SHA-256 chain record in the event transaction. Completed
  runs export a JSONL ledger, anchor, Markdown summary and file manifest alongside
  the database and report. `verify` accepts a separately retained digest/count.
- Updated README status, scenario catalog, commands and limitations.

## Validation evidence

All tools used `.venv\Scripts\python`; no dependency was added.

| Check | Baseline | Final |
| --- | --- | --- |
| Tests | 87 passed | 114 passed in 32.86s |
| Ruff check | Clean | All checks passed |
| Ruff format check | 28 files formatted | 32 files formatted |
| Scenario files | 1 | 5 |
| CLI subprocess smoke | Session 1 commands already covered | 26 invocations exited 0 |

Commands: `.venv\Scripts\python -m pytest -o addopts='' -q`,
`.venv\Scripts\python -m ruff check .`, and
`.venv\Scripts\python -m ruff format --check .`.
The explicit pytest addopts override removes the configured extra `-q` so the
measured test count is printed.

Windows sandbox directory permissions caused pytest setup errors on the first
baseline attempt, including a repository-local basetemp attempt. The authorized
unsandboxed rerun of the same virtual environment passed all baseline tests;
subsequent validation used that execution context. No product fix was made for
this tooling restriction. Pytest used its default Windows temporary directories;
product smoke evidence and source changes are inside this repository.

New Session 2 scenario-focused test instances (not coverage percentages):

| Scenario | New tests | What they exercise |
| --- | --- | --- |
| S01 happy-path | 2 | Single-PO positive control; all CLI happy paths |
| S11 duplicate-po | 3 | Blocked/reported duplicate; deterministic rerun; CLI |
| S04 supplier-changed | 4 | Refuse/release; suppressed-fault failure; rerun; CLI |
| S02 response-lost | 7 | Reconcile; missing lookup; retry/release/wrong intent failures; rerun; CLI |
| S03 approval-revoked | 4 | Refuse/release with evidence; suppressed-fault failure; rerun; CLI |
| Evidence, shared | 7 | Untouched chain; edit/delete/reorder/truncate indices; whole-chain rewrite versus retained digest; atomic rollback |

These 27 new tests plus the 87 existing tests total 114. Existing direct boundary
and evaluator tests are not counted again as new scenario tests.

Milestone suite totals: A 89, S04 91, S02 93, S03 95, C 102, final 114.
Development runs caught and corrected a generated JSON BOM, stale catalog/output
expectations, and missing JSON argument decoding in the new reconciliation check.
No failing run was counted as a pass.

The 26 real subprocess CLI checks comprise catalog listing plus five invocations
per scenario: run, replay, evaluate, verify with local anchor, and verify with
explicit retained head/count. Logs and `results.json` are ignored local artifacts
under `.rehearsal/session2-validation/`; each scenario's evidence is in its own
subdirectory. These are synthetic run results, not performance benchmarks.

## Commits before this checkpoint

| Hash | Message |
| --- | --- |
| c57be0a | feat: add duplicate PO attempt scenario and evaluator reporting |
| 4db94db | feat: add S04 supplier suspension at purchase commit boundary |
| 44b339d | feat: add S02 lost purchase response and independent reconciliation checks |
| fa6f239 | feat: add S03 reviewer approval revocation before commit |
| bfe843a | feat: append atomic SHA-256 evidence chain and add verify CLI |

## Files changed relative to main

`README.md`; `docs/SESSION-2-CHECKPOINT.md`;
`scenarios/{duplicate-po,supplier-changed,response-lost,approval-revoked}.json`;
`src/agent_rehearsal/{cli,controller,evaluator,evidence,runner,scenarios,store}.py`;
`tests/{test_cli,test_disruptions,test_evidence,test_scenarios}.py`.
17 files total, including this checkpoint.

## Not done / limits

- No live model/AI adapter, network call in runtime code, API key, runtime/dev
  dependency, frontend, API server, MCP, deployment, Arabic, fork or restart/resume.
- Fault scheduling is synchronous and bounded, not a general fault simulator.
  Unfired configured core faults fail expected-outcome evaluation. Only the three
  implemented effects and their permitted boundaries are accepted.
- Ambiguous purchases found by lookup are supported. Absent/unresolvable purchases
  escalate; automatic retry after an absent lookup is not implemented.
- Evidence is **tamper-evident (not tamper-proof)**. Anyone controlling the files
  can rewrite the entire chain and the local anchor. Keep a trusted digest outside
  the writable bundle; this does not prove external truth.
- `verify` checks the exported ledger, not DB/report consistency or manifest file
  hashes. `evaluate` and `replay` perform their separate existing consistency checks.
  Bundle export occurs on completion; interrupted export recovery is not built.
- No process-crash durability, failed-compensation scenario, policy-change scenario,
  CI, license selection, PR, merge, portfolio edit or production claim was added.
  No settings, credentials, global environment or global Git config were changed.

## Next step

Commit final validation/docs, then push only `feat/session-2-disruptions` to origin
as explicitly authorized. The chat report records the actual push result and final
commit hash. Do not create a PR or merge. Any later Session 3 work needs a new task.
