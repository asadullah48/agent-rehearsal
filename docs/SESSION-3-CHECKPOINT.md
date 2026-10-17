# Session 3 checkpoint

Date: 2026-10-17 (Asia/Karachi). Branch: `feat/session-3-recovery`.
Starting main: `b57b866`, matching the local `session-2-complete` tag.

## Done

- Confirmed Git root `D:/agent-rehearsal`, starting clean tree, and requested main
  revision. Created the feature branch; no Git operation targeted the parent repo.
- Read README, Session 2 checkpoint, supplied instructions and relevant source.
  No repository AGENTS.md or CLAUDE.md was found. Used the TDD skill and observed
  new milestone tests fail before implementing each feature.
- M1: success reports record count and head index, e.g.
  `PASS: 6 records verified (head index 5)`. Failure keeps the first bad index.
  Exit codes remain 0/1/2. Empty-chain success uses `head index none`.
- M2: six deterministic crash boundaries invoke `os._exit(86)`, bypassing cleanup,
  clean run completion and bundle export. Durable decision/reply journaling lets
  resume reconstruct controller state without re-executing acknowledged effects.
  Pending non-purchase effects reuse persisted results. Pending purchases reconcile
  by intent lookup first; an unacknowledged committed response becomes unknown.
- Resume validates database chain records against actual events and the JSONL
  ledger against an unchanged database prefix. A transaction may have committed
  before its JSONL mirror safe point; only that valid missing tail is regenerated.
- M3: exports stage all six files in a sibling directory, flush them and publish
  a complete directory generation. A two-rename publication can leave no public
  path; the backup restores it on resume. Interrupted completed runs finish export
  without repeating workflow actions. Cleanup checks exact generated sibling paths
  and rejects symlinks/foreign files before recursive deletion.
- M4: `adapters.Controller` is the decision-stream protocol. The deterministic
  reference controller and a scripted offline controller implement it. The latter
  reads strict versioned JSON; its configuration/hash persist for restart without
  the source file. The shipped S03 script deliberately attempts purchase twice
  after approval withdrawal. Both are blocked. A false terminal success claim fails
  independent evaluation. CLI/report labels name the actual offline controller.
- The runner reconciles ambiguous purchases even when a bad script ignores the
  unknown result. Later unsafe script proposals are still guarded and evaluated.
- M5: README documents commands, semantics, adapter extension contract and limits.
  No new runtime/dev dependency or live model integration was added.

## Validation evidence

All commands used `.venv\Scripts\python.exe`.

| Check | Baseline | Final |
| --- | --- | --- |
| Tests | 114 passed in 34.10s | 160 passed in 189.43s |
| Scenario JSON files | 5 | 5, plus one separate scripted-decision JSON asset |
| Ruff check | Clean supplied Session 2 baseline | All checks passed |
| Ruff format | 32 files, supplied baseline | 39 files formatted, including this checkpoint |
| CLI smoke invocations | Session 2: 26 successful invocations | 40 expected exits: 38 zero, 2 deliberately injected crashes at 86 |

Full-suite command:
`.venv\Scripts\python.exe -m pytest -o addopts='' -q`.
The addopts override removes the configured extra `-q` so the count is printed.
Checks: `.venv\Scripts\python.exe -m ruff check .` and
`.venv\Scripts\python.exe -m ruff format --check .`.

The autouse network/`.env` guard was preserved. All 114 original tests are
unchanged. No assertion was weakened or deleted. New recovery tests explicitly
close SQLite handles; SQLite's connection context manager alone does not close
them, which matters for directory renaming on Windows.

| Milestone | Added tests | Evidence |
| --- | --- | --- |
| M1 | 2 | Success count/head and unchanged first-bad-index failure |
| M2 | 19 | Real process death at all six flow boundaries; repeated same-point semantic traces; four other scenarios across reservation/approval restart; reconciliation ordering; corrupt ledger/database rejection; completed/missing-run resume |
| M3 | 15 | Seven export interruption boundaries for both initial and existing complete bundles; old-or-none-or-complete publication; matching manifest hashes; no repeated finished workflow |
| M4 | 10 | Both protocol implementations; blocked bad script; rejected false claim; persisted-script resume; invalid scripts/options; runner reconciliation before a bad script's next purchase |
| M5 | 0 | Documentation and final smoke/diff review |
| Total | 46 | 114 existing + 46 new = 160 |

