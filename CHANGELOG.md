# Changelog

All notable changes to **Prusik** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and versions are `MAJOR.MINOR.PATCH`.

## [0.200.0] — phase-entry reality: briefs re-verify the world

Two field failures, one mechanism. A criteria.yaml may record
`ground_truth: {command, …}` captured from a real run (`prusik gate
ground-truth --feature F --capture`); sprint-start re-runs it and DRIFT
BLOCKS with a two-sided human diff, a `ground_truth_drift` ledger event,
and the exact re-baseline command — scope can no longer derive from a
brief whose facts the world outgrew. And `prusik watchdog` now probes
prove_red criteria for early-phase sprints (scoping/triage/planning):
ALL green on base means the goal may already be achieved outside the
sprint → one deduped `criteria_already_met` incident (close-or-rescope).
Both halves dormant unless authored — zero new ceremony otherwise.
Closes fb-4c542a24db7c, fb-8637c2416504
(moat-finding:fb-4c542a24db7c, moat-finding:fb-8637c2416504).

## [0.199.0] — push-or-park: completed work must reach origin, mechanically

New guard closing a field data-loss exposure: a COMPLETED 25-commit sprint
sat local-only for a week after a session collision. Entering reviewing or
integrating — and sprint-complete — now checks git's own push state
(upstream tracking + `rev-list --count`): unpushed work surfaces an
advisory naming the branch, ahead-count, and the exact push command, and
records an `unpushed_sprint_work` ledger event; `push_or_park: {require:
true}` in sprint-config blocks instead. `prusik watchdog` files an
`unpushed_sprint_work` incident when an active sprint at reviewing/
integrating has unpushed work. A repo with no remote is loudly
inapplicable — visible, never a silent skip, never a block. Prusik never
pushes for you; it detects and names the command. Closes fb-eef892a3e033
(moat-finding:fb-eef892a3e033).

## [0.198.1] — ordered lists are lists; Marketplace-ready action.yml

`schema.extract_list_items` now recognizes CommonMark ordered-list items
(`1. x`, `2) x`) at column 0 — a plan authored from prusik's own shipped
template (numbered Build order) no longer fails `prusik gate plan`. The
space after the marker is load-bearing: `3.5x faster` / `0.198.0 …` prose
stays uncounted; indented numbered lines stay nested. One shared parser,
8+ consumers fixed at once. Closes fb-664f701dc005
(moat-finding:fb-664f701dc005). Also: action.yml description ≤125 chars +
`branding` block (check-circle/purple) — the GitHub Marketplace publish
form's requirements.

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
