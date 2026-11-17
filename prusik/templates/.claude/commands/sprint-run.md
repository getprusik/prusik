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
- **Model tier, every spawn:** before each `Agent(...)` call for a prusik role, run `prusik tier resolve <feature-slug> --role <role>`. `<feature-slug>` is the first word of $ARGUMENTS only, never `$ARGUMENTS` itself: `/sprint-start foo --trivial` must give `prusik tier resolve foo --role ...`. On `model=<ref>`, pass `model=<ref>` to that Agent call. On `model=off`, pass no model. On rc ≠ 0, STOP and show the output; do not spawn untiered.
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

- **"reports/$ARGUMENTS/brief-critique.txt missing"** → run `prusik gate brief $ARGUMENTS` via Bash. It reports every mechanical violation together. On a non-zero exit, STOP and show the output: the brief needs fixing and no critic round is due. On exit 0, invoke `brief-critic` with that output in its prompt, because the role has no shell:

      Agent(subagent_type='brief-critic', prompt='review briefs/$ARGUMENTS.md per your role spec. `prusik gate brief` output: <paste it here>')

  **Reviewer artifact fallback (v0.5.0+):** After the agent returns, check whether `reports/$ARGUMENTS/brief-critique.txt` was written. If NOT:
    - Inspect the agent's text response.
    - If its first non-empty line starts with the literal word `PASS` or `FAIL` (followed by whitespace/punctuation/EOL — not "PASS with notes" or any hedged form): use the Write tool to create the file with that first-line verdict + the agent's full body. Then via Bash:
        ```
        prusik gate mark-fallback --role brief-critic --feature $ARGUMENTS
        ```
    - If the first word isn't a strict PASS/FAIL literal: STOP and tell the user the agent's verdict is unparseable. Do not infer.

  Then re-run `prusik gate sprint-init --feature $ARGUMENTS`.

- **"product-fit … soundness is UNJUDGED"** (charter present + `require_critique`; fb-8295dd5d4859) → invoke `product-fit-critic` instead of leaving it for a manual round-trip. First ensure `design/$ARGUMENTS/product-fit.md` exists (`prusik gate product-fit $ARGUMENTS --bootstrap` drafts it), then:

      Agent(subagent_type='product-fit-critic', prompt='review design/$ARGUMENTS/product-fit.md per your role spec and write reports/$ARGUMENTS/product-fit-critique.txt (PASS/FAIL)')

  **Apply the same reviewer artifact fallback** as brief-critic: if the file wasn't written but the agent's first word is a literal `PASS`/`FAIL`, write it from the response, then `prusik gate mark-fallback --role product-fit-critic --feature $ARGUMENTS`. On `FAIL`, the critic names the specific coherence gap (usually an uncited overlapping sibling) — fix `## Related` in product-fit.md, then re-run sprint-init. Sequencing it here collapses the form-pass→manual-critic→re-run round-trip.

- **"map drift Nn% > 30%"** → re-invoke cartographer, then re-run sprint-init.

Loop until sprint-init exits 0 and the phase advances to `scoping`.

---

## Step 3 — hand off to the driver

Once sprint-init passes (phase `scoping`), run the rest of the sprint in code. Run this with Bash `run_in_background: true` and wait for its completion notification (do not poll):

    prusik drive $ARGUMENTS

`prusik drive` runs scoping, triage and planning (each author and critic round in a fresh headless session, capped by `drive.design_rounds`), then, when triage chooses solo, `solo_execute` (a tier-resolved `solo-builder` builds in `worktrees/solo/`; solo fix rounds run the same way), or building when it chooses team, then reviewing and integrating, each role spawned headless with its tier model. It closes the sprint with `gate sprint-complete`, prints one line per phase and keeps gate output in `reports/$ARGUMENTS/drive.log`. Do not spawn those roles with `Agent(...)` yourself.

