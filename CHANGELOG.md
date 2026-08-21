# Changelog

All notable changes to **Prusik** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and versions are `MAJOR.MINOR.PATCH`.

## [0.219.0] — a capability-shipped fix no longer false-closes an adopter's ticket

Proof-transfer closes an adopter finding on a **version floor** (`engine >= fix
version`) when the fix carries a `moat-finding:` marker. That is sound for an
**engine-only** fix — a parser or gate fix takes effect the moment the engine ships.
It is a **false-close** for a fix that ships a **capability requiring adopter opt-in**
(a config flag, a declared list): the moat test proves "engine *can*, when configured",
not "the adopter's repro passes", so the ticket auto-closes while the field gap persists
until the adopter opts in. v0.218.0 hit exactly this — a `ui-e2e-check` config option
shipped and its adopter finding version-floor-closed, but the adopter never declared the
config, so the under-match still reproduced (fb-33e4be4542ff).

The fix is a distinct marker, `moat-capability:`, for opt-in-gated fixes. It is
**deliberately excluded from `_closures.json`** (the version-floor closure map), so such
a finding can never proof-transfer-close on `engine >= X` — it closes on the adopter's
own real verify. A guard test pins the invariant `capability_ids ∩ _closures.json = ∅`
(and that the two marker verbs are disjoint), so the false-close class cannot recur.
fb-436a9cf46e37 is retagged `moat-capability:` and removed from the manifest
accordingly; the ui-e2e capability itself (v0.218.0) is unchanged.

Closes fb-33e4be4542ff (moat-finding:fb-33e4be4542ff).

## [0.218.0] — ui-e2e-check honors the project's declared rendered surface

`prusik ui-e2e-check` matched rendered-behavior files by **extension** (`.tsx`/`.vue`/
…), so a non-component file that reverse-imports *into* rendered components — a `.ts`
store or api module — was a false negative: sprint-mode returned `{"ui_files": [],
"flagged": false}` and silently auto-passed the local reviewing gate, while the
project's *CI* gate (carrying its own rendered-extra list) correctly flagged the same
change. The two modes of one gate disagreed, which masks a real gap on any project
whose CI doesn't independently re-check (fb-436a9cf46e37).

`ui-e2e-check` now also honors a project-declared glob list,
`ui_coverage.rendered_surface_extra` in sprint-config, so a declared reverse-importer
flags in sprint-mode too — converging with CI-mode. Absent → extension match only, so
existing projects are unchanged; keep the list in sync with your CI's rendered-extra
set (ideally one source).

Ships the opt-in capability requested in fb-436a9cf46e37
(moat-capability:fb-436a9cf46e37) — the engine can now honor a declared list, but the
adopter's field gap closes only once they DECLARE it (or harden their own wrapper), so
this is deliberately NOT a version-floor close (see v0.219.0 / fb-33e4be4542ff).

## [0.217.0] — proof-transfer for the CI-observe fix reaches the reporter's ticket

v0.216.0 shipped the CI-deliverable-observation gate under a freshly-filed engine
finding id, but the adopter's own ticket for it carries a different content-addressed
id (same issue, different text → different hash), so the fix wouldn't proof-transfer to
close their ticket. The moat test now co-tags the adopter's finding id, so
`prusik update` closes it by version-floor like any other. No behavior change — the
gate is identical to 0.216.0.

Closes fb-556f5caebef2 (moat-finding:fb-556f5caebef2) — the adopter-side twin of
fb-e340cd203897.

## [0.216.0] — a CI-job deliverable must be observed running, not just merged

A sprint whose deliverable *is* a CI job/workflow could complete `done` with the
deliverable's **runtime never observed**: a job triggered only on `pull_request` /
`schedule` / `workflow_dispatch` never fires on the sprint's push to its branch, so the
reviewing gate (real for the code) is systematically hollow for the job as a
deliverable — and an adopter shipped a nightly whose *first* real run was RED (a broken
deliverable, green under review). This is the wired≠observed class (fb-41877c6a453f)
one meta-level up: the sprint that builds a CI gate never observes the gate
(fb-e340cd203897).

