# Changelog

All notable changes to **Prusik** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and versions are `MAJOR.MINOR.PATCH`.

## [0.232.0] — The full-suite check names the phase it guards and credits the sentinel's captured run

- **The advisory names the phase being left (fb-6916f692908a).** `_full_suite_gate` guards
  both building exit and reviewing→integrating, but its text said "this building phase" for
  both. The advisory, the BLOCKED text and the `advance_blocked` reason now name the phase
  actually being left.
- **A green `prusik gate capture` of the full suite counts as proof.** The check credited only
  `prove_run`, so the regression-sentinel's full-suite capture, which is evidence prusik
  recorded itself, was ignored, and the advisory fired on reviewing phases that had run green
  (tests=1659, 1874). Both proof kinds now feed one candidate rule, read from the ledger:
  - a proven `prove_run`, or a `reviewer_execution_verified` tests event with `ok` true for
    this feature;
  - recorded after the latest entry into the phase;
  - full-sized per the learned baseline.

  A red, other-feature, pre-rewind, subset or non-tests capture is not credited. The remedy
  names both paths. Block semantics (`require_full_suite_at_build`, shared test-infra) are
  unchanged.
- **Skip-dirs are pruned relative to the walk root (fb-2a4222ba7797).** discovery's source
  walk, scan's file walk and verify_loop's test search matched skip-dir names
  (`build`, `dist`, `target`, `vendor`, `worktrees`, ...) against the absolute path. So a
  project whose root had an ancestor with one of those names pruned every file: an empty
  dep-graph, an empty blast radius, "no scannable files", and no error. Only the parts below
  the walk root are tested now. Found running the engine's own suite inside `worktrees/solo`.

## [0.231.0] — Model tiers: the engine decides the class, the adopter's profile names the model

- **Tier routing is engine policy (fb-4328a6e686ea).** Roles ran on whatever model the
  orchestrator spawned them with, and adopters who wanted cheaper routing hand-rolled it per
  project. New `prusik/model_tier.py`:
  - each packaged role has an engine-owned base class (`high_stakes`, `bounded` or
    `mechanical`), equal to its template frontmatter today, so a profile mapping the classes
    back to opus/sonnet/haiku reproduces today's routing exactly;
  - triage's existing signals score the sprint's stakes: size (S 0, M 1, L 2, XL 3), priority
    (P0 2, P1 1), team mode +1, and the profile's `risk_weights` matched in the scope's Domains
    and Risks. The lane comes from `sprint_started.lane`: the trivial lane, which never runs
    triage, scores from the brief instead (size 0, no team);
  - high stakes lifts a bounded role to high_stakes, low stakes drops a high_stakes role to
    bounded, and mechanical never moves. Pre-scope roles and pr-composer are fixed;
  - a team plan may re-tier a role with an optional `## Model tiers` section
    (`- <role>: <class> — <reason>`), shown as `source: override`.
- **The adopter's profile names the model, and fails closed.** Tiering is off until
  `.claude/sprint-config.yaml` sets `model_tiering: {enabled: true}`. Once it's on,
  `.claude/model-profile.yaml` must map all three classes to a model ref the host accepts at
  spawn, and set `stakes: {low, high}` and `risk_weights`. A missing, partial or invalid profile,
  an unknown role, or a standard-lane stakes-sensitive role resolved before triage is rc 2 with
  every problem named, never a default. Claude Code's Agent tool takes aliases only, so a
  pinned model id in a profile is rejected with that explanation; pin an id through the host's
  own alias settings.
- **`prusik tier resolve <feature> [--role R | --json]`.** `--role` prints exactly `model=<ref>`
  or `model=off` for the orchestrator, and it is the only call that records a `tier_resolved`
  ledger event, so a prediction is on record only where a spawn used it. The sprint-run,
  sprint-start and brief-new templates gain one rule at every spawn: run it with the feature
  slug (never `$ARGUMENTS`, which can carry `--trivial`), pass `model=<ref>`, and STOP on
  rc ≠ 0. A read failure and a record failure are named apart.