- **rc 0** — the sprint is complete and `gate sprint-complete` has run. Continue at Step 8, skipping its `sprint-complete` command.
- **rc 3** — drive stopped at a judgement point. Read `reports/$ARGUMENTS/drive-escalation.json` and act on that file only. Report its `kind`, `reason` and `evidence` to the user, with the remedy for the kind:
  - `scope_rejected` / `plan_rejected` — the critic did not approve within `drive.design_rounds`. Read the critic report in the evidence, fix the artifact or raise the cap, then re-run `prusik drive $ARGUMENTS`.
  - `triage_rejected` — triage rejected the sprint or its decision was unreadable. Fix the brief or `decisions/$ARGUMENTS.json`, then re-run.
  - `trivial_fix` — a reviewer FAILED a trivial-lane sprint (built by hand) and drive has opened the fix round. Address the findings in the failing report named in `evidence`, in `worktrees/solo/`, commit there, then re-run `prusik drive $ARGUMENTS`: it ends the open round and re-reviews.
  - `prove_red_failed`, `suite_failed`, `fix_round_cap`, `advance_blocked` — a gate or proof failed; fix the cause named in the evidence, then re-run.
  - `permission_denied`, `spawn_failed`, `tier_unresolved`, `auth_mismatch`, `writer_busy` — a spawn or setup problem; show the evidence and stop.
  - `tree_written` — a role changed shared-tree files its phase may not write, by a means the gate could not see. The files are left as the role left them. Show the paths in the evidence and stop: the user decides whether to keep or restore them.
  - `budget_exceeded` — measured cost (USD, API list price) crossed a cap; the reason names the key (`drive.budget_usd`, `phases[<phase>].budget_usd` or `drive.session_budget_usd`) and the evidence carries the cost by role and the raw tokens by category. For the session cap, drive has released this session's writer lease: re-run `prusik drive $ARGUMENTS` from a fresh session. For the others, show the numbers and stop: only the user raises a cap.
  - `budget_unmeasurable` — drive could not read the cost a cap needs (registry or transcript unreadable, or a model with no price in prusik/prices.json). Show the reason and stop; drive never runs past a budget it cannot read.
- **rc 2** — the sprint is not drivable yet (including the trivial lane: write `design/$ARGUMENTS/trivial.md`, run `prusik gate prove-red --feature $ARGUMENTS`, create the solo worktree with `prusik gate worktree-add --feature $ARGUMENTS --role solo`, implement in `worktrees/solo/`, re-run from reviewing). Show the message.

---

## Manual / not driven

The steps below are the by-hand path for a sprint that is not driven. `prusik drive` does all of them itself; do not run them after the hand-off.

### Manual step 3 — scoping

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

### Manual step 4 — triage (pure code, zero tokens)

    prusik triage --feature $ARGUMENTS

Read `decisions/$ARGUMENTS.json`'s `mode`:
- `mode=solo` → `prusik gate advance solo_execute --feature $ARGUMENTS`
- `mode=team` → `prusik gate advance planning --feature $ARGUMENTS`
- `mode=reject` → STOP; surface reason to user.

---

### Manual step 5a — solo build (if mode=solo)

Read `design/$ARGUMENTS/scope.md`'s Modules touched. Before writing any code, capture the RED baselines (skip if drive's `solo_execute` step already did; leaving `solo_execute` is refused without them):

    prusik gate prove-red --feature $ARGUMENTS

Create the solo worktree unless drive's `solo_execute` step already did (`worktrees/solo/` exists). It records the fork point the reviewing checks measure the change set from; a worktree made any other way is refused at the boundary check:

    prusik gate worktree-add --feature $ARGUMENTS --role solo

Implement the feature in `worktrees/solo/` and commit there (in a project without git it is a plain directory: mirror the repo structure for files you touch). Respect the writable gate (nothing outside worktrees/solo/**).

Run the project's smoke/unit tests. If passing, advance:

    prusik gate advance reviewing --feature $ARGUMENTS

---

### Manual step 5b — team build (if mode=team)

Invoke `feature-planner`:

    Agent(subagent_type='feature-planner', prompt='produce design/$ARGUMENTS/plan.md per your role spec')

Then run `prusik gate plan $ARGUMENTS` via Bash, because the role has no shell. On a non-zero exit, send the errors back to `feature-planner` and re-run the gate before going on.

Review the plan:

    Agent(subagent_type='plan-critic', prompt='review design/$ARGUMENTS/plan.md per your role spec; write reports/$ARGUMENTS/plan-approval.txt')

---

## Step 8 — close out

If the sprint was driven, `prusik drive` has already run `gate sprint-complete`; skip to the digest. A sprint that was not driven needs the wall-clock duration from the ledger (token usage is measured by prusik from the session transcripts, so don't estimate it):

    prusik gate sprint-complete --feature $ARGUMENTS --duration-min <N>

Invoke `pr-composer` (with the tier rule above):

    Agent(subagent_type='pr-composer', prompt='draft reports/$ARGUMENTS/pr.md per your role spec')

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
