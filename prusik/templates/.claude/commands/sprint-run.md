---
description: Drive one feature end-to-end from brief through sprint-complete using real agents, with optional bridge management (when on), phase transitions, and failure handling. Replaces typing a multi-line sprint prompt.
argument-hint: <feature-slug>   # ONE arg only — slug like `domain-schema`, NOT free-form prose
allowed-tools: [Bash, Read, Agent]
---

## Step −1 — pre-flight: validate the feature slug

**Before any other step**, verify `$ARGUMENTS` is a clean prusik feature slug. The slug must match `^[a-z][a-z0-9-]*$` (lowercase letter, then lowercase alphanumeric and hyphens).

**Two distinct failure modes — diagnose by checking `$ARGUMENTS` first:**

### Case A: `$ARGUMENTS` is empty or whitespace-only

Almost always means the user typed `/sprint-run` with no slug at all (forgot the argument). If you suspect a different cause — for example, you invoked this command via the `Skill` tool from inside an agent loop and saw empty-slot rendering — note that pre-v0.6.6 prusik's slash-command templates used `$1` instead of `$ARGUMENTS`, which CC does NOT substitute. v0.6.6+ uses `$ARGUMENTS` consistently (matching the v0.6.3 `/sprint-pause` precedent), so empty-slot rendering should now only happen when the args are genuinely empty. **STOP and tell the user**:

    /sprint-run got an empty feature slug.

    Re-invoke with the slug:

        /sprint-run domain-schema

    If you're seeing empty-slot rendering despite passing args
    (e.g. via Skill tool), check that your prusik version is ≥0.6.6
    — pre-v0.6.6 templates used $1 which CC doesn't substitute
    (B11 in domain-schema bridge).

### Case B: `$ARGUMENTS` is non-empty but doesn't match the slug regex

Usually means the user passed multi-token prose (em dash, free-form guardrails, smart quotes, mixed case, etc.). **STOP and tell the user**:

    /sprint-run takes exactly one argument: the feature slug.
    Got: `$ARGUMENTS`.
    Expected: lowercase alphanumeric + hyphens (e.g. `domain-schema`).

    Operator notes / guardrails / on-rails framing belong in
    briefs/<feature>.md (## Notes section), NOT in /sprint-run args.
    The brief is the intent contract; this slash command takes only
    the slug.

In either case: **do not render or execute any subsequent step. Do not run any Bash command. Do not invoke any Agent.** Surface the error with the right Case (A or B) message and wait for the user.

The prusik engine also rejects invalid slugs at `prusik gate sprint-init` and every `--feature` argument (v0.6.4+) — defense-in-depth — but catching here saves the user a longer trace and an aborted bridge entry.

---

Run the full sprint for feature `$ARGUMENTS` end-to-end. This is the "just ship it" command — it owns every phase transition so you don't have to.

**Default assumptions** (do NOT ask the user to confirm these — they are the baseline):
- Real agents, no substitutes. If a prusik role doesn't resolve via Agent(subagent_type=...), STOP — do not fall back to `general-purpose`.
- Quality first, then efficiency.
- Never edit prusik source during a sprint.
- No force flags, no destructive ops, no git push/commit unless the user explicitly asks.

---

## Step 0 — verify real agents resolve

Before touching anything else, confirm prusik's project-local agents are in CC's registry:

    Agent(subagent_type='brief-critic', prompt='ping — reply with the single word ACK and nothing else')

If this errors with `Agent type 'brief-critic' not found`, STOP and tell the user:

    Agent registry doesn't have prusik's roles. Either:
      1. This CC session was started before `.claude/agents/` was populated — restart CC.
      2. Run `/agents` in the CLI to reload the registry.
      3. Run `prusik agents doctor` to diagnose frontmatter issues.
    Not falling back to general-purpose per your preference.

If ACK returns, proceed.

---

## Step 1 — bridge hygiene (only if the bridge is ON)

The bridge is an **opt-in** live-collaboration channel, default OFF. Check it:

    prusik bridge status

**If the first line says `bridge: OFF`** — skip this entire step and go to Step 2.
Do NOT turn the bridge on here; it's enabled deliberately by the operator
(`prusik bridge on`), not automatically by a sprint.

**If `bridge: ON`** and it's active for a DIFFERENT feature (path contains a
different slug), rotate it to this sprint's slug:

    prusik bridge off
    prusik bridge on --slug $(date +%Y-%m-%d)-$ARGUMENTS

Skip the rotate if the active slug already matches $ARGUMENTS.

---

## Step 2 — pre-sprint (discovery + cartographer + fingerprint + brief-critic)

Run `prusik gate sprint-init --feature $ARGUMENTS`. This is idempotent — re-run after each LLM step.

