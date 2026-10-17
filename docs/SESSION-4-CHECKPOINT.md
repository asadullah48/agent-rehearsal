# Session 4 checkpoint

Date: 2026-10-17 (Asia/Karachi). Branch: `feat/session-4-demo`.
Starting main: `d46b10e87303eb5261fd19e99705b26e29334314`.

## Follow-up F1 / F2 / F3 completed

Follow-up starts at `e491933` on the same `feat/session-4-demo` branch. No PR,
merge, deployment or publication. No dependencies added. Repository remains
private; portfolio card stays a draft.

- F1 (`e92edfb`): seven real offline runs over the five fixtures. Added
  `false-success` and `stale-approval`, both using the Session 3 scripted adapter
  and labelled **scripted offline agent (replayed decisions, no model calls)**.
  Each attempts purchase twice after approval revocation; both calls are blocked
  with `APPROVAL_REVOKED` and certainty `not_committed`. Neither creates a PO.
  False-success nevertheless claims `FULFILLED`, fails `expected_business_outcome`,
  and has verdict FAIL. Stale-approval claims `REFUSED` and correctly passes.
  Original reference-controller S03/S04 expected refusals remain visibly distinct
  from evaluation FAIL. Original adapter labels remain visible too.
- F2 (`9542e7e`): index cards grouped under “Controller handles disruptions” and
  “Evaluator catches a bad agent”. Cards display scenario ID, controller, execution
  status, business outcome, verdict badge and a one-line disruption summary.
  Case pages show assertion results and event-ID links into an ordered timeline,
  original tool responses, PO count, and blocked purchase-commit count. Events are
  open by default, can be collapsed, and receive a visible outline when targeted.
  Semantic HTML, skip link, native controls, explicit focus rings, wrapping text,
  and text-labelled high-contrast PASS/FAIL/NOT_APPLICABLE badges require no scripts,
  fonts, CDNs or external resources. NOT_APPLICABLE is an assertion result; no
  generated run is mislabelled as unevaluated.
- F3 (`4f7cb3d`): six tests in `tests/test_demo_view.py` compare rendered values
  against original reports, resolve every assertion event link, inject unsafe
  fixture text, reject external resources, distinguish false-success FAIL from
  expected refusal PASS, and check title/lang/headings/structure. F1 adds another
  test for the real scripted outcomes and exact CLI exit codes.
- All 162 existing test cases remain. The original demo regression now expects
  eight pages, the one intended failed run, and valid same-page event fragments;
  its privacy and evidence checks remain intact. All 160 pre-demo tests are unchanged.
