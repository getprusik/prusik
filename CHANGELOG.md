# Changelog

All notable changes to **Prusik** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and versions are `MAJOR.MINOR.PATCH`.

## [0.206.0] — capture runs in the tree it stamps (no wrong-tree greens)

`prusik gate capture` forced the command's working directory to the project
root, unconditionally. So a worktree-scoped capture — `cd worktrees/solo &&
prusik gate capture … -- <cmd>` — ran `<cmd>` against **main**, while the
evidence hash stamped the **worktree** file-set. The dangerous direction: a
capture scores GREEN against main while the reviewed worktree code is broken —
hash-bound evidence that is provably about the wrong tree, a laundered
false-green in the one layer that must never lie (fb-caff9937144e). This is the
worktree-awareness `prove` got in v0.197.21, never carried to `capture`.

Now capture runs the command in the **invocation cwd** when it is inside the
project (so the `cd worktrees/solo` the agent already did is honored), falling
back to root otherwise and never running outside the repo. Every evidence entry
records `exec_dir` (the tree the command actually ran in) as provenance, and a
worktree-mode sprint that captures at the project root gets a **loud warning**
naming the wrong-tree risk and the `cd worktrees/<role>` remedy. The warning is
not a hard block — a reviewing/integration capture legitimately runs against the
integrated root, so the recorded `exec_dir` is what makes a genuine wrong-tree
capture auditable rather than a false-refusal. Closes fb-caff9937144e
(moat-finding:fb-caff9937144e).

## [0.205.0] — the writable-scope remedy shows the whole boundary

The first **measured actuation** off the v0.204.0 bounce baseline: `writable_scope`
was the fleet's worst-re-bouncing gate (up to ~70% — an agent re-hitting the
write-lock location after location). The old deny named only the blocked path
and, at most, one redirect for that one path — so an agent that didn't know the
phase's writable *set* guessed a new path on each rejection.

Both writable-scope deny sites (Write/Edit target and bash redirect) now route
through one `_writable_scope_deny_msg`, which shows the **whole** writable set for
the phase at once, the concrete worktree route for this target, and an explicit
"don't retry other locations — each is the same block." One write can now be
valid on the first try instead of the fifth. The `gate_class` is unchanged
(`writable_scope`), by design: re-running `prusik bounces` over the next sprints
measures whether the rewrite actually drops the rate — the remedy's effect is
proven, not asserted. The redirect-arrow (`→`) operator contract is preserved and
now pinned behaviorally rather than by a source-line grep.

## [0.204.0] — remedy quality is measurable: the repeat-bounce lens

A gate block is a teaching moment — it stops the agent and names a fix. If the
agent re-hits the *same gate class* within the *same sprint*, the remedy didn't
land: it bounced off the fence twice, a wasted fix-round with a known cause.
That re-bounce is a signal on the **remedy**, the value-side companion to what
`catch_quality` is for the gate — and until now it was invisible.

Two pieces make it a first-class, retrospective measurement:

- **`gate_class`, a stable key on every block event.** New `prusik/gate_class.py`
  owns the closed set of block classes (writable-scope, unmet-exit-artifacts,
  cross-artifact-inconsistency, full-suite-not-proven, rewind-guard, push-or-park,
  deny-command, writer-lease, sprint-start-gate). Every `gate_blocked` /
  `advance_blocked` / `sprint_start_blocked` site now self-labels its class
  (a source-scan test is the forcing function, mirroring `capture_diagnose`'s
  KNOWN_MODES parity), so the key survives a remedy *reword* — which is exactly
  what a free-text-`reason`-derived key could not. `classify()` also derives the
  class from a legacy event's own fields (`missing=`/`inconsistencies=`/reason),
  so the entire pre-field ledger still classifies; a block that recorded nothing
  identifying is honestly `unclassified`, never guessed.