At **sprint-complete**, prusik now diffs the workflows the sprint changed (against the
`base_commit` recorded at sprint-start, so it's independent of push state) and reads
each one's `on:` triggers — handling GitHub's YAML gotcha where a bare `on:` key parses
as the boolean `True`. A changed workflow whose triggers can't fire on a push to the
sprint's branch is a **deliverable never observed**: advisory + a
`ci_deliverable_unobserved` event by default, a hard block under `ci_observe: {require:
true}`. The escape is proof, not assertion — `prusik gate mark-ci-observed <workflow>
--run <id>` records it only after `gh` confirms that run concluded **success**; it
refuses a non-green run (that's exactly the broken deliverable) or an absent `gh`. A
job that also runs on `push` is observed by the normal CI run and never flagged.

Closes fb-e340cd203897 (moat-finding:fb-e340cd203897).

## [0.215.0] — the product-fit parser tolerates prose, like scope/brief already do

An adopter's well-formed `product-fit.md` was rejected across ~4 sprint-start iterations
with cryptic errors (fb-d4d401114c19). Two modes, one root cause: `product_fit._bullets`
was a **naive re-implementation** of bullet extraction that never routed through
`schema.extract_list_items` — the one extractor (used by 8+ consumers) that requires a
**space** after a `*` marker. So a markdown emphasis span at line start (`*wiring*`) was
parsed as a phantom bullet with its leading `*` stripped, and `## Related` slugs were
never split from trailing prose or stripped of `**markdown**`. This is the exact
brittleness class the scope/brief parsers already fixed (fb-6a4075fb15fe et al.), which
re-surfaced because a new artifact bypassed the shared parser.

- `_bullets` now delegates to `schema.extract_list_items` — emphasis is prose, not a
  bullet, and sub-bullets / HR / ordered items are handled uniformly.
- `## Related` extracts the leading slug up to the first `:`/`—`/` - ` delimiter
  (internal hyphens in slugs preserved) and strips markdown wrappers via
  `artifact_variants`, so `**beta-ready-rebase** — the source` resolves to the slug.
- The errors now name the fix (cite a pillar id; use a bare slug; put prose after a
  delimiter and non-brief notes in an HTML comment) instead of echoing the mangled
  string. A genuinely missing brief still blocks.

Closes fb-d4d401114c19 (moat-finding:fb-d4d401114c19).

## [0.214.0] — `bounces --since`: measure a remedy on the post-fix cohort

`prusik bounces` computes per-class re-bounce rates over the **entire** append-only
ledger (dozens of sprints per class). So the remedy-quality loop's promise — rewrite the
worst remedy, re-run, watch the rate drop — didn't hold: one post-fix sprint adds a
handful of events against a ~190-event history, and the rate stays frozen (49% before a
fix, 49% after), while the classes a fix targeted may not even fire in a single sprint
(fb-efb97d03d9d9). The cumulative view can't attribute an improvement to a fix.

`prusik bounces --since DATE` windows block events (by their ledger `ts`) to the sprints
run **on the fixed engine** — pass the day you upgraded. The rewrite's effect is then
measured on the post-fix cohort as a clean before/after instead of drowned by history;
a class that re-bounced heavily in the past but not since the fix now reads clean. The
window states `N of M block events` so the sample size is explicit, and a version-shaped
`--since` is rejected with a pointer to the date form (a version has no offline date in
an adopter repo). Read-only and retrospective, like the base report.

Closes fb-efb97d03d9d9 (moat-finding:fb-efb97d03d9d9).

## [0.213.0] — success criteria carry evidence by default, not by opt-in

The fleet dashboard showed `criterion_evidence` and `criterion_prove_red` had **never
fired** across any adopter. The cause was not that criteria go unwritten (they are,
heavily) — it was that `kind:` and `prove_red:` are opt-in and **no criterion declares
them**, so a local `verify_command` was trusted on exit code alone: a vacuous verify
(exit 0, nothing executed) passed, and an acceptance test that was never RED proved
nothing (fb-d34fb5d5c7a5). Same shape as fb-c76 — a discipline that exists but isn't
the default doesn't happen. Two fixes make evidence the default:

- **Inferred `kind:`.** When a local criterion doesn't declare `kind:`, it's inferred
  from a test/lint/type-shaped `verify_command` (`pytest`/`vitest`/`mypy`/`tsc`/`ruff`/
  `eslint`), so the false-clean guard arms automatically — a pytest verify that exits 0
  having run nothing now FAILS. Only tools the evidence parser reliably counts are
  inferred; a bare `tsc` is augmented with `--extendedDiagnostics` (fb-c76ae6da2255) so
  a silent-clean typecheck still emits its file count; opaque `npm`/`pnpm` wrappers are
  never inferred (they'd risk false-failing a clean run). A `criterion_kind_inferred`
  event makes the newly-armed criteria visible to the fleet.
- **`prove_red` shift-left.** `prusik gate brief-lint` now warns (advisory) when a
  `new_feature` brief's local acceptance criteria never declare `prove_red` — an
  acceptance test that was never red is vacuous-green. It nudges marking the
  new-behavior criteria and capturing a RED baseline before implementing, and leaves
  regression / CI-verified criteria alone.

`charter_freshness` was examined and is **not** part of this — it's a benign preventive
advisory that correctly hasn't fired (the fleet's charters are fresh).

Closes fb-d34fb5d5c7a5 (moat-finding:fb-d34fb5d5c7a5).

## [0.212.0] — the identity gate covers the engine's own tickets

`boundary_check` (the open-core identity gate) excluded `findings/` from its
public-surface scan, treating it as private plane. That is correct for an adopter's
own private repo, but wrong for the **public** engine: the engine's own dogfooding
tickets are committed to `getprusik/prusik`, so a ticket that names the adopter which
surfaced it is a public identity leak — and two did, undetected, for weeks
(fb-ac17c362d5c6).

`findings/` is now scanned public surface: a committed ticket carrying an adopter name
fails the gate at commit time. The id→adopter crosswalk stays in the private plane
(prusik-hq / the adopter's own repo, where this gate does not run); the public engine's
tickets cite by `fb-<id>` only. The two leaked tickets are scrubbed to a generic
"Adopter walkthrough" in HEAD. (The names remain in prior public history — a separate
maintainer decision, deliberately not rewritten under the covers.)

Closes fb-ac17c362d5c6 (moat-finding:fb-ac17c362d5c6).

## [0.211.0] — the cross-artifact block tells you how to clear it

`prusik bounces` (the v0.204.0 remedy-quality lens) ranked
`cross_artifact_inconsistency` at 19/34 = **56% wasted re-bounces** — tied for the
worst remedy-quality rate in the fleet and, unlike `unmet_exit_artifacts` and
`writable_scope`, targeted by nothing (fb-25937f6926fa). Root cause: the advance-block
message listed *what* had drifted but never named the fix-route or said that a bare
retry is futile, so agents re-ran `prusik gate advance` unchanged and bounced
identically.

Rewritten in the v0.205.0 `writable_scope` mold: the remedy now states plainly that
the block **re-fires byte-identically until the drift is gone on disk**, and names the
source-of-truth decision per drift-type — an out-of-boundary file goes back into scope
or into `design/<feature>/deviations.md`; a worktree cache dir is deleted now; a brief
type/size conflict is fixed in the brief. The `worktrees_clean_of_cache_artifacts` item
now leads with the immediate delete instead of trailing next-sprint prevention advice.
The gate class and its detection are unchanged **by design**, so re-running `prusik
bounces` after the next sprints measures the remedy's effect as a clean before/after.

Closes fb-25937f6926fa (moat-finding:fb-25937f6926fa).

## [0.210.0] — a verdict gate reads the verdict line, not any line

The reviewing exit gate bound `regression.txt` with `must_contain: PASS` — but that
was a **substring-anywhere** check. A stale FAIL-bodied report left over from a prior
fix-round, whose fix-instructions merely *mentioned* "PASS" ("re-run until it reports
PASS"), satisfied the gate, and `gate advance integrating` succeeded on a report whose
verdict was FAIL (fb-d3c6dd0da1e6). `conventions.txt` carried the identical binding
and only blocked by luck — its FAIL body happened not to contain the token; the
scope/plan `APPROVED` artifacts had the same latent hole (a "NOT APPROVED, revise…"
body would have passed).

Every producing role spec mandates the verdict on the **first line** ("first line must
be exactly PASS/FAIL", "APPROVED/REJECTED"). The gate now matches the **leading token
of the first non-empty line**, not a substring anywhere — `_verdict_line_ok`, applied
to both the exit-artifact gate and the pre-sprint gate. A verdict buried in prose no
longer satisfies; carry-forward's `<token> (carried forward — …)` line still passes.
This is a pure engine fix keyed to a shipped field, so it reaches the whole fleet on
`pip install -U prusik` with no per-project config change — and it hardens conventions
and the APPROVED artifacts at the same time, not just the one that was caught.

Closes fb-d3c6dd0da1e6 (moat-finding:fb-d3c6dd0da1e6).

## [0.209.0] — a push is confirmed against the remote, not the local ref

The integrator merged correctly, then launched `git push` as a **background** task
and returned while "waiting." The process died with its turn: origin never moved, the
`retro.md` exit artifact was never written, and a completed sprint sat on one disk
(fb-60d5c11b2f99). The safety net held (the exit-artifact gate + push-or-park blocked
the close), but two gaps remained: the integrator fire-and-forgot an outward,
irreversible operation, and the push check trusted the **local** `@{upstream}` cache —
which a killed or forged tracking ref leaves stale while origin never received a thing.

- **Remote-truth confirmation.** `push_guard.remote_confirm` consults the remote
  itself (`git ls-remote`), not the local tracking ref. At `sprint-complete` — where
  the push has supposedly landed — a branch that reads *parked* locally but whose
  `HEAD` origin does **not** carry is surfaced as `push_unconfirmed` and, under
  `push_or_park: {require: true}`, **hard-blocks** the close. Scoped to the terminal so
  the frequent pre-push advance checks pay no network cost; offline degrades **loudly**
  to the local signal, never a silent pass.
- **Integrator spec.** The push must run **synchronously** in the foreground — never
  backgrounded — observe the pre-push hook chain, confirm `git ls-remote origin
  <branch>` equals `HEAD`, and report the pushed `before..after` range. A general rule
  is added: never fire-and-forget an outward, irreversible operation (push, release,
  deploy) and return — you cannot observe its result and the process dies with the turn.

Closes fb-60d5c11b2f99 (moat-finding:fb-60d5c11b2f99).

## [0.208.0] — a silent-clean typecheck counts on the first capture

`tsc` prints nothing on a clean typecheck, so `prusik gate capture --kind types --
tsc --noEmit` recorded `types=0` — a false-clean the advance gate correctly rejected
as "nothing measurable ran." The reviewer then had to manually re-capture with
`tsc --noEmit --extendedDiagnostics` (which prints `Files: N`) and hand-delete the
stale `types=0` entry — a round-trip that recurred **every** reviewing phase
(fb-c76ae6da2255, a confirmed re-bounce source).

Capture now augments a trailing bare `tsc` under `--kind types`: it appends
`--extendedDiagnostics` so a genuine clean typecheck emits its file count and counts
on the FIRST capture. The augmentation is narrow and auditable — it fires only when
the command's last statement is `tsc` with no diagnostics flag already present, and
the recorded evidence command shows the appended flag (with a stderr notice), so a
reviewer sees exactly what ran. Scoped to `tsc`: `mypy` prints `N source files` and
`ruff` is loud on an empty scope, so they already count; a silent lint runner is left
for a demand-pulled follow-up. Closes fb-c76ae6da2255 (moat-finding:fb-c76ae6da2255).

## [0.207.0] — CI credit: wired is not observed-green

v0.201.0 made a `verify_in: ci` criterion prove its spec is **wired** into a
resolvable CI job (the runner's own resolver). But a spec that's wired into
`ci.yml` and never actually *run* is not evidence — a wired-but-unrun spec ships
latent bugs that surface on the first real CI execution (fb-41877c6a453f, a
recurring high-cost class). The credit now separates the two halves:

- **WIRED** — `credit_check` passing is recorded as `ci_execution_wired`, a
  *necessary* precondition, no longer the misleading `ci_execution_verified`.
  Calling wiring "verified" was the false comfort that let unrun specs read as
  passed.
- **OBSERVED-GREEN** — a criterion is credited only when its `ci_verify_command`
  actually ran **green**, recorded as `ci_observed_green` bound to the
  integration commit + the specs it covers (the "reference to an observed green
  run" a static wiring check can't supply; a stale green on an older commit is
  now distinguishable).

A `verify_in: ci` criterion whose spec is wired but has no green-attesting
command is refused as **UNVERIFIED**, not PASS, with that exact framing. Closes
fb-41877c6a453f (moat-finding:fb-41877c6a453f).

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
