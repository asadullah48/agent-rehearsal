# Session 4 checkpoint

Date: 2026-10-17 (Asia/Karachi). Branch: `feat/session-4-demo`.
Starting main: `d46b10e87303eb5261fd19e99705b26e29334314`.

## Done

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
  or deployment steps. CI: **written, unverified until first GitHub Actions run**.
- M3: evidence-linked portfolio draft, README usage and this checkpoint.
  No dependencies were added. All original tests and the offline guard are preserved.

## Validation evidence

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

`test_demo_real_runs_private_and_relative` independently evaluates all five SQLite
databases and verifies all five ledgers. `test_demo_refuses_existing_output` checks
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
- GitHub Actions cannot be validated offline. Local checks do not establish a
  GitHub Actions result or a required merge-blocking check.

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
  **deterministic reference controller (not an AI agent)**.
- Forks, reviewer mutation, live agents, MCP and Arabic remain deferred, with no
  delivered-feature claims. No real purchasing, SDK, model call, API key,
  new dependency, runtime networking, application server or hosting was added.
- Viewer generation itself is not crash-resumable or atomically published. If
  generation is interrupted, use a fresh output directory. Privacy checks cover
  the shipped synthetic fixtures; this is not a general arbitrary-data scrubber.
- No publication, PR or merge. Portfolio stays a draft. CI and true phone checks
  retain the unverified status stated above.

## Exact PowerShell review commands

Run from the repository root. Output must be fresh (choose another name if it
already exists). Opening the file is a local browser action, without a server.

```powershell
.venv\Scripts\python.exe -m agent_rehearsal.demo --output .rehearsal/session4-demo
if ($LASTEXITCODE -ne 0) { throw 'Demo generation failed' }
Invoke-Item .rehearsal/session4-demo/index.html
```

Independently evaluate and verify every generated run, including manifest hashes:

```powershell
Get-ChildItem .rehearsal/session4-demo/runs -Directory | ForEach-Object {
    $run = $_.FullName
    foreach ($command in @('evaluate', 'verify', 'replay')) {
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

Full validation (all 160 prior tests plus new demo regressions):

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