Green milestone totals: M1 116; M2 135; M3 150; M4/final 160.
Workflow trace tests compare same scenario/seed/crash point across two separate
crashed processes. They check business-outcome agreement with an uninterrupted
run, not equality of uninterrupted and recovered tool traces: recovery can add
lookup calls. Budget conservation, reservation consistency and intent uniqueness
are independently evaluated; resumed chains verify and playback/evaluation agree.

The CLI smoke comprises one catalog listing, five commands per scenario
(run/replay/evaluate/verify/completed resume), five crash-demo invocations, four
export-demo invocations, and five scripted-controller invocations. All 40 exits
matched expectations. Logs, `results.json` and bundles are ignored local artifacts
under `.rehearsal/session3-validation/`. The injected runtime/export crashes
returned 86; recovery and verification returned 0. These are local synthetic
observations, not production reliability or throughput measurements.

## Commits before this checkpoint

| Hash | Message |
| --- | --- |
| 4088b80 | fix: clarify verify success count and head index |
| 947cd3d | feat: recover journaled runs after deterministic process crashes |
| bd9a4b5 | feat: publish complete export generations and recover interrupted exports |
| b21499a | feat: define offline controller protocol and scripted decision adapter |

The final docs/checkpoint commit hash and actual push result are in the chat report.

## Files changed relative to main

14 files:
`README.md`; `docs/SESSION-3-CHECKPOINT.md`; `scripts/revoked-approval.json`;
`src/agent_rehearsal/{adapters,cli,controller,evidence,recovery,runner,store}.py`;
`tests/{test_adapters,test_atomic_export,test_recovery,test_verify_messages}.py`.

## Tooling caveats and corrected failures

- Windows sandbox restrictions on Git and pytest were already established during
  Session 2; authorized commands used the project venv with the necessary access.
  Tests use pytest's default Windows temporary directories; product smoke files
  and all source changes are inside this repository. No global config, environment,
  credentials or Claude settings were changed.
- Baseline pytest passed all 114. Its trailing lint overlapped creation of the M1
  test and flagged that new test's formatting; formatting it restored clean checks.
  This was not counted as a baseline product failure.
- Development checks caught an incorrect report metadata lookup, protected-report
  overwrite on completed resume, Windows `fsync` requiring a writable handle,
  test-held SQLite connections blocking rename, and missing runner-side recovery
  reconciliation for the bad script. Each was corrected and regression-checked.
- A read-only inspection tool call stalled and was cancelled. It did not alter
  files or interrupt the independent test process; the completed test result was
  retrieved separately. No automatic approval rejection blocked delivery.

## Not done / limits

- Recovery is tested for the six named workflow boundaries and seven named export
  boundaries using actual process termination. It is not a proof against arbitrary
  instruction-level crashes, power loss, disk corruption or concurrent writers.
  Close external database handles before publishing on Windows; use one writer.
- SQLite, its chain and journal are local authoritative simulation state. There
  is no distributed durability, external connector recovery, general migration
  or guarantee for incompatible adapter versions. Paused reviewer waits stay paused.
- Publication is a directory-generation swap with a possible temporary absent
  public path, not a continuously available atomic replacement of six root files.
  Resume automatically restores the backup and rebuilds an interrupted export.
- Evidence remains **tamper-evident (not tamper-proof)**. A file owner can rewrite
  the whole chain, local anchor, manifest and journal. Keep a trusted digest outside
  the writable bundle. Local resume validation does not authenticate provenance.
  `verify` checks the exported ledger; manifest hashes are checked separately.
- Both controllers are offline. Scripted replay can deliberately ignore responses
  and make bad proposals; a passing refusal exercise does not certify the script.
  The protocol is not a sandbox for untrusted Python or a live-agent implementation.
- No live model calls, SDK, API keys, new dependencies, network runtime calls,
  frontend, hosting, MCP, fork, Arabic, real purchasing, PR or merge were added.
- No requested milestone was skipped. Single-writer/power-loss/model guarantees
  were not requested as delivered capabilities and are explicitly excluded.

## Next step

Commit final documentation, then push only `feat/session-3-recovery` as authorized.
No PR or merge. Use the README's fresh-directory crash/resume and scripted replay
commands to review locally. Later features need a separate task.
