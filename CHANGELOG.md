# Changelog

All notable changes to **Prusik** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and versions are `MAJOR.MINOR.PATCH`.

## [0.197.3] — 2026-07-12

Initial public release of the **Prusik** open-core engine — a deterministic,
evidence-based build harness for autonomous coding agents. Core capabilities:

- **FSM sprint gates** (brief → scope → plan → build → review) with fail-closed enforcement.
- **Execution-evidence verification** — a claim of "done" must carry reproducible proof
  (real, ungameable executed counts), not prose.
- **Adversarial critics** — scope, plan, architecture, conventions, and test-craft review.
- **Fix-round convergence control** — bounded retries with escalate-to-human on stall.
- **Blast-radius prediction** — predicted-at-risk tests must be verified, not passed vacuously.

Apache-2.0.
