# Publication preparation review

Prepared 2026-10-18 on `feat/publication-prep`, starting at merged main
`381d5ed3e9ddef95637b03cf3f5615435d7f28f8`. This branch does not authorize a
merge, visibility change, Pages enablement or portfolio deployment.

## Full-history audit

After `git fetch --all --tags`, enumerated all reachable history: **22 commits,
106 unique file blobs**. Read every commit tree and every unique blob with
`git ls-tree -rz --full-tree` / `git cat-file blob`, including deleted and earlier
file versions. Each match was mapped back to every containing commit and path.
Author, committer and full commit-message metadata were scanned separately.
This is a baseline audit; the finished prep commit is rescanned before push.

Secret checks:

- Official Gitleaks **8.30.1**, Windows x64 release, SHA-256 verified against its
  published checksum: `d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e`.
- Command: `gitleaks git . --log-opts=--all --redact=100 --ignore-gitleaks-allow --report-format=json --report-path=.rehearsal/gitleaks-history.json --no-banner --no-color`.
- Exit **0, no leaks found**. Gitleaks reported 19 commits with diff content;
  the 22 reachable commits include three merges. No custom allowlist or baseline.
- Supplementary full-blob checks: private-key headers, provider API/token formats,
  password/credential assignments, credential-bearing connection URLs, JWTs,
  `.env` filenames and uppercase environment-style assignments.
- **No credential findings; no tracked `.env` files in any commit.** The broad
  assignment detector matched 788 occurrences of ordinary Python constants across
  historical snapshots; all distinct matching source lines were inspected.

Personal-data checks:

- Windows/user-home paths, emails, international/local phone formats, common
  Windows machine names, hostname assignments and the actual local machine name.
- **378 Windows-path occurrences** in the Session 1 prompt and Session 1/2/3
  checkpoints. These describe a workspace/drive root, not a private user home.
- **8 generic Unix-home regex occurrences** in historical `tests/test_demo.py`;
  these are privacy-detector literals, not actual user paths.
- **45 email occurrences in commit metadata**, including the author's contact
  email and platform/coauthor noreply addresses. No email in tracked file contents.
- No phone numbers, C-drive/user-home identifiers or machine identifiers detected.
- The complete occurrence-by-occurrence file/commit/line/match listing and exact
  ref snapshot are in the ignored local `.rehearsal/HISTORY-PERSONAL-DATA.md` and
  `.rehearsal/history-scan.json`. They remain local to avoid republishing identifiers.

These are detector-based findings, not proof that arbitrary encodings or every
possible personal identifier are absent. No history was rewritten. Moving or
deleting a current file later would not remove its old contents from history.

Current-file cleanup: the owner approved moving the prompt into `docs/process/`
and the specification into `docs/`. Current tracked documentation now uses
repo-relative links or portable project paths. The historical audit counts above
remain findings about earlier commits; no existing commits were rewritten.
`tests/test_repository_privacy.py` scans every tracked text file listed by
`git ls-files`, ignoring URL contents and allowing local-path samples only in
`tests/fixtures/privacy-paths.json`. Its regression first failed on nine existing
documentation lines before cleanup, with each offending file and line reported.
Cleanup validation: **171 tests passed in 195.67s**, with Ruff check and format
check passing. The requested grep reports 24 lines: 15 HTTPS URL lines and nine
allow-listed fixture lines, with no other matches. All existing Markdown HTTPS
URLs are unchanged and the updated relative document links resolve.
Two earlier full runs using workspace temporary directories each hit a Windows
export-directory rename access error (170 passed, one failed). The isolated
failing test passed; the final full run used pytest's normal OS temporary directory
and passed all tests. Export behavior was not changed by this cleanup.

## Session 4 correction evidence

| Commit | Correction | README / executable evidence |
| --- | --- | --- |
| `e92edfb` | Added false-success and stale-approval scripted cases | Seven runs, five fixtures, eight pages; `tests/test_demo_cases.py` |
| `9542e7e` | Grouped cards, separate execution/business/verdict labels, counts and linked event timeline | Static demo section; `src/agent_rehearsal/demo.py` |
| `4f7cb3d` | Six viewer regressions for values, links, escaping, external resources and verdict distinctions | `tests/test_demo_view.py` |
| `4a1ef87` | Corrected five-run/six-page docs, described scripted failure evidence and exact expected exit codes | README's seven-row CLI exit table and PowerShell loop |

The checkpoint also records a corrected stray CSS selector during initial delivery;
that observation comes from its delivery log, not a separately identifiable commit.
The README test summary now says **169**, rather than the historical Session 3's 160.

The existing demo command was run with a fresh output directory:

```powershell
.venv\Scripts\python.exe -m agent_rehearsal.demo --output .rehearsal/publication-prep-demo
```

The README's evaluate/verify/replay checks are applied to all seven run directories:
false-success evaluate must exit **1**; all other 20 commands must exit **0**.
Both ledger validity and matching playback can coexist with an evaluator FAIL.
Manifest hashes are checked separately. Local command logs stay under `.rehearsal/`.
Fresh validation: **21 expected CLI exits, 35 manifest hashes matched, eight pages**.
The full Python suite passed **169 tests in 300.48s** during the initial review
in this conversation; runtime and tests are unchanged by publication preparation.
Ruff check/format and portfolio TypeScript validation pass. Pages YAML is parsed
and its trigger, private-repo guard, official-action names, artifact path and
deployment permissions are checked locally. This is not a hosted deployment test.

