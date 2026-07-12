---
description: Advance to the next phase after validating current phase's exit artifacts.
argument-hint: <target-phase> <feature-slug>
allowed-tools: [Bash]
---

Advance the sprint. Args (in `$ARGUMENTS`): `<target-phase> <feature-slug>` — whitespace-separated.

**Parsing `$ARGUMENTS`:** split on whitespace into exactly two tokens.
- First token = target phase (one of: `scoping`, `triage`, `planning`, `solo_execute`, `building`, `reviewing`, `integrating`).
- Second token = feature slug.

If `$ARGUMENTS` doesn't have exactly two tokens, or the phase isn't recognized, STOP and tell the user:

    /sprint-advance takes two args: <target-phase> <feature-slug>.
    Got: `$ARGUMENTS`.
    Expected: `/sprint-advance triage domain-schema`

Otherwise run via Bash:

    prusik gate advance <PHASE> --feature <FEATURE>

substituting the parsed tokens for `<PHASE>` and `<FEATURE>` (prusik takes phase as a positional and feature as a `--feature` flag — do NOT pass `$ARGUMENTS` directly to `prusik gate advance` as that would mismatch the CLI shape).

Prusik will:
- Validate that the current phase's exit artifacts exist and pass their schema / required-sections / must-contain checks.
- If anything is missing, refuse to advance and print what's needed. Show that output to the user.
- If satisfied, transition phase and append to the ledger.

After advancing, run `prusik status` to show the new phase's writable patterns, budget, and required exit artifacts — so the user (or the next role) knows what to produce.

**Common transitions:**
- `scoping → triage` after `design/{feature}/scope.md` is valid
- `triage → planning` after `decisions/{feature}.json` says mode=team
- `triage → solo_execute` after `decisions/{feature}.json` says mode=solo
- `planning → building` after plan.md + plan-approval.txt (APPROVED)
- `building → reviewing` after builders complete
- `reviewing → integrating` after regression.txt + conventions.txt both say PASS
- `integrating → (end)` after retro.md
