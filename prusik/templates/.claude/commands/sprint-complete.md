---
description: Close the sprint — record predicted vs actual to the ledger, clear active state.
argument-hint: <feature-slug> [--duration-min M] [--escalated]
allowed-tools: [Bash]
---

Close the sprint for feature `$ARGUMENTS`.

Run via Bash:
```
prusik gate sprint-complete --feature $ARGUMENTS [ --duration-min M ] [ --escalated ]
```

- If the user supplies the actual duration, pass it. Do not estimate tokens: prusik measures them from the session transcripts and records them, or records `tokens_unmeasured` with the reason.
- If the sprint escalated solo→team mid-flight, pass `--escalated`.
- If unsure, run without flags — the engine will recover predicted values from the decision file and estimate duration from ledger timestamps.

After running, prusik:
- Appends a `sprint_complete` event to `.sprint/ledger.jsonl` with predicted + actual (including the measured `tokens` and its `usage` breakdown by phase, role, model and session)
- Clears `.sprint/state.json` (no active sprint)
- `prusik digest` next time will pick up the outcome and update prediction-error stats

A sprint closes only from its last phase, with that phase's exit artifacts in place. Advance to `integrating`, write retro.md with its required sections, then run this command. Otherwise it returns rc 2, names what is missing, and the sprint stays open.
