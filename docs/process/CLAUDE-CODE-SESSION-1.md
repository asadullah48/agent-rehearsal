# Agent Rehearsal — Claude Code Session 1

Historical Session 1 execution prompt. The current specification is [SPEC-AGENT-REHEARSAL.md](../SPEC-AGENT-REHEARSAL.md). Open the repository root in VS Code, open the Claude Code panel, and paste the complete block below. The specification describes the roadmap; this prompt authorized Session 1 only.

```text
Implement Session 1 of Agent Rehearsal. Read docs/SPEC-AGENT-REHEARSAL.md fully first, especially Sections 4, 8–14, and 18–20. Use version 1.1 or later incorporating the four-session, English-first hiring-asset scope. Do the implementation and verification in this session; do not stop at a plan.

PURPOSE

Build a standalone, honest portfolio project: a fictional procurement simulator that checks whether tool-using controllers preserve approval, idempotency, and budget invariants. The eventual audience is agentic AI hiring managers and implementation teams. This is not a quick-income SaaS build. Keep Session 1 small and runnable offline.

SESSION 1 BOUNDARY

Deliver the procurement state machine, typed tool gateway, SQLite persistence, deterministic reference controller, one happy-path scenario, direct invariant tests, recorded tool responses and playback, independent evaluator, basic JSON evidence report, and CLI.

Do not implement the disruption engine, frontend, API server, live LLM calls, MCP, decision forks, Arabic, authentication, billing, deployment, or external integrations in this session. The three disruptions belong to Session 2. Label the reference controller deterministic in the CLI, README, and report. Never call recorded playback durable execution or call a deterministic controller a tested live AI agent.

WORKSPACE AND AUTHORIZATION

0. KNOWN LAYOUT: Work from the agent-rehearsal repository root. At initial setup, its parent drive root contained an unrelated .git directory. That parent repository is NOT this project. Never run Git mutation commands against the parent repository. If this project is not initialized, run `git init` inside the project folder, confirm `git rev-parse --show-toplevel` identifies this project, and continue. Leave the parent repository and all unrelated files untouched.
1. Inspect cwd, repository root, git status, current branch, and applicable AGENTS.md before changing files. Summarize what you found. These explicit task instructions override conflicting repository conventions.
2. Work only in the standalone agent-rehearsal project. If cwd belongs to the portfolio, CMT, ClaudeCertMastery, or another unrelated repository, stop and report the mismatch rather than placing this project inside it. If this is the correct standalone folder and it has no Git repository, initialize one locally.
3. Preserve all unrelated work. Create or switch to feat/session-1-kernel only when safe. Never reset, clean, overwrite unrelated files, or change the default branch. If that branch already has work, inspect and continue it safely.
4. Do not create a remote repository, push, open a PR, merge, deploy, purchase anything, or change any portfolio file. Leave a reviewable local diff. No commit is required.
5. Use only new local SQLite databases under this project's .rehearsal/ directory or pytest temporary directories. Do not read .env, retrieve credentials, contact production APIs/databases, or run commands copied from other projects. Dependency installation is allowed; runtime and tests must be offline.
6. Existing project/skill names in the spec are reuse candidates, not proof those resources are installed. Do not search for or invoke unavailable skills. Session 1 needs no code from the existing repositories.
7. If the specification is missing, stop and state the exact required filename. For routine implementation choices, use the smallest clear solution and document it; do not repeatedly ask permission.

IMPLEMENTATION

Use Python 3.12+, Pydantic v2, sqlite3, argparse, pytest, and Ruff. Use a src layout and pyproject.toml. Verify installed/available dependency versions, use a project virtual environment, and record the resolved versions for reproducibility. Avoid unnecessary packages. Follow the spec's suggested layout; small adjustments are fine when explained.

Use strict typed models for requests, suppliers, budgets, reservations, approvals, purchase orders, tool envelopes, events, assertions, and reports. Represent money as integer minor units. Reject invalid or unknown action fields. Use a synthetic UTC clock and stable scenario data. Wall-clock time is measurement only.

Implement the spec's fictional demo-procurement-v1 policy. Every purchase needs an active supplier, sufficient reserved funds, and active approval bound to the exact proposal and policy version. Approval expires at now >= expires_at. Check authorization at the mutation boundary. Trusted actor identity must not come from an arbitrary caller-supplied role field.

Only the scripted scenario reviewer can grant approval. The controller can inspect approval but cannot grant, extend, or revoke it. Keep reviewer/scenario operations out of the controller's tool capability set.

Make state mutations and their events atomic SQLite transactions. Enforce purchase-intent uniqueness and idempotency in the database as well as the domain layer. Same key and same canonical payload returns the original result; changed payload with the same key conflicts without mutation. A new key must not create a second PO for the same logical intent. Consuming/releasing a reservation cannot double-spend or refund spent funds.

Build one happy-path fixture using the values in the spec. All other Session 1 edge cases are direct tool/evaluator tests, not unfinished scenario files. Provide an explicit state transition model and clear rejection reason codes. The reference controller proposes tools through the gateway, never by changing tables directly.

Record ordered tool requests, recorded responses, state-changing events, policy version, actor, operation identity, and simulated timestamps. A basic report must include metadata, initial fixture/policy hashes, final state, assertions with evidence references, execution status, business outcome, and verdict. Model calls are zero for this offline controller; model cost is not applicable. Do not invent performance numbers.

The evaluator must independently inspect persisted state and event history rather than trust controller success or reuse the mutation handler as its only oracle. Evaluate authorization at commit time. Preserve separate execution status, business outcome, and evaluation verdict. Expected refusal is not automatically a failed evaluation; missing evidence cannot count as a pass.

Recorded playback must read saved events/responses only. It must not call tools, rerun the controller, or access a provider. Keep deterministic rerun distinct from playback and document both. Same-seed semantic comparison may exclude run IDs and wall-clock measurements; document all exclusions.

CLI CONTRACT

Implement and run:

python -m agent_rehearsal scenarios list
python -m agent_rehearsal run --scenario happy-path --seed 42 --output .rehearsal/demo/happy
python -m agent_rehearsal replay --run-dir .rehearsal/demo/happy
python -m agent_rehearsal evaluate --run-dir .rehearsal/demo/happy

The run directory contains run.sqlite and report.json. Refuse nonempty output directories. Exit codes: 0 required assertions pass; 1 evaluation fails; 2 invalid input, harness error, or inconclusive evaluation. Generated artifacts remain ignored by Git.

VERIFICATION

Run any existing suite before changes if there is one. For an empty project report that no baseline suite exists; never invent a baseline count.

Prove these behaviors with focused tests:
- Happy path produces one PO, consumes one reservation, and balances funds.
- Missing, expired, and revoked approval; inactive supplier; and insufficient funds cannot produce a PO.
- Controller approval impersonation is denied.
- Idempotency handles repeat calls and conflicts; a different key cannot duplicate one logical purchase intent.
- Invalid scenario input is rejected before creating a run world.
- Recorded playback works when tool and controller invocation are disabled by the test.
- Independent evaluator fails isolated unauthorized-PO and inconsistent-budget negative controls. Keep corruption helpers test-only.
- Same-seed offline runs have equal canonical semantic traces.
- A fresh evaluate command agrees with the recorded verdict and every report evidence reference resolves.
- Default tests require neither network nor credentials and never load .env.

For evaluator negative controls, show that invalid data is actually detected, not just that the happy path passes. Run pytest and Ruff, then the documented CLI flow. Report exact output and failures. If dependencies are unavailable, complete safe static work and report exactly which verification could not run; do not claim success.

DOCUMENTATION AND FINISH

Write README.md with purpose, synthetic-data disclaimer, setup, reproducible commands, deterministic-controller label, and one Limits / non-goals section. Keep strong claims only where demonstrated. State Arabic is planned, not supported. No government logo, endorsement, fabricated customer, or compliance certification.

Write docs/ADR-001-OFFLINE-KERNEL.md explaining why the simulator/evaluator is independent of an LLM and why live adapters come later.

Write docs/SESSION-1-CHECKPOINT.md with implemented scope, actual commands/results, current branch, changed files, local SQLite location, known limitations, and precise Session 2 tasks: supplier ineligibility after approval, lost response after purchase commit, and approval withdrawal before commit. Hash-chained evidence belongs to Session 2 and is tamper-evident, never tamper-proof.

End with a concise report:
1. What works and how to run it.
2. Exact test/lint/CLI results; distinguish tests from scenarios.
3. Files changed and local data location.
4. Known gaps and which acceptance criteria remain unmet.
5. Confirmation that no remote push, deployment, production access, or unrelated repository change occurred.

Stop after Session 1. Do not advance into later sessions to make the result appear more complete.
```
