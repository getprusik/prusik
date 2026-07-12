"""`prusik sprint <feature>` — pre-flight orchestrator (v0.17.0, Item 8).

A single CLI entrypoint that runs the *pre-agent* checks the operator
otherwise types as a sequence:

  prusik brief-lint briefs/<feature>.md
  prusik doctor --insights-for-brief briefs/<feature>.md     (v0.17.0)
  prusik gate brief briefs/<feature>.md
  prusik gate sprint-start [--trivial] <feature>

then tells the operator the next step (run `/sprint-run <feature>` in
their Claude Code session, or however their harness drives the agents).

Complements rather than replaces:
  - The `/sprint-run` slash-command in CC drives the AGENT phases —
    that's harness-internal (CC only).
  - `prusik sprint` is the CLI pre-flight — works from any terminal,
    independent of harness. Aligned with the v0.22 cross-harness vision.

Decisions on lane: brief Type → trivial-lane-eligible (bug_fix / doc /
config / test / chore) suggests --trivial; the other Types suggest full
lane. Operator confirms or overrides. Per prusik's mission boundary,
the operator owns the lane decision; this tool surfaces a recommendation
based on the brief.

Read-only on the project state until the operator confirms — then runs
`prusik gate sprint-start` which writes .sprint/state.json + ledger event.
"""

from __future__ import annotations

import sys


# Trivial-lane-eligible Types (sourced from prusik.gate._TRIVIAL_ELIGIBLE_TYPES
# but kept local so this module doesn't depend on gate's internals).
_TRIVIAL_ELIGIBLE = frozenset({"bug_fix", "doc", "config", "test", "chore"})


def run(feature: str, force_lane: str | None = None,
        yes: bool = False) -> int:
    """Pre-flight a sprint by feature slug.

    feature: the brief slug (briefs/<feature>.md must exist)
    force_lane: 'trivial' or 'full' to override the recommendation
    yes: skip confirmation prompts (CI-friendly)
    """
    from prusik import ledger
    root = ledger.project_root()
    brief_path = root / "briefs" / f"{feature}.md"

    print(f"[prusik-sprint] Pre-flight for: {feature}")
    print(f"             brief: {brief_path}")
    print()

    if not brief_path.exists():
        print(f"[prusik-sprint] ✗ brief not found at {brief_path}", file=sys.stderr)
        print("             Author it first. Templates at "
              "`<prusik>/templates/briefs/.templates/<type>.md`.",
              file=sys.stderr)
        return 2

    # Step 1 — brief-lint (structural + near-miss).
    print("Step 1/4 — prusik brief-lint")
    from prusik.brief_lint import lint as _lint
    rc = _lint(str(brief_path), cutoff=0.80)
    if rc != 0:
        print("[prusik-sprint] ✗ brief-lint failed — fix issues above first.",
              file=sys.stderr)
        return rc
    print()

    # Step 2 — forward risk signal.
    print("Step 2/4 — prusik doctor --insights-for-brief")
    from prusik import doctor
    doctor.run(insights_for_brief=str(brief_path))
    print()

    # Step 3 — brief schema validation.
    print("Step 3/4 — prusik gate brief (schema validation)")
    from prusik import gate
    import argparse as _ap
    rc = gate.brief(_ap.Namespace(path=str(brief_path)))
    if rc != 0:
        print("[prusik-sprint] ✗ brief schema invalid — fix above.",
              file=sys.stderr)
        return rc
    print()

    # Step 4 — lane decision + sprint-start.
    from prusik import schema as _schema
    sections = _schema.parse_sections(brief_path.read_text())
    btype = sections.get("## Type", "").strip()
    trivial_eligible = btype in _TRIVIAL_ELIGIBLE
    if force_lane == "trivial":
        use_trivial = True
        if not trivial_eligible:
            print(f"[prusik-sprint] ✗ --lane trivial requested but Type={btype!r} "
                  f"is not trivial-eligible (only {sorted(_TRIVIAL_ELIGIBLE)}).",
                  file=sys.stderr)
            return 2
    elif force_lane == "full":
        use_trivial = False
    else:
        use_trivial = trivial_eligible
        print("Step 4/4 — Lane decision")
        print(f"  Type:               {btype}")
        print(f"  trivial-eligible:   {trivial_eligible}")
        print(f"  recommendation:     {'TRIVIAL (collapsed scoping; reviewing floor preserved)' if use_trivial else 'FULL (scope-critic + plan-critic review)'}")
        if not yes:
            resp = input("Proceed with this lane? [Y/n] ").strip().lower()
            if resp == "n":
                resp2 = input("Override to other lane? [y/N] ").strip().lower()
                if resp2 == "y":
                    use_trivial = not use_trivial
                else:
                    print("[prusik-sprint] Cancelled.")
                    return 0

    # Step 5 — actually start the sprint.
    print()
    print(f"Step 5/4 — prusik gate sprint-start {'--trivial ' if use_trivial else ''}{feature}")
    rc = gate.sprint_start(_ap.Namespace(
        feature=feature, trivial=use_trivial))
    if rc != 0:
        print(f"[prusik-sprint] ✗ sprint-start failed (rc={rc}).",
              file=sys.stderr)
        return rc

    print()
    print(f"[prusik-sprint] ✓ Pre-flight complete. Sprint started in "
          f"{'trivial' if use_trivial else 'full'} lane.")
    print()
    print("Next step:")
    print(f"  In your Claude Code session, run:  /sprint-run {feature}")
    print("  (or drive the phases manually via `prusik gate advance <phase>`)")
    return 0