- **`prusik tier report [--json]`, advisory and read-only.** Per completed sprint with a
  prediction, per role: the predicted class and model, the measured models, and two flags:
  `not_applied` (tokens ran on a model the ref does not match) and `bounced_below_top` (a
  role below high_stakes whose authored scope.md/plan.md was REJECTED, or whose phase was
  rewound into; reviewer PASS/FAIL is never a bounce). Its flags never fail it. A record it
  cannot use is listed under `skipped` with the reason, and an input it cannot read at all is
  rc 2, never reported as `off` or as an empty history.
- **Actuals carry role × model.** `usage.measure` adds `by_role_model` to the sprint-complete
  snapshot. `usage.role_models(record)` is its only reader: absent reads as `{}`, and
  malformed raises with the defect named. `rollup` and the HQ export do not carry it.
- **Host knowledge stays in the adapter.** `PlatformAdapter` gains `spawn_model_refs()`,
  `pinned_model_note(ref)` and `model_matches(ref, observed)`. `agents_doctor` takes its
  frontmatter check from them, so it now also accepts `fable` and pinned or Bedrock ids, and
  still accepts everything it accepted before. A suite guard finds no model or provider
  name in `prusik/**/*.py` outside `platform_adapter.py` (adopter identity stays with the
  repo-wide pre-commit boundary check), and two fixture profiles
  (opus-topped and sonnet-topped) route the same signals to different models with no engine
  branch on adopter identity.
- **`schema.parse_sections` ignores headings inside code fences.** Found by this release's
  dogfood: a plan that showed the `## Model tiers` grammar in a fenced example had the rest
  of its prose read as overrides. A `## ` line inside a fence is now body text. Fences follow
  CommonMark: at most 3 spaces of indent, 3 or more backticks or tildes, a backtick fence's
  info string may not contain a backtick, and a fence closes only on the same character at
  least as long with no info string. Fence lines stay in the body, and an unclosed fence runs
  to the end. Of the repo's 79 markdown artifacts, only this release's plan parses
  differently.
- **The engine dogfoods it.** This repo enables tiering with its own `.claude/model-profile.yaml`
  (low 2, high 6; hook 1, ledger 1, migration 2, security 2). `prusik tier resolve
  model-tier-policy` at integrating:

```
model tiers: model-tier-policy (lane standard, stakes high, score 6)
  size L: +2
  priority P1: +1
  team mode: +1
  risk 'hook': +1
  risk 'ledger': +1
  score 6 >= high 6 -> high
role                  class        model   source
backend-builder       high_stakes  opus    stakes
brief-critic          mechanical   haiku   fixed
cartographer          high_stakes  opus    fixed
conventions-enforcer  high_stakes  opus    stakes
conventions-scribe    bounded      sonnet  fixed
feature-planner       high_stakes  opus    stakes
frontend-builder      high_stakes  opus    stakes
integrator            high_stakes  opus    stakes
plan-critic           high_stakes  opus    stakes
pr-composer           mechanical   haiku   fixed
product-fit-critic    high_stakes  opus    fixed
regression-sentinel   high_stakes  opus    stakes
scope-critic          high_stakes  opus    fixed
scoping               bounded      sonnet  fixed
test-writer           high_stakes  opus    stakes
```

## [0.230.0] — A capture whose tree changed mid-run is never recorded

- **Evidence is stamped with the tree it ran against (fb-97beccfb14a4).** `prusik gate capture`
  hashed the judged tree only after the command returned, so an edit landing mid-run was
  stamped onto output from the code before it. In the usage-rollup sprint, a fix landed during
  the regression-sentinel's full-suite capture; the entry carried the post-fix hash, and
  `verdict-current` carried the pre-fix PASS forward. `capture` now snapshots the judged tree
  before and after the run and stamps the pre-run hash. The hash and the judged file set are
  unchanged, so every existing comparison keeps its meaning.
- **A mid-run change is non-evidence, through the registry.** A new `capture_diagnose`
  detector, `worktree_changed_mid_run`, refuses the capture whatever its exit code: it exits
  1, records no entry, and logs `capture_non_evidence`. Prior evidence stays untouched, with
  or without `--reset`. The message names up to 5 changed paths and both fixes: re-capture
  once the tree is settled, or, when the command itself writes those files (a
  `--fix`/format/codegen step, or a test artifact that is not gitignored), run that step
  outside capture or gitignore the artifact. Writes to gitignored files never trigger it.
  `CaptureResult` gains `changed_paths`, defaulting to `()`.