Possible outputs:
- **"design/map.md missing"** → invoke `cartographer`:

      Agent(subagent_type='cartographer', prompt='produce design/map.md per your role spec. Use .sprint/inventory.json and .sprint/dep-graph.json. After writing, run `prusik discovery fingerprint-map` via Bash.')

  Then re-run `prusik gate sprint-init --feature $ARGUMENTS`.

- **"briefs/$ARGUMENTS.md missing"** → STOP. The brief is the user's intent contract; do not fabricate it.

- **"reports/$ARGUMENTS/brief-critique.txt missing"** → invoke `brief-critic`:

      Agent(subagent_type='brief-critic', prompt='review briefs/$ARGUMENTS.md per your role spec')

  **Reviewer artifact fallback (v0.5.0+):** After the agent returns, check whether `reports/$ARGUMENTS/brief-critique.txt` was written. If NOT:
    - Inspect the agent's text response.
    - If its first non-empty line starts with the literal word `PASS` or `FAIL` (followed by whitespace/punctuation/EOL — not "PASS with notes" or any hedged form): use the Write tool to create the file with that first-line verdict + the agent's full body. Then via Bash:
        ```
        prusik gate mark-fallback --role brief-critic --feature $ARGUMENTS
        ```
    - If the first word isn't a strict PASS/FAIL literal: STOP and tell the user the agent's verdict is unparseable. Do not infer.

  Then re-run `prusik gate sprint-init --feature $ARGUMENTS`.

- **"map drift Nn% > 30%"** → re-invoke cartographer, then re-run sprint-init.

Loop until sprint-init exits 0 and the phase advances to `scoping`.

---

## Step 3 — scoping

Invoke `scoping`:

    Agent(subagent_type='scoping', prompt='produce design/$ARGUMENTS/scope.md per your role spec')

Validate:

    prusik gate scope design/$ARGUMENTS/scope.md

If validation fails with a markdown-wrapping error, the scoping agent should retry with plain paths (v0.3.3+ strips markdown wrappers; error would indicate a different issue).

Invoke `scope-critic`:

    Agent(subagent_type='scope-critic', prompt='review design/$ARGUMENTS/scope.md per your role spec and write reports/$ARGUMENTS/scope-approval.txt')

**Apply the same reviewer artifact fallback** as brief-critic: if `reports/$ARGUMENTS/scope-approval.txt` was not written but the agent's text response starts with literal `APPROVED` or `REJECTED`, write the file from that, then `prusik gate mark-fallback --role scope-critic --feature $ARGUMENTS`. If the first word is anything else (hedged, "MOSTLY APPROVED", etc.), STOP.

