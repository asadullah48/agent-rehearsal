# ADR-001: Offline, LLM-independent simulation and evaluation kernel

- Status: Accepted (Session 1)
- Date: 2026-10-17

## Context

Agent Rehearsal tests whether an agent can complete an authorized business task,
preserve invariants when something fails, and leave enough evidence to explain
the outcome. Such a test is only as trustworthy as the simulator that applies
effects and the evaluator that judges them. If those parts depended on a language
model, results would vary from run to run, could not be reproduced offline, and
could be shaped by the system being tested.

## Decision

1. **The kernel is plain Python with no agent framework.** The world model,
   policy, gateway, persistence, evaluator, report and CLI use only Pydantic v2
   and the standard library (`sqlite3`, `argparse`, `hashlib`).
2. **The gateway is the only path to effects.** A controller proposes typed
   actions. The gateway is bound to a trusted actor by the harness, checks
   capabilities, strict argument schemas and the policy at the commit boundary,
   and commits each mutation with its events in one SQLite transaction. Reviewer
   and scenario operations live in separate privileged consoles that a controller
   never receives.
3. **The evaluator is independent of the decision path.** It opens the database
   read-only, rebuilds the authorization state at each commit event from the
   fixture plus the ordered event log, and restates the proposal-hash formula
   itself. It does not import `policy`, `tools`, `store`, `privileged`,
   `controller` or `runner`; a test enforces this. This way a defect in the
   gateway's decision function cannot also hide itself in the verdict.
4. **Session 1 ships only a deterministic reference controller.** It is labelled
   as deterministic in the CLI, report and README, and it is never presented as an
   AI agent. Its role is to exercise the harness so the harness can be checked.
5. **Negative controls are part of the design.** Test-only corruption helpers
   build unauthorized commits and inconsistent budgets. These include commits
   after revocation, at exact expiry, with a controller-authored approval, or with
   a suspended supplier. Each control must make the evaluator fail. A calibration
   control (a forged but otherwise valid commit) must pass, which shows that each
   failure comes from the targeted defect.

## Why live adapters come later

- **Reproducibility first.** Evaluator verdicts must be stable before they are used
  to judge non-deterministic agents. Same-seed reruns of the offline kernel give
  equal canonical traces, and that is the baseline a live run is compared against.
- **Honest labelling.** A live adapter needs a recorded model ID, adapter version,
  usage, cost provenance and a clearly separate "live rerun" mode. Adding it before
  the kernel is verified would invite results where the deterministic controller is
  mistaken for, or quietly substituted for, an AI agent.
- **Security boundary.** Adapters must not read SQLite, fixtures or evaluator
  expectations. Defining that contract (Spec §17) is easier once the gateway and
  the evidence model are fixed.
- **No credentials in the default path.** The offline kernel and its tests need no
  network, API key or `.env`. That keeps CI and the default demo free of secrets.

## Consequences

- Session 1 results speak only about the harness and the deterministic controller,
  not about how any LLM behaves.
- A later live adapter (Spec §6 suggests an eve-based TypeScript process, subject to
  validation) will plug in behind the same gateway and evaluator. It will need its
  own ADR for framework, provider and version choices.
- The evaluator restates some logic that also exists in the policy module, such as
  the proposal-hash formula and the expiry rule. This duplication is deliberate, so
  that the evaluator does not inherit the gateway's judgement. The happy path passes
  only when both copies agree. Nothing yet checks the two copies against each other
  across many generated inputs; that is a candidate for later property-based tests.
