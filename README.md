# Prusik

[![PyPI version](https://img.shields.io/pypi/v/prusik)](https://pypi.org/project/prusik/) [![Python versions](https://img.shields.io/pypi/pyversions/prusik)](https://pypi.org/project/prusik/) [![License](https://img.shields.io/pypi/l/prusik)](https://github.com/getprusik/prusik/blob/main/LICENSE)

**Proof, not opinion.** Prusik verifies that your agent's work actually happened — from the tool's own output, never the agent's word. Start with one zero-ceremony command; scale to a full autonomous harness for Claude Code agent teams when you're ready.

## Prove your agent's tests actually ran (30 seconds, zero config)

No `init`, no config, no buy-in — install and prove:

```bash
pip install prusik

prusik prove -- pytest -q
prusik prove --kind types -- mypy src/
prusik prove --min 20 --json -- pytest tests/
```

Prusik wraps any test/lint/type command and proves it *really ran clean* — from the tool's own output, not the agent's word. The exit code reflects the truth: `0` only when the command exited 0 **and** real work was observed.

The case it exists to catch — *exit 0 but zero tests executed* ("tests pass ✅" when nothing actually ran: auto-skip, no collection, wrong path) — fails with `rc=1`. Drop it into CI or a pre-push hook as a one-line anti-fabrication check. That's the whole pitch; everything below is opt-in from here.

## Why this exists

Claude Code can coordinate agent teams, but agents fabricate: they report "tests pass ✅" when nothing ran, burn tokens on malformed work, lose context across sessions, and freelance outside their scope. Prusik converts discipline into enforcement — hooks block out-of-phase writes, schemas reject shallow artifacts, triage routes solo-vs-team deterministically, a watchdog catches stuck teammates, and a ledger records every transition for audit and self-tuning. Every check grounds in deterministic evidence, not an LLM's opinion of its own work. `prove` is the wedge; the full harness is the same principle applied end-to-end.

## The full harness (opt-in from here)

Beyond `prove`, prusik scaffolds into a project as a phase-gated harness for Claude Code agent teams: writable-path enforcement, schema-validated artifacts, deterministic triage, a watchdog, and a ledger that closes the feedback loop. `prusik init` sets it up; nothing below is required to use `prove`.

## Adopt / pause / remove

```bash
cd your-project
prusik init                                                    # scaffold .claude/, briefs/, design/, .sprint/
prusik init --conventions ~/workspace/python/best-practices    # ... with a convention pack

prusik status                                                  # show current state / active sprint
prusik disable                                                 # pause hooks without removing files (reversible)
prusik enable                                                  # resume

prusik uninstall                                               # remove only what prusik installed (preserves user edits)
prusik uninstall --force                                       # also remove files you modified
prusik uninstall --keep-artifacts                              # keep .sprint/ (ledger, inventory, dep-graph)
```

`prusik init` writes `.claude/.prusik-manifest.json` tracking every file it creates with a content hash. `prusik uninstall` uses that manifest to remove only prusik-written files — any custom agents, commands, or config tweaks you added stay put. The `.gitignore` block prusik adds is wrapped in markers so it can be cleanly unwound too.

Your project now has:
- `.claude/settings.json` — PreToolUse/Stop/SessionStart hooks wired to `prusik gate`
- `.claude/sprint-config.yaml` — phase FSM + triage heuristics + pre-sprint gates + issues + watchdog settings
- `.claude/agents/*.md` — role library (cartographer, scoping, planner, builders, reviewers, integrator, brief-critic)
- `.claude/commands/*.md` — slash commands (`/brief-new`, `/sprint-start`, `/sprint-advance`, `/sprint-status`, `/sprint-complete`, `/sprint-watchdog`, `/issues-sync`, `/brief-form`)
- `.claude/schemas/*.yaml` — brief + scope schemas
- `briefs/`, `design/`, `reports/`, `decisions/`, `worktrees/`, `.sprint/`

## The flow (one feature, end-to-end)

```
/brief-new email-receipts                              # 5-field wizard; writes briefs/email-receipts.md
prusik discovery all                                       # (first time or stale) inventory + dep graph
/sprint-start email-receipts                            # brief-critic runs; brief validated; scoping begins
<scoping role writes design/email-receipts/scope.md>
/sprint-advance triage --feature email-receipts         # pure-code solo vs team routing
/sprint-advance <solo_execute | planning> --feature email-receipts
<builders in worktrees; heartbeat every N turns>
/sprint-advance reviewing                               # regression-sentinel + conventions-enforcer PASS
/sprint-advance integrating                             # integrator merges; pr-composer drafts PR
/sprint-complete email-receipts --tokens N --duration-min M
```

At each step prusik enforces:
- **Writable paths** by phase (PreToolUse hook)
- **Exit artifact existence + schema** before advancing (advance gate + Stop hook)
- **Required sections + must_contain tokens** (plan approval, regression PASS, etc.)
- **Cross-reference integrity** (scope.md's declared modules must exist in the repo)
- **Pre-sprint gates** (brief-critique PASS before scoping)

## What's in prusik

### Engine (`prusik/`)

| Module | Purpose |
|---|---|
| `evidence.py` | The anti-fabrication primitive: `executed_count` (real work from the tool's own output) + `prove_verdict`. Shared by `prove` and `gate capture` |
| `prove.py` | `prusik prove` — standalone "did it actually run clean?" gate (no FSM) |
| `schema.py` | Loads YAML schemas; validates briefs, scopes, triage decisions; cross-refs module paths against the repo |
| `ledger.py` | Append-only `.sprint/ledger.jsonl`; richer `digest()` with outcome stats |
| `phases.py` | Phase FSM engine; writable-path resolution; sprint state on disk |
| `gate.py` | Hook + CLI entry points: `pre-tool`, `stop`, `session-start`, `advance`, `brief`, `scope`, `sprint-start`, `sprint-complete` |
| `discovery.py` | Inventory + dep graph (plugin-based) |
| `discovery_plugins/` | `python` (ast), `javascript`/`typescript`/`jsx`/`tsx` (regex), `go` (regex) |
| `triage.py` | Pure-code routing from `scope.md` + `brief.md` to `decisions/<feature>.json` |
| `watchdog.py` | Polls heartbeats, phase staleness, budget — files incidents under `.sprint/incidents/` |
| `issues.py` | Tracker sync coordinator |
| `issue_plugins/` | `github` (via `gh` CLI), `linear` (stub) |
| `serve.py` | Tier-3 GUI: stdlib `http.server` rendering a form from the brief schema |
| `init.py` | `prusik init` — scaffolds prusik into a target project |

### Templates (`prusik/templates/` — copied by `prusik init`)

- `.claude/` — settings.json, sprint-config.yaml, agents/, commands/, schemas/
- `artifacts/` — brief/scope/plan/retro skeletons

## Commands

| Command | What it does |
|---|---|
| `prusik prove [--kind tests\|lint\|types] [--min N] [--json] -- <cmd>` | Prove a command actually ran clean (no FSM); rc=0 only if proven |
| `prusik scan [--detectors a,b] [--no-local-detectors]` | Static detectors (binding-mismatch, test-reach) + your own from `.claude/detectors/*.py` |
| `prusik init [--conventions PATH] [--force]` | Scaffold into current project |
| `prusik gate pre-tool` | PreToolUse hook: block writes/bash outside phase |
| `prusik gate stop` | Stop hook: block session end if phase artifacts missing |
| `prusik gate session-start` | SessionStart hook: inject active sprint context |
| `prusik gate advance <phase> --feature F` | Advance FSM, verify exit artifacts |
| `prusik gate brief <path>` | Validate a brief against the schema |
| `prusik gate scope <path>` | Validate a scope artifact (plus cross-ref) |
| `prusik gate sprint-start <feature>` | Begin a sprint (runs pre-sprint gates) |
| `prusik gate sprint-complete --feature F [--duration-min M] [--tokens N] [--escalated]` | Close, record predicted vs actual |
| `prusik discovery inventory \| dep-graph \| all` | Deterministic discovery |
| `prusik triage --feature F` | Pure-code solo/team routing |
| `prusik issues sync` | Pull issues from configured tracker |
| `prusik issues search "query"` | Cheap keyword search over synced issues |
| `prusik watchdog [--poll MIN]` | One-shot or polling heartbeat/budget watcher |
| `prusik serve [--port N]` | Launch local web form at 127.0.0.1:N |
| `prusik status` | Current phase, writable patterns, exit artifact status |
| `prusik digest` | Ledger summary: outcomes, escalation rate, prediction error, gate blocks by phase |
| `prusik metrics [--since ISO] [--json]` | Defect-prevention scorecard: what prusik flagged/caught/blocked (factual ledger counts) |

## Concepts

### Engine vs opinions

Prusik ships the enforcement engine (language-agnostic). Opinions come from **convention packs** — directories with a `conventions.yaml` manifest. A target project can ingest one or more packs; each pack's content becomes a convention baseline that roles read from.

### The phase FSM

Declared in `.claude/sprint-config.yaml`. Each phase specifies:
- `writable` — glob patterns the PreToolUse hook enforces
- `deny_bash` — regex patterns for shell commands blocked in this phase
- `exit_artifacts` — files (with optional schema/section/content validators) required before advancing
- `budget_tokens` — advisory budget; watchdog files an incident if exceeded

### Pre-sprint gates

A sprint can't start until declared gates pass. Default: `brief_critique` requires `reports/<feature>/brief-critique.txt` containing `PASS` (produced by the `brief-critic` role). Disable via `pre_sprint_gates.brief_critique.enabled: false`.

### Triage

`prusik triage --feature <name>` is pure-code. Reads `design/<feature>/scope.md` + `briefs/<feature>.md`, applies heuristics from `sprint-config.yaml`, writes `decisions/<feature>.json`. Zero LLM tokens.

### Ledger + digest

Every phase transition, gate block, triage decision, watchdog incident, and sprint completion writes to `.sprint/ledger.jsonl`. `prusik digest` surfaces:
- Sprint outcomes and **solo→team escalation rate**
- **Mean prediction error** for duration and tokens
- **Gate blocks by phase** (where are the rough edges?)
- Triage mode distribution
- Watchdog incident counts by kind

This is how prusik becomes self-tuning: if escalation rate climbs or prediction error drifts, tune the heuristics in `sprint-config.yaml`.

For the team-lead view — *what has prusik actually caught?* — `prusik metrics` turns the same ledger into a defect-prevention scorecard: binding-mismatches flagged, non-runs/failures caught by the evidence gate, review fix-rounds, out-of-phase writes blocked, verify-loop closure rate. Every number is a recorded event (factual, not a modeled "bugs prevented" claim); `--json` feeds a dashboard or trend.

### Input surfaces (GUI)

Three ways to author a brief. Same schema; same canonical `briefs/<slug>.md` output.

| Tier | Surface | Audience | Shipped? |
|---|---|---|---|
| 1 | `/brief-new` slash command wizard | Engineers using Claude Code | ✓ |
| 2 | `prusik gate brief <path>` (validate hand-written) | Engineers outside CC | ✓ |
| 3 | `prusik serve` — local web form at 127.0.0.1:8765 | Non-engineer stakeholders | ✓ |
| 4 | Issue tracker template (labels → enums) | Teams living in Linear/GitHub | Sketch — see `issues.tracker` config |

## Cohesion safeguards (v0.3)

Three mechanical defenses against scope drift across features, each running automatically:

1. **Map staleness detection.** `prusik discovery fingerprint-map` snapshots the dep-graph when `design/map.md` is written. The `map_freshness` pre-sprint gate compares the current dep-graph against the fingerprint and blocks `/sprint-start` if drift exceeds `max_drift_pct` (default 30%). Forces the cartographer to refresh before a new feature sprint runs on a stale map.

2. **scope-critic reviewer.** `scoping` phase now requires `reports/<feature>/scope-approval.txt` to contain `APPROVED`. Mirrors `plan-critic` but for the scope artifact. Catches missed blast radius, overreached size, hollow risks — at the cheapest possible stage.

3. **Cross-artifact consistency checks.** `prusik gate advance` now runs deterministic checks between artifacts:
   - `plan_within_scope` — plan.md's modules ⊆ scope.md's modules; scope creep caught at planning→building advance
   - `builder_writes_within_plan` — files written under `worktrees/*/` must fall inside plan.md's declared modules (tests exempted); catches rogue builders at building→reviewing advance
   - `brief_type_matches_scope` — `bug_fix`/`doc`/`config` with L/XL size is flagged at scoping→triage advance

## Compared to other harness / agent tooling

Prusik operates at one specific layer — **process discipline for build-time agent teams.** Other tools occupy adjacent layers; prusik composes with them, doesn't try to absorb them.

| Tool | Layer | What it does | When to use it INSTEAD of prusik |
|---|---|---|---|
| **[learn-harness-engineering](https://github.com/walkinglabs/learn-harness-engineering)** | Pedagogy | Course + skill that teaches how to build a harness; ships templates, not a runtime | If you want to LEARN harness design before adopting one. Prusik is the runtime adopters reach for after the course. |
| **[sentrux](https://github.com/sentrux/sentrux)** | Architectural measurement | Real-time codebase quality signal (5 root-cause metrics, MCP integration) | If you want continuous architectural quality feedback. **Composes** with prusik via `behavior_regression.command = "sentrux gate ."` |
| **[helmor](https://github.com/dohooo/helmor)** | Local desktop UI | Tauri-based workbench wrapping Claude Code SDK + OpenAI Codex SDK; SQLite-backed sessions | If you want a GUI for managing agent sessions. Prusik is headless / hooks-based; orthogonal layer. |
| **[future-agi](https://github.com/future-agi/future-agi)** | Production observability | OTel-native tracing + 50+ eval metrics + gateway + guardrails for live agents | If you need production runtime observability. Prusik is build-time-only; future-agi is run-time. |
| **[GitHub Spec Kit](https://github.com/github/spec-kit)** | Pre-planning | Generate detailed specs and plans before writing code | If you prefer waterfall-style design-then-implement. Prusik chose the opposite posture: ship narrow on recurrence trigger, defer until evidence. |
| **Bare Claude Code** | None | Just the CLI, no harness | If your project is small / one-off and the agent works fine without enforcement. Prusik's value compounds with project complexity. |
| **[LangGraph](https://github.com/langchain-ai/langgraph) / [AutoGen](https://github.com/microsoft/autogen) / [CrewAI](https://github.com/crewAIInc/crewAI)** | Multi-agent orchestration framework | General-purpose multi-agent coordination libraries | If you're building a custom agent runtime. Prusik is opinionated for Claude Code specifically; these frameworks are agent-agnostic but require you to engineer the harness yourself. |

**Prusik's strength:** opinionated, discipline-first, multi-agent decomposition with critic-actor separation, recurrence-trigger framework that prevents speculative complexity. Phase FSM + schema-validated artifacts + ungameable signal design.

**Prusik's gaps (honest):**
- **Claude Code-coupled.** Hooks contract is CC-specific. Codex / Cursor / other agent runtimes need a different harness.
- **Single-active-sprint.** `.sprint/state.json` assumes one developer-with-AI per checkout. Multiple developers running parallel agent sessions on the same repo isn't modeled.
- **Python-AST-privileged.** First-class discovery for Python; regex-based for JS/TS/Go; unsupported for Rust/Java/Ruby (until tree-sitter discovery lands, planned but recurrence-gated).
- **No UI.** Prusik is CLI + hooks. If you need a visual treemap or session browser, pair with helmor or sentrux.
- **No production runtime.** Prusik's job ends at sprint-complete. Production observability is future-agi's job.

**The composition story:** prusik's `behavior_regression` and `project_policy` blocks (v0.7.1, v0.8.0) explicitly invoke arbitrary commands during reviewing. This is the integration point with sentrux (`sentrux gate .`), with project pre-commit pipelines (`pre-commit run --all-files`), with browser smoke (`pytest -m browser_smoke`), and with anything else that exits non-zero on failure. **Prusik doesn't replace these; it consumes them.**

If you're not sure which layer you need, run `prusik doctor` after `prusik init` — it scores your harness across five subsystems and points at the lowest one with a concrete next step.

## Design limits (honest)

- **Depth is not gateable.** Schemas catch structural issues; they can't catch shallow thinking. Reviewer roles (`brief-critic`, `scope-critic`, `plan-critic`, `conventions-enforcer`) add judgment in isolated context windows, but a human reviews `design/` weekly.
- **Experimental surface.** `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` is still experimental; prusik pairs teams with worktrees and treats teams as ephemeral sprints, not long-running processes.
- **Non-Python dep graphs are regex-based.** Good-enough for scoping; not a real parser. Replace with tree-sitter per language if accuracy matters.
- **Watchdog runs out-of-band.** Prusik ships the command; installation (via `/schedule`, cron, or a polling terminal) is left to the adopter to match their environment.
- **Linear plugin is a stub.** Implement `prusik/issue_plugins/linear.py` with the Linear GraphQL API when you need it.

## Self-hosting

Prusik develops itself with prusik. `.claude/settings.json` and `.claude/sprint-config.yaml` at repo root wire hooks for work on prusik source. The self-host config includes extra writable paths for `prusik/**` and `tests/**` during `solo_execute`/`building` phases — so prusik-on-prusik development doesn't fight its own gates.

## Repository layout

```
prusik/                    — engine
  discovery_plugins/       — per-language graph builders
  issue_plugins/           — per-tracker sync
  templates/               — copied into target projects by `prusik init`
    .claude/               — settings, sprint-config, agents, commands, schemas
    artifacts/             — brief/scope/plan/retro skeletons
.claude/                   — self-host: prusik's own config
examples/greenfield/       — demo of `prusik init` against a fresh project
tests/test_smoke.py        — 17 smoke tests covering every engine module
```

## License

Apache 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