## [0.229.0] — Token usage rolls up per product, briefing included

- **One rollup of a product's measured usage.** `usage.rollup(records)` is a pure function over
  the ledger's `sprint_complete` events. It reads no transcript, so it outlives the host's
  transcript cleanup. It returns:
  - `sprints`, `measured` and `unmeasured` counts, and `unmeasured_reasons`. A sprint completed
    before v0.228.0 counts as `pre-measurement`;
  - `tokens` and `median_tokens` (`None` with no measured sprint);
  - `by_phase`, `by_role` and `by_model`, summed over measured sprints;
  - `by_month` and `per_sprint` (in ts order).
  It never carries `by_session`, and a non-int snapshot value is skipped, never fatal.
- **`prusik effort` reads the rollup.** `effort.summarize_phase_tokens` is deleted.
  `tokens_by_phase` (text and `--json`) comes from `rollup(records)["by_phase"]`, and
  `usage.py` is the only reader of a snapshot's `by_phase`.
- **The export carries usage.** `prusik report --export` gains a top-level `usage` block in
  the rollup's shape. It is anonymized by default: each per-sprint entry drops `feature`, and
  `by_role` keeps only the packaged prusik roles, `main` and `subagent`, summing every other
  role (a project's own agent, a host agent type) into `other`. `--full` keeps it
  verbatim. `usage.packaged_roles()` is the one source of the packaged-role set.
  `EXPORT_SCHEMA_VERSION` stays `1.0`; no key is removed or renamed.
- **Briefing is attributed to the sprint it briefs (fb-1e3ecda9d011).** The work before
  `sprint_started` (the brief and the brief-critic) was never counted, measured at 15–33% on
  top of the recorded cost. Now:
  - the first write to `briefs/<slug>.md` or `briefs/<slug>.criteria.yaml` records a
    `briefing_started` ledger event. It is system-recorded through one recorder,
    `gate.record_briefing`, with two sources: the PreToolUse hook (Write/Edit/notebook targets
    and Bash redirect targets, and only for a write the gate allowed) and `prusik serve`'s
    brief form;
  - the active sprint's own brief opens no window. `.sprint/briefing.json` dedups by slug, and
    `sprint_start` clears the slug's entry so a later re-brief opens a fresh window;
  - the hook never raises or blocks a tool call. Every recorder fault appends
    `briefing_record_failed` with its source, so a degraded recorder is never silent. The
    pre-tool import set is unchanged, and a non-brief call pays only a substring check;
  - effort's timeline turns the latest `briefing_started` into a `briefing` span that the
    feature's `sprint_started` closes. It starts no earlier than the latest closed span of any
    feature, so it never overlaps a completed sprint and no token is counted twice. A
    `briefing_started` with no `sprint_started` yields nothing, and a re-started sprint with no
    new brief opens none;
  - so `sprint-complete`'s measured `actual.tokens` includes briefing, shown as
    `by_phase["briefing"]` (the brief-critic under `by_role["brief-critic"]`). A feature's
    wall-clock still starts at `sprint_started`. The showcase dossier lists `briefing` first.
- **Known limits.**
  - Exploration before the first brief write has no feature identity yet, so it is not
    attributed.
  - The briefing window starts `measure`'s coverage at `briefing_started`, not
    `sprint_started`. A registered session last seen inside that window whose transcript the
    host has since cleaned up now makes the sprint `measured=False` (`transcript unreadable`).
    This is loud, recorded as `tokens_unmeasured`, never a silent undercount.
  - `metrics.events` and the export `timeseries` now include `briefing_started` and, on a
    recorder fault, `briefing_record_failed` (additive, no key changed). `metrics.phases`,
    overhead and bounce can now show a `briefing` phase.

Closes fb-1e3ecda9d011 (moat-finding:fb-1e3ecda9d011)

## [0.228.0] — Token usage is measured from the session transcripts

- **Phase budgets work, and the cost of a sprint is measured (fb-a1b7db140a5e).** The watchdog's
  `budget_tokens` check summed a `tokens` field no ledger event carried. `actual.tokens` came
  only from an agent-passed `--tokens`, which no sprint ever set. So every budget was a silent
  no-op, and no sprint's cost was ever recorded. Now:
  - **Registration.** The SessionStart and Stop hooks record each session's transcript in
    `.sprint/sessions.json`. They touch nothing else, and the Stop import set and timing are
    pinned.
  - **Reading usage.** Usage is read from Claude Code's own transcripts, the main one plus its
    subagents.
    - Rows are deduplicated by message id across every session, keeping the per-field maximum.
      Claude Code writes partial counters per content block, and resumed sessions copy rows.
    - A malformed or truncated row is counted, never fatal.
    - `tokens` is input + output + cache writes + cache reads.
  - **Attribution.** Usage is attributed to the feature's ledger phase windows (rewinds and the
    open phase included), by role and by model. A general-purpose agent's role is taken from
    its description. Usage is also broken down by session, so a concurrent session's share is
    visible.