Read `reports/$ARGUMENTS/scope-approval.txt`:
- First line `APPROVED` → proceed.
- First line `REJECTED` → re-invoke `scoping` with the critique attached. Max 3 attempts. If still REJECTED after 3, STOP and report to the user (file `prusik feedback "<one-line>" --kind friction --detail "<what happened>"` first — always-on capture — plus a bridge BUG if it's ON).

Advance:

    prusik gate advance triage --feature $ARGUMENTS

---

## Step 4 — triage (pure code, zero tokens)

    prusik triage --feature $ARGUMENTS

Read `decisions/$ARGUMENTS.json`'s `mode`:
- `mode=solo` → `prusik gate advance solo_execute --feature $ARGUMENTS`
- `mode=team` → `prusik gate advance planning --feature $ARGUMENTS`
- `mode=reject` → STOP; surface reason to user.

---

## Step 5a — solo build (if mode=solo)

Read `design/$ARGUMENTS/scope.md`'s Modules touched. Implement the feature in `worktrees/solo/` — mirror the repo structure under worktrees/solo/ for files you touch. Respect the writable gate (nothing outside worktrees/solo/**).

Run the project's smoke/unit tests. If passing, advance:

    prusik gate advance reviewing --feature $ARGUMENTS

---

## Step 5b — team build (if mode=team)

Invoke `feature-planner`:

    Agent(subagent_type='feature-planner', prompt='produce design/$ARGUMENTS/plan.md per your role spec')

Invoke `plan-critic`:

    Agent(subagent_type='plan-critic', prompt='review design/$ARGUMENTS/plan.md per your role spec; write reports/$ARGUMENTS/plan-approval.txt')

Gate on APPROVED same as scope-critic. Advance:

    prusik gate advance building --feature $ARGUMENTS

**Worktree setup (v0.74.0 — JS/TS stacks only).** A fresh worktree on a JS/TS monorepo can't typecheck or test until its deps are installed AND the workspace packages are built (`dist/`, which cross-package imports resolve to; bare `tsc` doesn't build deps, turbo's `^build` does). Before builders run their checks, prep the worktree:

    prusik worktree-setup --dir worktrees/<role> --run   # pnpm install --prefer-offline (+ turbo run build for a monorepo)

This is a no-op for non-JS stacks (a Python partial-mirror sprint runs tools from the project root, so no setup). It fails closed — a non-zero from install/build stops the sequence so a half-set-up worktree isn't mistaken for ready.

Invoke each builder per plan.md's `## Proposed roles`. For each, pass the plan path and the specific modules owned:

    Agent(subagent_type='backend-builder', prompt='implement the api/* portion of design/$ARGUMENTS/plan.md in worktrees/backend-builder/')
    # etc.

Invoke `test-writer` in parallel.

When all builders produce `reports/$ARGUMENTS/build-*.txt`, advance:

    prusik gate advance reviewing --feature $ARGUMENTS

---

## Step 6 — reviewing

Invoke reviewers in parallel:

    Agent(subagent_type='regression-sentinel', prompt='review changes for $ARGUMENTS per your role spec; write reports/$ARGUMENTS/regression.txt')
    Agent(subagent_type='conventions-enforcer', prompt='review changes for $ARGUMENTS per your role spec; write reports/$ARGUMENTS/conventions.txt')

**Apply the reviewer artifact fallback to each.** For regression-sentinel: if `reports/$ARGUMENTS/regression.txt` missing but text response starts with literal `PASS` or `FAIL`, write the file from the response then `prusik gate mark-fallback --role regression-sentinel --feature $ARGUMENTS`. Same for conventions-enforcer (`conventions.txt`). Hedged verdicts → STOP and ask the user.

Both files must have `PASS` on line 1. On `FAIL`:
- Read the failure report. Classify each finding: **real defect a builder can patch** vs **false positive** (e.g., worktree-vs-integrated path layout differences that resolve at integration).
- If there are real defects and fixes are small/localized to `worktrees/*/**`, run a **fix round**:

      prusik gate fix-round start --feature $ARGUMENTS

  This expands `reviewing` writable to include `worktrees/*/**` for the duration of the round (scoped tight: still blocks `design/`, `src/`, etc.). Then dispatch the appropriate builder subagent(s) to patch their worktrees. When all patches land:

      prusik gate fix-round end --feature $ARGUMENTS

  Then re-run the reviewer(s). Max 2 fix rounds per sprint — the prusik gate enforces this cap; a third `start` call returns exit 2 and logs `fix_round_cap_hit`.
- If a fix needs writes outside `worktrees/*/**` (e.g., scope or plan changes) OR the defect count exceeds what a fix round can absorb OR you hit the 2-round cap: STOP and report the verbatim reviewer output to the user (file `prusik feedback "<one-line>" --kind bug --detail "<reviewer output + what you tried>"` first — always-on capture — plus a bridge BUG if it's ON).

When both PASS:

    prusik gate advance integrating --feature $ARGUMENTS

---

## Step 7 — integrating

Detect VCS mode:

    test -d .git && echo git || echo non-git

Invoke `integrator` with the appropriate mode pointer:

    Agent(subagent_type='integrator', prompt='integrate feature $ARGUMENTS per your role spec; the project is in <git|non-git> mode')

Then write retro via the integrator or inline:

    design/$ARGUMENTS/retro.md  (must contain ## What happened, ## What surprised, ## Updates to CLAUDE.md)

Invoke `pr-composer`:

    Agent(subagent_type='pr-composer', prompt='draft reports/$ARGUMENTS/pr.md per your role spec')

---

## Step 8 — close out

Capture approximate token count and wall-clock duration for the sprint (from the ledger / your awareness). Then:

    prusik gate sprint-complete --feature $ARGUMENTS --duration-min <N> --tokens <M>

Finally:

    prusik digest

Show the digest output to the user. Announce the sprint as complete.

---

## Failure handling (at any step)

If anything blocks and self-healing is not possible within one or two attempts:
1. **Always capture the friction so it isn't lost** (v0.97.0): `prusik feedback "<one-line>" --kind bug --severity high --detail "<verbatim output + what you tried>"`. This files a structured finding that rides the export to HQ and is tracked to a release — no live author needed, so it works whether or not the bridge is on. THEN, if the bridge is ON, also append the live entry: `prusik bridge write --role live-cc --kind BUG --body "<verbatim output + what you tried>"` and print the bridge path (the bridge is the real-time design-partner channel; `prusik feedback` is the always-on scale capture).
2. STOP and report the blocker (verbatim output + what you tried) directly to the user.
3. Do not:
   - fall back to substitutes
   - skip phases
   - use --force
   - commit or push
   - edit prusik source

The user (or prusik-author, if the bridge is on) decides how to proceed.

---

## When the user types `/sprint-run foo`

They are approving the entire flow above for feature `foo`. No clarifying questions unless something in the feature's brief itself is malformed. Execute.
