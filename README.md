# Agent Rehearsal

An offline simulator and independent evaluator that checks whether a tool-using
controller preserves **approval**, **idempotency** and **budget** invariants in a
fictional procurement workflow.

> **Status: Session 1 (offline kernel).** The only controller is a
> **deterministic reference controller**: a fixed, seeded state machine. It is
> **not an AI agent** and makes **no model calls**. Nothing in this repository
> shows how any live LLM agent behaves.

## Synthetic data notice

Every supplier, reviewer, budget, request and amount is fictional. Policy
`demo-procurement-v1` is a deliberately strict demo rule set. It does not
represent the procurement law or policy of any government or organisation. AED
is illustrative only. There is no government affiliation, endorsement, customer
or compliance certification.

## What a run checks

A controller proposes typed tool calls. A gateway bound to a trusted actor
identity checks the capability set, the arguments and the policy at the mutation
boundary. It then commits the change and its events in one SQLite transaction.
Afterwards an evaluator reads the persisted database *read-only* and recomputes
these assertions from world tables and the ordered event log:

| Assertion | Meaning |
| --- | --- |
| `approval_valid_at_commit` | A reviewer-authored approval was active, unrevoked, unexpired (`now < expires_at`) and bound to the exact proposal **at the commit event** |
| `supplier_active_at_commit` | Supplier status, rebuilt from the fixture plus events, was `active` at commit |
| `policy_current_at_commit` | The commit and its approval used the policy version in effect at commit |
| `one_po_per_intent` | At most one purchase order per logical purchase intent |
| `budget_conserved` | `initial total == available + active reservations + spent`, and `spent == consumed == PO total` |
| `reservation_state_consistent` | Created once, terminal at most once, and PO linkage matches state |
| `all_mutations_have_events` | Every persisted mutation is explained by an event and a committed tool call |
| `expected_business_outcome` | The world-derived outcome matches the scenario, and the controller's claim matches the world |
| `unknown_outcome_reconciled` | `not_applicable` unless a tool outcome was ambiguous (Session 2) |

Three results are reported separately and never merged into a single score:
**execution status** (`running`/`paused`/`completed`/`error`), **business
outcome** (`fulfilled`/`refused`/`awaiting_review`/`unresolved`) and
**evaluation verdict** (`pass`/`fail`/`inconclusive`). Missing evidence yields
`inconclusive` or `fail`, never `pass`. A harness error can never pass.

## Setup

Requires Python 3.12 or newer. Verified on Python 3.13.15 (Windows 11).

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    POSIX: source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Versions resolved during Session 1: pydantic 2.14.1 (pydantic_core 2.50.1),
pytest 9.1.1, ruff 0.17.1. The runtime dependency is pydantic only; SQLite and
argparse come from the standard library. Once installed, runs and tests need no
network, API key or `.env` file.

The default scenario directory is located relative to the source tree, so use
the editable install shown above. Otherwise pass `--scenarios-dir`.

## Commands

```bash
python -m agent_rehearsal scenarios list
python -m agent_rehearsal run --scenario happy-path --seed 42 --output .rehearsal/demo/happy
python -m agent_rehearsal replay --run-dir .rehearsal/demo/happy
python -m agent_rehearsal evaluate --run-dir .rehearsal/demo/happy
```

- `run` refuses a nonempty output directory. It writes `run.sqlite` and `report.json`.
- `replay` is **recorded playback**. It renders the saved tool calls, recorded
  responses, events and state transitions in their original order, then
  cross-checks them against `report.json`. It invokes no tool, controller or
  model. It is **not** crash recovery and **not** durable execution.
- `evaluate` recomputes every assertion from `run.sqlite` without invoking the
  controller. It reports whether the fresh result agrees with `report.json` and
  whether every evidence reference resolves.

Exit codes: `0` all required assertions pass · `1` evaluation failed · `2` invalid
input, harness error, inconclusive evaluation, or evidence that disagrees with the
report. For `replay`, `0` means the playback matched the report.

### Three execution modes

| Mode | How | Guarantee |
| --- | --- | --- |
| Recorded playback | `replay` | Reads saved evidence only |
| Deterministic rerun | `run` again with the same scenario and seed | Equal *canonical semantic trace* (tested) |
| Live rerun | not available | No live adapter exists in Session 1 |

