---
description: Start the local web form for authoring briefs (Tier-3 GUI for non-engineer authors).
allowed-tools: [Bash]
---

Run `prusik serve` via Bash — starts a local web server at http://127.0.0.1:8765 that renders a form from the brief schema.

The server writes `briefs/<slug>.md` when submitted and runs `prusik gate brief` to validate. Errors render inline; valid submissions tell the author what to run next (`/sprint-start <slug>`).

For a non-default port: `prusik serve --port 9000`.

Ctrl-C in the terminal running the command to stop the server.