- **`sprint-complete` records the measured value.** `actual.tokens` is the measured total, and
  `actual.usage` holds the breakdown.
  - When usage can't be measured, it records `tokens_unmeasured: <reason>` and prints
    UNMEASURED. It never blocks completion.
  - `--tokens` is still accepted but ignored, because it is an agent claim.
- **`gate advance`** prints the measured usage of the phase just left, against its budget when
  one is set.
- **The watchdog enforces budgets.** It files `budget_exceeded` with the by-role, by-model and
  by-session breakdown. When a budget can't be measured, it files `budget_unmeasurable` with
  the reason. Each incident is filed once per phase until it is resolved.
- **The shipped default budgets are retired.** They were guesses of 150k–1M. Measured phases
  run 0.5M–94M tokens (medians 1.6M–22M across three projects), so the defaults were 10–100×
  low under any definition.
  - The template and the fastapi-postgres preset no longer set them.
  - `prusik refresh` deletes exactly the `budget_tokens` lines that still equal a retired
    default, and names each one. Every other byte of the file is kept.
  - Any other value is yours and is now a live budget.
  - A layout refresh can't edit safely (flow style, a merge key) is named for you to delete by
    hand.
  - To set a budget, run `prusik effort`, which now shows tokens per feature and per phase, and
    set `budget_tokens` on a phase from those numbers.
- **Known limits.**
  - A session that ended before this upgrade and never ran a later hook is invisible, so the
    first sprint after the upgrade can read as unmeasured.
  - `watchdog.check(root)` with an explicit root other than the project root reports
    `budget_unmeasurable` rather than comparing two projects (fb-5b83acbaab4a).

Closes fb-a1b7db140a5e (moat-finding:fb-a1b7db140a5e)

## [0.227.1] — The test suite never touches the real project ledger

- **Engine test infrastructure only. No runtime change (fb-f3f196654458).** conftest dropped
  `CLAUDE_PROJECT_DIR` but left cwd at the repo root, so `ledger.project_root()` walked up to the
  engine's own `.sprint/`. Every full run appended 29 synthetic events (fake `prove_run`s, tiny
  dep-graphs, tmp briefs) to the engine's real ledger, and the advance-time full-suite advisory
  read one as "the proven run executed 7 test(s)". Now:
  - The process leaves the repo before collection, and each test starts in its own empty cwd.
  - Resolving a ledger path inside the repo fails the test immediately.
  - An end-to-end test runs the original offenders from a fake repo's cwd and requires its
    ledger byte-identical.
- `test_eval_scorecard` now scores the shipped template config in a project of its own. It had
  been reading the engine's self-hosting config through the leaked cwd.
- The engine's own ledger was purged of 2,648 test events: 387 real events kept, backup kept.
  The ledger stays local; HQ never collected the engine ledger.

Closes fb-f3f196654458 (moat-finding:fb-f3f196654458)

## [0.227.0] — A carried-forward verdict never overwrites a newer report

- **A fresh reviewer FAIL is no longer replaced by "PASS (carried forward)" (fb-00c98b8c70c6).**
  Carry-forward rewrote a present approval file whose verdict line wasn't the gate token
  whenever the ledger's last positive verdict matched the current hash. That held even when
  the file was written *after* that verdict, which is the case when the reviewer-artifact
  fallback writes a FAIL report without recording it. Now a report newer than the recorded
  verdict is never overwritten. The gate stays unmet and says to re-run the role. Applies to
  all four carry-forward gates: scope-approval, plan-approval, regression and conventions.