The canonical semantic trace is `report.json` with these fields removed:
`metadata.run_id`, `metadata.source_revision`, `metadata.python_version`,
`measurements.wall_clock_ms` and `events[].run_id`. Action order, arguments,
recorded responses, events, simulated timestamps, assertions and outcomes are all
compared. A different seed changes operation keys, so its trace differs, and a
test checks that.

## Design notes

- **Trusted identity.** The harness binds each gateway or console to an `Actor`.
  Tool arguments are strict schemas that reject unknown fields, so a
  `"role": "reviewer"` field is a validation error, not a claim of authority.
- **Capabilities.** The controller gateway exposes nine agent tools.
  Grant/revoke approval, supplier status and clock changes live in separate
  privileged consoles. Those consoles refuse non-reviewer or non-harness actors
  and are never handed to the controller.
- **Approval binding.** An approval binds to `proposal_hash = sha256(canonical
  JSON of {request_id, supplier_id, budget_id, amount_minor, currency,
  description})` and to the policy version. The evaluator restates this formula
  rather than importing it, and a test enforces that the evaluator imports none of
  the decision modules.
- **Idempotency.** Keys are scoped per actor and tool. Same key with the same
  canonical payload replays the original result. Same key with a different
  payload returns `IDEMPOTENCY_CONFLICT` with no mutation. The logical intent
  (`PI-<request_id>`) is derived by the gateway, so a fresh key returns
  `DUPLICATE_INTENT`. SQLite also enforces unique intent, unique reservation per
  PO, one live reservation per request, idempotency key scope, and non-negative
  balances.
- **Money and time.** Money is stored as integer minor units, never floats. The
  clock is a synthetic UTC clock that advances one tick per operation.
  Wall-clock time is recorded only as a measurement.
- **State machine.** Workflow states are `NEW → VALIDATED → RESERVED →
  AWAITING_APPROVAL → READY_TO_COMMIT → FULFILLED`, plus `RECONCILING`,
  `REFUSED` and `ESCALATED`. They are enforced by an explicit transition table
  and journaled by the harness.

## Tests and scenarios

```bash
python -m pytest
python -m ruff check . && python -m ruff format --check .
```

Session 1 result: **87 tests passed**. Tests and scenarios are counted
separately. There is **one scenario** (S01 `happy-path`). The edge cases are
direct tool-boundary and evaluator tests, not scenarios: missing, expired,
revoked or impersonated approval; inactive supplier; insufficient funds;
idempotency; and evaluator negative controls. An autouse fixture makes network
connections and `.env` reads raise during tests.

## Limits / non-goals

- **No live AI agent.** Passing S01 shows only that this harness and its
  deterministic controller behave as specified on one synthetic fixture. It says
  nothing about how an LLM agent would behave, and it does not show that unseen
  cases are safe.
- **Session 1 scope only.** There is no disruption engine yet. The three core
  disruptions (supplier ineligible, lost purchase response, approval revoked
  before commit) are Session 2 work. There is also no frontend, API server, MCP
  adapter, decision forks, authentication, billing, deployment or external
  integration.
- **Not durable execution.** Runs are synchronous and in-process. Process
  restart/resume is not implemented or tested. Playback reads saved evidence only.
- **Local guarantees only.** Atomicity and idempotency rely on one local SQLite
  file. Real connectors would need to declare their own guarantees.
- **Evidence integrity.** `report.json` hashes describe inputs. They do not detect
  an attacker who rewrites both data and hashes. Hash-chained evidence is planned
  for Session 2, and it will be tamper-evident relative to a trusted digest, not
  tamper-proof.
- **English only.** Arabic is planned, not supported. It will come only after a
  competent reviewer verifies content and behaviour.
- **No real purchasing, payments, legal interpretation, or government system
  access**, now or as a goal.
- **License.** No license has been chosen yet. All rights are reserved by the
  owner until one is added.

See `docs/ADR-001-OFFLINE-KERNEL.md` and `docs/SESSION-1-CHECKPOINT.md`.
