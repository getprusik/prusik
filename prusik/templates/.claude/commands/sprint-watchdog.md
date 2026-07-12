---
description: Check heartbeats, phase staleness, and budget. Files incidents for anything stuck.
allowed-tools: [Bash]
---

Run `prusik watchdog` via Bash. One-shot check. Show the output to the user.

To run continuously (polling every N minutes) in a terminal:
```
prusik watchdog --poll 15
```

Or schedule it out-of-band via Claude Code `/schedule` or OS cron so the loop stays closed without a human in the chair.

Incidents land under `.sprint/incidents/` with a timestamp + kind (stale_heartbeat, phase_stalled, budget_exceeded). Each incident also appears as a `watchdog_incident` event in the ledger.