- **`prusik gate verdict-current` agrees with the exit gate.** Every verdict role runs it first
  to decide whether to skip its dispatch. It now re-gates when the role's approval file (found
  from the sprint config's exit artifacts) is newer evidence. The re-run the gate asks for
  therefore actually happens, and there is no stall. Both read one shared carry decision.
- Unchanged: a missing approval file, a carry marker, and a report older than the verdict all
  still carry forward. Execution-evidence checks are untouched. Fail-closed edge: a report
  whose mtime is bumped without new content (e.g. a checkout) reads as newer, so the role
  re-runs once.

Closes fb-00c98b8c70c6 (moat-finding:fb-00c98b8c70c6)

## [0.226.0] — Python module names follow the source root

- **src-layout projects get real import reach (fb-63d1c3a15af1).** The dep-graph named every
  Python module by its path from the project root, but a src-layout project imports `pkg.mod`
  from `src/pkg/mod.py` — so its from-import edges never matched, and plan-time import reach
  predicted **no** at-risk tests at all on a src-layout design partner. Now a top-level `src/`
  with no `__init__.py` is the Python source root (unless the project imports `src.*`
  absolutely — then nothing changes), decided once per graph and recorded in its stats. Module
  names, relative-import resolution and from-import candidates all use that one naming. On the
  design partner: +41 edges, 0 removed, and a change to its core domain module now predicts 93
  importing files (was 0).
- **A changed package `__init__.py` has import reach (fb-35f64503cc75).** It was queried as
  `pkg.__init__` and matched nothing; `pkg/__init__[.py]` now means the package itself — every
  `import pkg` / `pkg.sub` importer (package init runs on any submodule import).
- **Strictly additive.** `blast_radius` keeps every query it made before and adds the mapped name
  only for real Python module paths — verified over 2,084 queries on three repos: no hit lost,
  and every non-Python path (e.g. a TypeScript `src/config.ts`) answers exactly as before.
  Projects whose `src/` is a package, or that import `src.*`, keep their graphs unchanged.
- A dep-graph cached by an earlier version (no source-root decision) is rebuilt once on the next
  query, never misread.

