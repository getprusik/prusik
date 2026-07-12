---
description: Run brief-critic, validate the brief, check map freshness, and start the sprint in the scoping phase.
argument-hint: <feature-slug> [--trivial]
allowed-tools: [Bash, Read]
---

> **Prefer `/sprint-run <feature>`** (v0.3.6+) — that command drives the
> full sprint end-to-end. Use this `/sprint-start` command only if you
> want to advance phase-by-phase manually.

Start a sprint for feature `$ARGUMENTS`.

**Steps:**

1. Verify `briefs/$ARGUMENTS.md` exists. If not, tell the user to run `/brief-new $ARGUMENTS` first.

2. **Pre-sprint gate: brief-critique.** If `.claude/sprint-config.yaml` has `pre_sprint_gates.brief_critique.enabled: true` (the default), and `reports/$ARGUMENTS/brief-critique.txt` does not yet exist, invoke the `brief-critic` agent (via the Agent tool with `subagent_type=brief-critic`). Its job: read `briefs/$ARGUMENTS.md` and produce `reports/$ARGUMENTS/brief-critique.txt` whose first line is `PASS` or `FAIL`.

   **Reviewer artifact fallback (v0.5.0+):** after invoking brief-critic, check that `reports/$ARGUMENTS/brief-critique.txt` exists. If the agent returned a verdict in its text response but did NOT Write the file (recurring CC behavior — see the v0.4.5 `prusik refresh` restart note for why this happens):

     a. Inspect the agent's final text response. Look at its **first word on the first non-empty line**.
     b. If that first word is EXACTLY `PASS` or `PASS,` or `PASS:` or `FAIL` or `FAIL,` or `FAIL:` (a LITERAL verdict, not hedged "PASS with notes"): use the Write tool to create `reports/$ARGUMENTS/brief-critique.txt` with the verdict as line 1 followed by the agent's full body. Then via Bash run:
        ```
        prusik gate mark-fallback --role brief-critic --feature $ARGUMENTS
        ```
     c. If the first word is anything else (hedged, ambiguous, "mostly PASS", paragraph prose, etc.): STOP. Tell the user the agent's response is unparseable and ask them to decide the verdict manually. Do NOT infer silently.

   If FAIL (either from the file or via fallback): show the user the critique and stop — they need to fix the brief.

3. **Pre-sprint gate: map freshness.** Age (>7 days) is only a weak floor — the real signal is whether THIS feature's subsystem drifted since the map was generated. If `.sprint/dep-graph.json` or `.sprint/map-fingerprint.json` is missing or stale, if `design/map.md` doesn't exist, OR if a dependency in the feature's own subsystem merged after the map was fingerprinted (the engine's `map_freshness` gate detects this feature-scoped drift even when the map is recent — fb-76ff51b273de), run discovery + cartographer first:
   - `prusik discovery all` via Bash — refresh inventory and dep graph (zero tokens)
   - Invoke the `cartographer` agent (Agent tool, `subagent_type=cartographer`) to produce or refresh `design/map.md`
   - `prusik discovery fingerprint-map` via Bash — snapshot the current dep-graph as the baseline for future freshness checks

4. Run `prusik gate sprint-start $ARGUMENTS` via Bash. The engine validates the brief, checks the `brief_critique` gate, and checks the `map_freshness` gate — it recomputes the dep-graph fresh, then fails on either global drift exceeding `max_drift_pct` OR a drifted module inside the feature's own subsystem (feature-scoped, independent of age/global %). If any gate is unmet, the command exits 2 with a specific reason; show the user the errors and stop.

5. On success, tell the user:
   - Sprint is in the `scoping` phase.
   - Next: invoke the `scoping` role (Agent tool, `subagent_type=scoping`) to produce `design/$ARGUMENTS/scope.md`.
   - Then: invoke the `scope-critic` role to produce `reports/$ARGUMENTS/scope-approval.txt` (APPROVED or REJECTED).
   - Then: `/sprint-advance triage $ARGUMENTS` once both scope.md and scope-approval.txt are in place.

Do not invoke scoping or scope-critic yourself in this command. Just set up the phase and tell the user what's next.

**Trivial lane (v0.11.0 #2 — proportional ceremony).** If the user passes
`--trivial` (or the change is self-evidently a one-shot bug_fix/doc/config/
test/chore), run `prusik gate sprint-start $ARGUMENTS --trivial`. The engine
**rejects** the flag if the brief Type is new_feature/refactor/migration
(ungameable — those have real blast radius). On acceptance the sprint enters
the trivial lane:

   - Step 2 (brief-critic) and the full **reviewing** correctness floor
     (regression + conventions PASS) still apply — these are NOT skipped.
   - Skipped: scope-critic, triage, planning, plan-critic. Instead of
     `scope.md`, write a lightweight `design/$ARGUMENTS/trivial.md` with
     `## Change` and `## How verified`.
   - Then `/sprint-advance solo_execute $ARGUMENTS` directly (the engine's
     lane-aware exit gate accepts `trivial.md` in place of
     scope.md+scope-approval), implement in `worktrees/solo/`, then
     `/sprint-advance reviewing` → `/sprint-advance integrating` as normal.

   This is ceremony proportional to blast radius — prusik's stated value —
   not a loophole: the correctness floor is intact, only design-review
   ceremony a one-line change cannot benefit from is removed.
