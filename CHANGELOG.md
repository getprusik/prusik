# Changelog

All notable changes to **Prusik** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and versions are `MAJOR.MINOR.PATCH`.

## [0.198.0] — `prusik overhead`: the harness proves its own cost

New read-only command completing the overhead-to-catch ratio (`catches` is
the value side): active-vs-idle time per phase (silence beyond `--idle-min`
splits out as idle, never read as harness cost), per-gate block→retry cost
(capped at the idle threshold so an abandoned block can't read as hours),
fix-round loop cost, `--json` stable keys, `--ledger PATH` for field-ledger
backtests (tolerant parse; skipped lines surfaced, never silent), and
`--hook-bench` measuring the real PreToolUse hook latency. Span math reuses
`effort.extract_spans` — one source of truth. Nothing-to-measure exits 1:
an absent ledger is unknown overhead, not zero. Verified against a real
design-partner ledger (249 sprints). Closes fb-e7fe8177cc8c
(moat-finding:fb-e7fe8177cc8c).

## [0.197.38] — docs-only

README positioning pass (reaches the PyPI long description): independent
verification framed as a structural requirement of the agentic SDLC;
deterministic verdicts (pure function of the tool's own output) and
derive-don't-store ticket state stated explicitly; the composite GitHub
Action (`action.yml`, `source: prove` as a PR gate) surfaced with a usage
block; honest-limits gains the runner-coverage boundary (pytest/vitest;
mypy/tsc/ruff/eslint). Fixes an `action.yml` input-description typo.
No engine changes.

## [0.197.37] — see the GitHub Releases for per-version notes

Public open-core engine — deterministic, evidence-based build harness for
autonomous coding agents (FSM sprint gates, execution-evidence verification,
adversarial critics, fix-round convergence control, blast-radius prediction).
Apache-2.0. Release notes: https://github.com/getprusik/prusik/releases
