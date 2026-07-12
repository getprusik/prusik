---
description: Pull issues from the configured tracker into .sprint/issues.db.jsonl. Graceful no-op if not configured.
allowed-tools: [Bash]
---

Run `prusik issues sync` via Bash. Show the output.

This reads `.claude/sprint-config.yaml` under `issues:` and dispatches to the right plugin (currently: github via `gh` CLI, linear is a stub).

If the tracker is `none` or unsupported, or `gh` is missing, the command prints a reason and exits cleanly — it does not fail.

After sync, `.sprint/issues.db.jsonl` is available for the scoping role to correlate against briefs via `prusik issues search "<query>"`.