Expect larger, real predictions: a plan touching a package `__init__.py` now flags the package's
importers (the engine's own `prusik/__init__.py`: ~140 tests).

Closes fb-63d1c3a15af1 (moat-finding:fb-63d1c3a15af1), fb-35f64503cc75
(moat-finding:fb-35f64503cc75).

## [0.225.0] — the Python dep-graph sees `from pkg import module`

- **Blast radius and at-risk-test prediction find from-import importers (fb-27ad79ae220b,
  HIGH).** The Python extractor recorded `from pkg import module` as an edge to `pkg` alone and
  skipped relative imports entirely — so `blast_radius`, and the plan-time at-risk-test
  prediction whose honesty IS the graph's recall, missed every importer written in the most
  common Python idiom. On the engine itself `ledger.py` had 18 attributable importers out of 75.
  Now one rule covers absolute and relative imports: the resolved base is always recorded, and
  each imported name adds `base.name` **only when it is a real project module or package**
  (including PEP 420 namespace dirs) — so attributes and externals (`typing.Any`) never become
  graph nodes. Relative imports resolve against the importing file's package exactly as Python
  does (verified against `importlib.util.resolve_name` over 84 cases); root-level and
  above-root relative imports are dropped, never recorded as bare names. Strictly additive on
  real repos (engine: +483 edges, 0 removed) and no slower (one directory walk).
- **Expect larger, real predictions.** A change to a widely imported module now flags many more
  importing tests (e.g. a `gate.py` change: 3 → ~38) in the plan's at-risk set, in
  `blast-verify`, and in the fix-round fan-out lane — those were real importers the gates were
  missing.
- `discovery.build_graph` returns the graph in memory (`dep_graph` persists it), and
  `blast_radius` accepts an optional in-memory `graph=`.

Known limit, filed: src-layout projects (`src/pkg/…` imported as `pkg…`) don't benefit yet
(fb-63d1c3a15af1).

Closes fb-27ad79ae220b (moat-finding:fb-27ad79ae220b).

## [0.224.0] — a reviewer verdict binds to the code it judged

- **Main-tree builds no longer carry stale reviewer verdicts (fb-3fbf1846ac92, HIGH).**
  Reviewer verdicts and `gate capture` evidence were fingerprinted over `worktrees/` only. In
  a main-tree build (no builder worktrees — every engine self-build, and any adopter sprint
  that skips worktrees) that was a fingerprint of nothing: a constant. So a stale PASS
  survived any code change — `verdict-current` said carry it forward, evidence captured before
  a fix read fresh, and the exit-artifact check could **rewrite a reviewer's fresh FAIL report
  as "PASS (carried forward)"** (reproduced: a `FAIL / 2 regressions` report became
  `PASS (carried forward … @ e3b0c44298fc1c14)`). Now, when no builder worktree holds judged
  files, the fingerprint covers the project's own source: git's tracked +
  untracked-not-ignored files under the project root (also for a project in a subdirectory of
  a larger repo, and non-ASCII names), or for a non-git project a walk that never enters
  VCS/virtualenv/derived dirs. Worktree builds fingerprint as before.
- **A carried-forward marker counts only while it is current.** Rewinds leave `reports/` on
  disk, and the evidence check waived re-capture for any report whose text said "carried
  forward". Both the evidence waiver and the `PASS`/`APPROVED` verdict line now honour a
  carry marker only while what it judged is unchanged — and only the engine's exact marker
  shape counts (a fresh report whose prose says "(carried forward, not merge-blocking)" is
  judged on its own verdict line, as before). Writer and reader share one marker definition.
- **Nothing judged, nothing bound.** `record-verdict` refuses when a reviewer judged zero code
  files — naming the likely cause (e.g. the project is gitignored by a parent repo) — and no
  carry-forward is granted over an empty set.
- **One orchestration list.** `decisions/` and `findings/` (state written mid-sprint by triage
  and `prusik feedback`) join `.sprint/`, `reports/`, `design/`, `.claude/`, `briefs/` in a
  single list shared by the fingerprint and worktree assembly, so writing a finding mid-review
  no longer re-gates the reviewers.

Old ledger verdicts bound to the empty fingerprint simply stop matching (a one-time,
conservative re-gate).

Closes fb-3fbf1846ac92 (moat-finding:fb-3fbf1846ac92).

## [0.223.0] — the per-tool-call hooks pay only for what they run

- **Hooks are ~35% faster (platform plan P0, sprint 1).** Every tool call spawns
  `prusik gate pre-tool` and `post-tool`. Profiling showed only ~27ms of the ~84ms median
  was the interpreter — prusik owned the rest: building the full ~108-subparser CLI before
  dispatching, importing modules the hooks never execute, and the pure-Python YAML loader.
  Now an exact `prusik gate <hook>` dispatches before the parser is built (any other argv
  falls through unchanged); `gate.py` imports `schema` / `consistency` / `discovery` only
  inside the functions that use them; and the sprint config parses with libyaml's C safe
  loader when available (identical results — verified on 112 real configs). Measured on the
  same machine: median 84.5ms → 52–55ms; prusik-owned overhead ~57ms → ~25ms (≈0.9x a bare
  interpreter). Zero behavior change: hook rc/stdout/stderr byte-identical across the fast
  path, the old dispatch and the console script; the eval scorecard equals its pre-change
  snapshot.
- **The budget is a CI invariant.** `tests/test_hook_hot_path.py` pins it deterministically —
  per-hook import allowlists measured across payload classes and phases (incl. `reviewing`
  with an active fix round), every lazy import bound by a leading local import (a branch-local
  import can't fail a deny branch open), YAML loader parity incl. malformed input and the
  no-libyaml fallback — plus a coarse min-of-N wall-clock catch (prusik-owned ≤ 1.75x the
  bare interpreter; it was 2.37x).

## [0.222.0] — the session banner names the feedback channel

- **`prusik feedback` is named where the agent always looks (fb-392958c3076a).** An
  adopter's in-session agent reached a concrete, prusik-directed design conclusion and
  parked it in scratchpad + local memory, filing it only after two operator nudges. The
  SessionStart banner — the one prusik surface the main agent always reads — pointed at
  briefs and `/sprint-start` but never at the feedback channel. It now carries one line,
  in every state, with a copyable invocation:
  `prusik feedback "<one line>" --kind bug|friction|request --detail "<what happened>"`.
  The advertised form is tested against the real CLI parser for each kind, so the banner
  can't advertise a command that doesn't run. Existing banner lines (incl. the
  template-skew banner) are unchanged and keep their order.

Closes fb-392958c3076a (moat-finding:fb-392958c3076a).

## [0.221.0] — a deferred test tier must have a destination

- **Sprint-complete now enforces where deferred tests run (fb-afbe9deead21, HIGH).**
  Reviewing deliberately excludes `reviewing_defer_markers` tests (live-server /
  browser smokes: pre-integration the running server is still the old binary) and
  defers them to sprint-complete — but only a brief's own criteria ever ran them there.
  A sprint whose criteria never selected the marker ran that tier nowhere before done,
  and a real regression shipped exactly that way (an adopter's integration-only
  criteria; caught only by post-merge live smoke). Now, when tracked tests **apply** a
  deferred marker (decorator / `pytestmark`, detected by parsing — a marker that only
  appears as text never counts), sprint-complete **blocks** until each such tier has
  prove-wrapped green evidence on the integrated tree: a passed criterion that selects
  it, or a proven `prusik prove -- <cmd> -m "<marker>"` run recorded for this feature
  after it entered integrating. An all-skip run (server down) is NOT PROVEN by design;
  an old or feature-less run never counts. The block names the unproven markers and
  the exact remedy, and is classified (`gate_class: deferred_tier`) so catches/bounces
  measure it. Projects with no applied deferred marker are unaffected.
  **Blocking by default** — unlike the advisory-default terminal gates, this restores a
  check prusik itself moved out of reviewing. Opt-outs: `deferred_tier: {command:
  "<your smoke run>"}` credits your server-managed command; `deferred_tier: {require:
  false}` opts out loudly (printed + recorded).
- **One pytest `-m` evaluator.** Three consumers (the criteria lint, brief-lint's
  browser-criterion check, the new gate) carried their own substring rules and
  disagreed. `schema` now owns one evaluator with three defined predicates — *selects*,
  *targets*, *excludes* — evaluated as pytest does (last `-m` wins; flags after the
  pytest token; wrapper runners' args read; an interpreter's module flag never). Two
  intended verdict changes: `-m 'not slow or browser_smoke'` no longer reads as
  excluding the marker, and a behaviour-dir criterion that deliberately targets a
  deferred marker (`pytest tests/behavior -m browser_smoke`) now lints clean — it is
  the sprint-complete criterion the role templates prescribe. Substring false matches
  (`not browser_smoke_x`, `notebook_browser_smoke`) are corrected.
- `prusik prove` ledger events now record the command and the active sprint's
  feature/phase, so a proof is attributable.
- Role templates (regression-sentinel, integrator, backend/frontend-builder) and the
  sprint-config template state the enforced destination and the remedy.

Closes fb-afbe9deead21 (moat-finding:fb-afbe9deead21).

## [0.220.0] — auto-sequence the product-fit critic; stop the false wrong-tree warning

Two adopter-filed harness fixes:

- **product-fit-critic is now auto-sequenced (fb-8295dd5d4859).** The sprint-start flow
  ran the product-fit *form* check, passed, then told the human to run the
  product-fit-critic **separately** and re-run — a 3–4 round-trip dance that recurred
  every sprint (corroborating the 67% `sprint_start_gate` rebounce that `bounces
  --since` flagged). brief-critic / scope-critic / plan-critic were already
  auto-invoked in the command templates; product-fit-critic was the odd one out.
  `/sprint-start` and `/sprint-run` now invoke it right after the form check (with the
  same reviewer-artifact fallback), so a FAIL surfaces the specific coherence gap
  (usually an uncited overlapping sibling) in one pass instead of a form-pass followed
  by a manual critic and a re-run. Same rigor, fewer round-trips.

- **No more false wrong-tree warning (fb-d3adb6e68c43).** `gate capture` printed its
  "ran at PROJECT ROOT — measured main instead" warning even when the command itself
  embeds `cd worktrees/<role> && …` and correctly stamps the worktree hash — a false
  alarm that cost reviewer time re-checking the evidence file. The warning is now
  suppressed when the command self-navigates into a worktree; a genuine root-run without
  that `cd` still warns.

Closes fb-8295dd5d4859 (moat-finding:fb-8295dd5d4859), fb-d3adb6e68c43
(moat-finding:fb-d3adb6e68c43).

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