Verified GitHub API evidence:

- Branch [39021939311](https://github.com/asadullah48/agent-rehearsal/actions/runs/39021939311):
  success at `4a1ef87`.
- Main [39023205471](https://github.com/asadullah48/agent-rehearsal/actions/runs/39023205471):
  success at `381d5ed`.
- Newer main [39023220546](https://github.com/asadullah48/agent-rehearsal/actions/runs/39023220546):
  success at the same `381d5ed`. This is the latest green main run at audit time.

## License and public introduction

Added the owner's requested MIT `LICENSE` and changed `pyproject.toml` license
metadata to reference it. README now states up front: offline simulator and
independent evaluator; **deterministic reference controller (not an AI agent)**;
zero model calls; **tamper-evident (not tamper-proof)**; synthetic data only.
Historical checkpoint statements about no license describe their delivery dates.

## Files needing editorial consideration

All tracked paths were inventoried. The following planning, prompt, draft and
historical files warrant review. The approved prompt and specification moves below are now applied; no files were deleted.

| File | Why it could confuse a public reader | Recommendation |
| --- | --- | --- |
| [process/CLAUDE-CODE-SESSION-1.md](process/CLAUDE-CODE-SESSION-1.md) | Historical assistant execution prompt and initial workspace precautions | Moved to `docs/process/` as approved; historical label added; current local paths replaced |
| [SPEC-AGENT-REHEARSAL.md](SPEC-AGENT-REHEARSAL.md) | Original scope/roadmap includes deferred features, not all delivered functionality | Moved to `docs/` as approved; retain roadmap versus shipped-feature distinction |
| `docs/SESSION-1-CHECKPOINT.md` | Old counts, initial uncommitted state and no-remote restrictions; current paths are now portable | Keep as dated implementation evidence; consider a historical banner |
| `docs/SESSION-2-CHECKPOINT.md` | Old no-CI/no-license/no-crash-recovery statements and superseded next step | Keep as dated evidence; consider a historical banner |
| `docs/SESSION-3-CHECKPOINT.md` | Old branch-only/no-merge next step and pre-viewer status | Keep as dated evidence; consider a historical banner |
| `docs/SESSION-4-CHECKPOINT.md` | Long tooling/debugging narrative and old no-PR/no-merge restrictions | Keep; current merged-main header explicitly supersedes historical constraints |
| `docs/PORTFOLIO-CARD-DRAFT.md` | Unpublished marketing copy and intended future links | Keep in docs as a draft; retire or rename after approved deployment |
| `docs/PORTFOLIO-SITE.patch` | Prepared change for a different repository; not runnable here | Keep as a temporary review artifact; remove only after approval once applied/published |
| `docs/ADR-001-OFFLINE-KERNEL.md` | Broad “agent” motivation can be mistaken for a live-agent implementation | Keep; offline architectural decision is explicit; README remains the current feature source |
| `docs/PUBLICATION-PREP.md` | Time-bound audit/preparation status, not an ongoing safety certification | Keep as dated release evidence |

README, license, package configuration, CI, source, five scenario fixtures, two
intentional bad-decision scripts and tests are normal project assets: keep.
`tests/corruption.py` and the two scripts are deliberate negative controls, not
unfinished production code. Generated evidence, temporary audit scripts, downloaded
scan tooling and scratch output stay ignored under `.rehearsal/`. No other tracked
scratch files were found.

## Prepared Pages workflow

`.github/workflows/pages.yml` uses only official `actions/*` actions. On each
push to main, it validates source, runs the existing demo command, uploads only
the generated directory and deploys through a separate `github-pages` environment
job. Contents access is read-only; Pages/OIDC write permissions exist only on the
deploy job. Private repositories are skipped. Pages auto-enablement is explicitly
false. No generated output is committed.

**Unverified until first Pages deploy.** The owner must manually make the repo
public and enable Pages with GitHub Actions as its source, then approve merge.
Current Pages API lookup returned 404; no Pages setting was changed. Intended URL:
https://asadullah48.github.io/agent-rehearsal/.

References: [GitHub custom Pages workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages),
[configure-pages inputs](https://github.com/actions/configure-pages/blob/v5/action.yml).

## Prepared portfolio change

Exact site diff: [PORTFOLIO-SITE.patch](PORTFOLIO-SITE.patch), for
`src/components/Projects.tsx` in the portfolio repository. One English card is
added; no site commit, push or deployment. Its public source and demo links are
future destinations and must be verified before deployment. Final title,
description, impact, metrics and URLs appear verbatim in that patch.

Claims: 169 tests; green main CI at `381d5ed`; seven recorded cases across five
synthetic fixtures; false-success caught by independent evaluation; zero model
calls. No live-agent or production-reliability claim. The detailed evidence map
is in [PORTFOLIO-CARD-DRAFT.md](PORTFOLIO-CARD-DRAFT.md).