- **`prusik bounces`** — a read-only report: per gate class, fires vs. wasted
  re-bounces vs. rate, worst-remedy-first, so the highest-waste remedy gets
  rewritten first and the rewrite's effect is *provable* (re-run, watch the rate
  fall) rather than asserted. Retrospective over the append-only ledger — no new
  sprint required. The same read feeds the proportional-rigor question: a class
  that re-bounces on low-blast work is a ceremony smell.

Baseline read across the fleet's existing ledgers: ~half of all block events
were wasted re-bounces, dominated by the writable-scope class (rate up to ~70%)
— a concrete, ranked remedy-rewrite queue turned from a hunch into a number.
Read-only, no gate behavior changed; this is the measurement phase before any
remedy is touched.

## [0.203.0] — a stale bundler cache is a false-RED, not evidence

The capture classifier gains a third registered non-evidence mode:
`stale_bundler_cache`. After a lockfile-affecting dependency bump, vite's
`node_modules/.vite` pre-bundle cache survives `turbo run <task> --force`
(--force busts turbo's OWN cache, never vite's) and serves stale pre-bundles
that fail to resolve packages pnpm just re-linked — a false-RED that read as a
real test failure (fb-7fb7e0cfd21b: a whole spec set "failed" on
`Failed to resolve import "next/navigation"` until `.vite` was cleared).

`prusik gate capture` now refuses to record it and names the remedy (clear
`node_modules/.vite` in every workspace, then re-capture). Keyed on vite's
import-analysis failing a **bare** specifier only — a relative-path
(`./missing`) resolution failure is a genuine code break and passes through
untouched. Sound by construction: self-correcting — a stale cache goes green on
a cache-cleared re-capture, a genuinely-unresolved dependency stays red and
correctly routes to a dependency fix, so a real failure can never be masked.
Registered the module's one way (detector + KNOWN_MODES + test), not a new
branch in `gate.capture()`. Closes fb-7fb7e0cfd21b
(moat-finding:fb-7fb7e0cfd21b).

## [0.202.0] — conventions sweep + map content re-verify

Two field findings, one release. `prusik scan` gains the
`convention-rules` built-in: adopters declare mechanical conventions in
`.claude/conventions/rules.yaml` (`forbid` line-regex and `pair`
file-level classes, glob-scoped) and the sweep names every violation
file:line repo-wide — diff-gating stops new debt, the sweep retires old
debt; an invalid rule is itself a loud finding, and no rules file means
fully dormant. Closes fb-7f0bc1640914 (moat-finding:fb-7f0bc1640914).

And the map can no longer quietly lie about the subsystem a sprint
changed: sprint-start snapshots design/map.md section hashes;
sprint-complete flags — advisory, by name, with a `map_reverify_flagged`
event — every section that references a touched file yet stayed
byte-identical. Advisory by design: prose truth is not gateable; the flag
names its evidence for a human. Closes fb-cda76f07f7e8
(moat-finding:fb-cda76f07f7e8).

## [0.201.0] — CI credit is a claim about execution

A green check can no longer credit a spec CI never runs. New `ci_exec`
resolves every workflow test invocation's executed file set via the
RUNNER'S OWN resolver (`playwright --list` / pytest `--collect-only`) —
the YAML arg list is never treated as truth. At sprint-complete, a
`verify_in: ci` criterion referencing spec file(s) (explicit
`ci_executes:` or extracted from its verify commands) is REFUSED credit
when any referenced spec is outside the CI-resolved union — before the
green is even consulted — with `ci_execution_refused`/`_verified` ledger
events. New `prusik scan` built-in `ci-orphan-specs` names every on-disk
e2e spec no CI invocation executes; an unresolvable invocation (dynamic
`${{ }}` args, runner absent on this host) is loudly UNRESOLVED and
counts as covering nothing — a sweep that can't see never says "no
orphans". Closes fb-39bd12ff439b (moat-finding:fb-39bd12ff439b).

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
