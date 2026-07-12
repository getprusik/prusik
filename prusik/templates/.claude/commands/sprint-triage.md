---
description: Run pure-code triage to decide solo vs team mode for a feature. Requires scope.md to exist.
argument-hint: <feature-slug>
allowed-tools: [Bash]
---

Run `prusik triage --feature $ARGUMENTS` via Bash. This is pure-code routing — zero tokens. Reads `design/$ARGUMENTS/scope.md` and `briefs/$ARGUMENTS.md`, applies the heuristics in `.claude/sprint-config.yaml`, writes `decisions/$ARGUMENTS.json`.

Show the output to the user. It will print the chosen mode (solo or team) with the rule that triggered it.

After triage, remind the user:
- `mode=solo` → next is `/sprint-advance solo_execute $ARGUMENTS`
- `mode=team` → next is `/sprint-advance planning $ARGUMENTS`