- CI is now **verified: GitHub Actions run 39019867173 succeeded**.
  Evidence: user-verified [Actions run](https://github.com/asadullah48/agent-rehearsal/actions/runs/39019867173).
  This records the supplied pre-follow-up CI result; it does not claim that the
  new follow-up commits have already completed their own GitHub Actions run.

### Follow-up validation

| Check | Result |
| --- | --- |
| Starting baseline | 162 passed in 151.15s |
| Final full suite | **169 passed in 202.33s** |
| New coverage | 1 scripted-case test + 6 viewer tests |
| F1/F2 focused checks | 3 passed; scripted cases, privacy and overwrite refusal |
| F3 focused checks | 6 passed in 56.50s |
| Ruff check / format | Clean |
| Generated artifacts | 8 HTML pages, 7 original evidence bundles |
| Independent CLI smoke | 21 exits as expected: evaluate false-success = 1; all other exits = 0 |
| Manifest hashes | All entries match across all 7 bundles |
| Privacy | Every generated file scanned, including SQLite; no absolute paths or current user/machine names |
| Installed Edge | All 8 pages: body clientWidth=380, body scrollWidth=380 with all details expanded |

New evidence is ignored under `.rehearsal/session4-followup-final/` and
`.rehearsal/session4-followup-validation/`. Edge QA copies constrained content to
380px; this is still not a true 380px browser viewport/touch-device check. The
index screenshot was inspected. Reused Edge profiles and an unwaited GUI output
capture initially returned no DOM; a waiting pipeline with dedicated local
profiles produced all eight measurements. Edge emitted renderer task-manager
diagnostics but returned the expected DOM and screenshot. No installation or
loopback server was used. QA scripts are outside the generated demo.

### Follow-up changed files

8 files relative to `e491933`: `src/agent_rehearsal/demo.py`,
`scripts/false-success.json`, `tests/test_demo.py`, `tests/test_demo_cases.py`,
`tests/test_demo_view.py`, `README.md`, `docs/PORTFOLIO-CARD-DRAFT.md`, and this
checkpoint. The branch has 9 changed files relative to main, including its
previously committed CI workflow. Final docs commit and actual push are reported
in chat.

## Initial Session 4 delivery (historical)

- Created the requested branch from clean main. No PR, merge, deployment or
  publication is authorized. Repository remains private; portfolio card is a draft.
- M1: standard-library static viewer generator executes all five offline fixtures
  with seed 42 and keeps the complete original bundles. Six read-only HTML pages
  use relative links, native expandable timeline/tool entries, assertion evidence
  references, and separate execution status/business outcome/evaluation verdict.
  Case selection opens an already-executed case. Existing output is refused.
- Privacy regression scans every generated file, including binary SQLite, for
  absolute Windows/UNC and user-home paths and current user/machine identifiers.
  No host paths, usernames or machine names are inserted into generated output.
- M2: workflow installs existing `.[dev]` extras and runs pytest with the addopts
  override, Ruff check and Ruff format check on push and pull_request. No secrets
  or deployment steps. Initial CI status was unverified; it is now
  **verified: GitHub Actions run 39019867173 succeeded** (user-verified).
- M3: evidence-linked portfolio draft, README usage and this checkpoint.
  No dependencies were added. All original tests and the offline guard are preserved.

## Initial validation evidence (before follow-up)

All Python commands used `.venv\Scripts\python.exe`.

| Check | Baseline | Final |
| --- | --- | --- |
| Tests | 160 passed in 183.61s | 162 passed in 183.43s |
| New tests | None | 2: generated output/privacy/relative links/fresh evaluations/ledger checks; overwrite refusal |
| Ruff check | Existing baseline | All checks passed |
| Ruff format | Existing baseline | All files formatted |
| Generated demo | None | 6 HTML pages, 5 original offline bundles |
| CLI smoke | None | 15 expected zero exits: evaluate, verify and replay for each case |
| Manifest hashes | None | All 5 bundles match every manifest entry |
| Browser | Installed Edge | All 6 pages: body clientWidth=380, body scrollWidth=380 with all details expanded |

Initially, `test_demo_real_runs_private_and_relative` evaluated all five SQLite
databases and verified all five ledgers. It now covers all seven, including the
expected failed verdict. `test_demo_refuses_existing_output` checks
overwrite refusal. The initial regression collection failed with the missing demo
module before implementation. The final full suite passed; the privacy test was
then strengthened with explicit current username/machine checks and rerun separately.

Installed Edge was used headlessly with local file URLs and a workspace-local
profile; no server or installation. Its minimum viewport measured 477/492px,
so QA copies constrained content to 380px and expanded every timeline entry.
All six bodies measured 380px without horizontal overflow. The first 380px
screenshot clipped the wider headless viewport; it is not evidence of a true
380px viewport check. Actual phone viewport, touch and manual interaction checks
remain **manual/unverified**. Native details and links require no JavaScript.
QA copies and screenshots are ignored artifacts outside the generated demo.

## Commits before this checkpoint

| Hash | Message |
| --- | --- |
| 390bff2 | feat: generate static read-only viewer from offline evidence runs |
| 3867288 | ci: add existing-dev-extras validation workflow |

Final documentation/privacy-check commit and actual branch push result are in the
chat report. Generated evidence and browser QA stay under ignored `.rehearsal/`.

## Files changed relative to main

6 files: `README.md`, `.github/workflows/ci.yml`,
`src/agent_rehearsal/demo.py`, `tests/test_demo.py`,
`docs/PORTFOLIO-CARD-DRAFT.md`, `docs/SESSION-4-CHECKPOINT.md`.

## Tooling caveats and corrected failures

- Sandbox Git metadata writes and default pytest temporary directories required
  approved access. An initial restricted baseline produced environment access
  errors; the approved-access baseline passed all 160. Those access errors are
  not product failures. Edge also required approved access to start its process.
- Fixed a stray CSS selector prefix before generating the demonstration. Ruff and
  generated-output regressions passed afterward. No automatic approval rejection
  blocked delivery. No global settings, credentials or environment were changed.
- GitHub Actions cannot be validated offline. The user verified run 39019867173
  succeeded. Local checks alone do not establish a GitHub Actions result or a
  required merge-blocking check.

## Not done / limits

All [Session 3 limits](SESSION-3-CHECKPOINT.md#not-done--limits) still apply:

- Recovery is exercised at six named workflow and seven named export boundaries
  with process termination, not arbitrary instruction-level crashes, power loss,
  disk corruption or concurrent writers. One writer; close external SQLite handles
  before Windows publication.
- SQLite, chain and journal remain local authoritative simulation state. No
  distributed durability, external connector recovery, general migration or
  incompatible-adapter guarantee. Paused reviewer waits stay paused.
- Export publication swaps complete directory generations with a possible
  **temporary absent public directory**. It is not continuous atomic replacement
  of six root files. Resume restores the backup and rebuilds interrupted exports.
- Evidence is **tamper-evident (not tamper-proof)**. A file owner can rewrite the
  chain, local anchor, manifest and journal. Keep a trusted digest outside the
  writable bundle. Resume does not authenticate provenance. `verify` checks the
  ledger; manifest hashing is a separate check.
- Both controllers remain offline. Scripted proposals can ignore responses and
  be unsafe; a passing refusal exercise does not certify a script. The protocol
  does not sandbox untrusted Python. Synthetic observations are not production
  reliability or throughput measurements. The viewer uses a
  **deterministic reference controller (not an AI agent)** in five cases and a
  **scripted offline agent (replayed decisions, no model calls)** in two cases.
- Forks, reviewer mutation, live agents, MCP and Arabic remain deferred, with no
  delivered-feature claims. No real purchasing, SDK, model call, API key,
  new dependency, runtime networking, application server or hosting was added.
- Viewer generation itself is not crash-resumable or atomically published. If
  generation is interrupted, use a fresh output directory. Privacy checks cover
  the shipped synthetic fixtures; this is not a general arbitrary-data scrubber.
- No publication, PR or merge. Portfolio stays a draft. True phone viewport/touch
  checks remain manual/unverified; CI has the user-verified result stated above.

## Exact PowerShell review commands

Run from the repository root. Output must be fresh (choose another name if it
already exists). Opening the file is a local browser action, without a server.

```powershell
.venv\Scripts\python.exe -m agent_rehearsal.demo --output .rehearsal/session4-review
if ($LASTEXITCODE -ne 0) { throw 'Demo generation failed' }
Invoke-Item .rehearsal/session4-review/index.html
```

Exact expected exits (evaluate / verify / replay):

| Run | evaluate | verify | replay |
| --- | --- | --- | --- |
| happy-path | 0 | 0 | 0 |
| approval-revoked | 0 | 0 | 0 |
| supplier-changed | 0 | 0 | 0 |
| duplicate-po | 0 | 0 | 0 |
| response-lost | 0 | 0 | 0 |
| stale-approval | 0 | 0 | 0 |
| false-success | **1 (expected evaluation FAIL)** | 0 | 0 |

Independently review every generated run and its manifest without throwing on the
intentional failure:

```powershell
$expected = @{
    'happy-path' = 0; 'approval-revoked' = 0; 'supplier-changed' = 0
    'duplicate-po' = 0; 'response-lost' = 0; 'stale-approval' = 0
    'false-success' = 1
}
foreach ($entry in $expected.GetEnumerator()) {
    $run = Join-Path '.rehearsal/session4-review/runs' $entry.Key
    & .venv\Scripts\python.exe -m agent_rehearsal evaluate --run-dir $run
    if ($LASTEXITCODE -ne $entry.Value) { throw "Unexpected evaluate exit: $run" }
    foreach ($command in @('verify', 'replay')) {
        & .venv\Scripts\python.exe -m agent_rehearsal $command --run-dir $run
        if ($LASTEXITCODE -ne 0) { throw "$command failed for $run" }
    }
    $manifest = Get-Content (Join-Path $run 'manifest.json') -Raw | ConvertFrom-Json
    foreach ($entry in $manifest.PSObject.Properties) {
        $actual = (Get-FileHash (Join-Path $run $entry.Name) -Algorithm SHA256).Hash
        if ($actual.ToLowerInvariant() -ne $entry.Value) { throw 'Manifest mismatch' }
    }
}
```

Full validation (all 162 existing test cases plus 7 follow-up tests):

```powershell
.venv\Scripts\python.exe -m pytest -o "addopts=" -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
.venv\Scripts\python.exe -m ruff check .
if ($LASTEXITCODE -ne 0) { throw 'Lint failed' }
.venv\Scripts\python.exe -m ruff format --check .
if ($LASTEXITCODE -ne 0) { throw 'Format failed' }
```

## Next step

Commit the final milestone, push only `feat/session-4-demo`, and report the actual
push result. No PR, merge or publication. Review the generated viewer locally.
