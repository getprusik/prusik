"""`prusik metrics` — defect-prevention scorecard from the ledger.

Team-lead-facing: "what has prusik actually surfaced or blocked?" Every number
here is a count of REAL ledger events — factual, not a modeled counterfactual.
We don't claim "N bugs prevented" (unprovable); we report what was flagged,
caught, and blocked, and let the reader judge.

    prusik metrics                  # all-time scorecard
    prusik metrics --since 2026-05-01T00:00:00+00:00
    prusik metrics --json           # for dashboards / trend tracking

Backed by these ledger events:
  reviewer_binding_flagged   — caller↔callee contract drift caught
  reviewer_test_set_reach    — untested contract surface flagged
  reviewer_skip_flagged      — suspect (false-clean) test skip flagged
  reviewer_execution_verified— a test command's REAL execution recorded;
                               ok=False = claimed-clean-but-didn't (evidence gate)
  fix_round_start            — review found defects needing rework (pre-merge)
  gate_blocked               — out-of-phase write/bash blocked
  advance_blocked / sprint_*_blocked — premature/invalid transition blocked
  verify_loop_checked        — T0→T1 closed-loop resolution
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict

from prusik import catch_quality, ledger

# Events that count as "a defect or risk surfaced before merge".
_CAUGHT_EVENTS = {
    "reviewer_binding_flagged": "binding_mismatches",
    "reviewer_test_set_reach": "test_reach_gaps",
    "reviewer_skip_flagged": "suspect_skips",
    "fix_round_start": "review_fix_rounds",
}


def compute(records: list[dict]) -> dict:
    """Pure: ledger records → metrics dict. No I/O (unit-testable)."""
    counts = Counter(r.get("event") for r in records)

    exec_events = [r for r in records if r.get("event") == "reviewer_execution_verified"]
    non_runs = sum(1 for r in exec_events if not r.get("ok", True))

    vl = [r for r in records if r.get("event") == "verify_loop_checked"]
    t0 = sum(int(r.get("t0_count", 0)) for r in vl)
    resolved = sum(int(r.get("resolved", 0)) for r in vl)

    caught = {label: counts[ev] for ev, label in _CAUGHT_EVENTS.items()}
    caught["non_runs_or_failures_caught"] = non_runs
    # v1.1 — custom/pluggable detectors flagged via the reviewer path
    caught["custom_detector_flags"] = counts["detector_flagged"]
    headline = sum(caught.values())

    # which custom detectors fired, and how often
    by_detector: dict[str, int] = defaultdict(int)
    for r in records:
        if r.get("event") == "detector_flagged":
            by_detector[r.get("detector") or "?"] += 1

    # per-feature rollup of the catch events (cheap trend signal)
    def _is_catch(r: dict) -> bool:
        ev = r.get("event")
        return (ev in _CAUGHT_EVENTS or ev == "detector_flagged"
                or (ev == "reviewer_execution_verified" and not r.get("ok", True)))
    by_feature: dict[str, int] = defaultdict(int)
    for r in records:
        if _is_catch(r):
            by_feature[r.get("feature") or "?"] += 1

    return {
        "events_total": len(records),
        "headline_caught_before_merge": headline,
        "caught_before_merge": caught,
        "process_discipline": {
            "out_of_phase_writes_blocked": counts["gate_blocked"],
            "premature_transitions_blocked": (counts["advance_blocked"]
                                              + counts["sprint_init_blocked"]
                                              + counts["sprint_start_blocked"]),
            "escalations": (counts["fix_round_escalation"]
                            + counts["integrated_under_escalation"]),
            "phase_rewinds": counts["phase_rewind"],
        },
        "execution_evidence": {
            "executions_verified": len(exec_events),
            "non_runs_or_failures_caught": non_runs,
        },
        "verify_loop": {
            "t0_findings": t0,
            "resolved": resolved,
            "closure_rate_pct": round(100 * resolved / t0) if t0 else None,
        },
        "throughput": {
            "sprints_started": counts["sprint_started"],
            "sprints_completed": counts["sprint_complete"],
        },
        "by_detector": dict(sorted(by_detector.items(), key=lambda kv: -kv[1])),
        "by_feature": dict(sorted(by_feature.items(),
                                  key=lambda kv: -kv[1])),
        # v0.45.0 — per-gate true-catch vs false-block (friction-value ratio).
        "catch_quality": catch_quality.summarize(
            catch_quality.resolve_catches(
                catch_quality.extract_catches(records), records)),
    }


def _print_human(m: dict, since: str | None) -> None:
    window = f"since {since}" if since else "all-time"
    print(f"prusik metrics — defect-prevention signal ({window})")
    print(f"{m['events_total']} ledger events.\n")

    c = m["caught_before_merge"]
    print(f"Caught before merge ({m['headline_caught_before_merge']} total):")
    print(f"  binding-mismatches flagged    {c['binding_mismatches']:5d}")
    print(f"  test-reach gaps flagged       {c['test_reach_gaps']:5d}")
    print(f"  suspect skips flagged         {c['suspect_skips']:5d}")
    print(f"  non-runs / failures caught    {c['non_runs_or_failures_caught']:5d}"
          f"   (execution-evidence gate)")
    print(f"  review fix-rounds             {c['review_fix_rounds']:5d}"
          f"   (defects found at review)")
    if c.get("custom_detector_flags"):
        print(f"  custom-detector flags         {c['custom_detector_flags']:5d}"
              f"   ({', '.join(f'{k}:{v}' for k, v in m['by_detector'].items())})")

    p = m["process_discipline"]
    print("\nProcess discipline:")
    print(f"  out-of-phase writes blocked   {p['out_of_phase_writes_blocked']:5d}")
    print(f"  premature transitions blocked {p['premature_transitions_blocked']:5d}")
    print(f"  escalations (solo→team/fix)   {p['escalations']:5d}")
    print(f"  phase rewinds                 {p['phase_rewinds']:5d}")

    e = m["execution_evidence"]
    print("\nExecution evidence:")
    print(f"  executions proven             {e['executions_verified']:5d}")
    print(f"  non-runs / failures caught    {e['non_runs_or_failures_caught']:5d}")

    vl = m["verify_loop"]
    if vl["t0_findings"]:
        rate = vl["closure_rate_pct"]
        print("\nClosed-loop verify:")
        print(f"  T0 findings {vl['t0_findings']} → resolved {vl['resolved']}"
              f" ({rate}%)")

    cq = m.get("catch_quality") or {}
    resolved_gates = {g: s for g, s in cq.items() if s.get("precision") is not None}
    if resolved_gates:
        print("\nCatch quality (true-catch / resolved — the friction-value ratio):")
        for g in sorted(resolved_gates, key=lambda g: -cq[g]["fired"]):
            s = cq[g]
            print(f"  {g:20s} {100 * s['precision']:3.0f}%  "
                  f"({s['true_catch']} true / {s['false_block']} false; "
                  f"{s['unresolved']} unlabeled)")
        print("  (label unresolved fires with `prusik catch <id> --true|--false`)")

    t = m["throughput"]
    print(f"\nThroughput: {t['sprints_started']} sprint(s) started, "
          f"{t['sprints_completed']} completed.")

    if m["events_total"] == 0:
        print("\n(No ledger yet — run some sprints, then check back.)")
    else:
        print("\n(All counts are recorded ledger events — factual, not modeled.)")


def run(since: str | None = None, json_output: bool = False) -> int:
    records = ledger.read_all()
    if since:
        records = [r for r in records if str(r.get("ts", "")) >= since]
    m = compute(records)
    if json_output:
        out = {"since": since, **m}
        print(json.dumps(out, indent=2))
    else:
        _print_human(m, since)
    return 0
