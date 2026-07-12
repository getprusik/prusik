---
description: Suspend Stop-hook exit-artifact enforcement for deliberate mid-phase pauses (e.g. yielding to user for a checkpoint). Resume with /sprint-resume.
allowed-tools: [Bash]
argument-hint: optional reason (recorded in `prusik status` and ledger)
---

Run `prusik pause $ARGUMENTS` via Bash and show the output. The `$ARGUMENTS` slot may be empty (no reason supplied) or contain free-form prose (the pause reason). Prusik CLI accepts variadic positional words and records them as the pause reason; pass them through verbatim — do NOT quote, escape, or interpret them.

Use this when you're intentionally yielding mid-phase (asking the user a question, waiting on input, taking a break) and don't want the Stop hook to demand exit artifacts that aren't due yet. The pause is sticky — it persists across turns until `/sprint-resume` (or `prusik resume`) clears it.

The reason (if supplied) shows up in `prusik status` and the `pause_started` ledger event for digest analysis. Useful for retro questions like "how often do sprints pause for checkpoints vs blockers?"

Pre-tool gates (writable patterns, deny commands) and ledger logging continue to function normally during pause; only the end-of-turn exit-artifact check is suspended.
