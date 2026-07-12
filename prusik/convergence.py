"""Convergence-stall control — pause + escalate when a feature thrashes.

A rewind is healthy when it leads to a fix and forward progress; it is a
PROBLEM when it repeats without converging — the canonical case is a feature
that rewound 8 times across 2 sprints with no remediation, churning
autonomously. This is the human-ON-the-loop safety valve: when rewinds for a
feature reach the configured budget, it FAILS CLOSED — the rewind is blocked,
the sprint is paused, and a `convergence_stall` escalation event is recorded,
so a human reviews instead of the agent thrashing forever. (No silent fallback:
the rewind does not proceed and the run halts until a human resumes.)

Unlike the read-only lenses (effort, showcase) this *changes behaviour*, so the
rule is deliberately simple and conservative, and the action is RECOVERABLE: the
budget resets on a fresh sprint OR after a human resume (`pause_ended`), so a
human who looks and resumes gets a clean window with no re-escalation loop.

Counting is derived from the append-only ledger (no extra state). The threshold
is configurable: `watchdog.max_rewinds_before_escalation` (default 4); set it to
0 to disable (an explicit operator opt-out, not a silent one).

Relationship to the v0.8.11 detector: that one watches *tool-level* thrash
(N identical Bash outputs) and only *warns*; this watches *phase-level* thrash
(rewinds) and *hard-stops*. They share the `convergence_stall` ledger event,
discriminated by `kind` ("bash_output_repeat" vs "phase_rewind").
"""

from __future__ import annotations

from typing import Any

_DEFAULT_MAX_REWINDS = 4


def max_rewinds(config: dict | None) -> int:
    """The rewind budget from sprint-config (watchdog block). Absent → default;
    a non-int value falls back to the default rather than disabling silently."""
    wd = (config or {}).get("watchdog") or {}
    v = wd.get("max_rewinds_before_escalation", _DEFAULT_MAX_REWINDS)
    try:
        return int(v)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_REWINDS


def _anchor_ts(records: list[dict], feature: Any) -> str:
    """Rewinds are counted only AFTER the later of: this feature's most recent
    `sprint_started`, or the most recent `pause_ended` (a human resume). That
    makes the budget reset on a fresh sprint and on resume — no loop."""
    anchor = ""
    for r in records:
        ev = r.get("event")
        if ev == "sprint_started" and r.get("feature") == feature:
            anchor = max(anchor, r.get("ts", ""))
        elif ev == "pause_ended":
            anchor = max(anchor, r.get("ts", ""))
    return anchor


def rewind_count(records: list[dict], feature: Any) -> int:
    """Rewinds for `feature` since the anchor (current sprint / last resume)."""
    anchor = _anchor_ts(records, feature)
    return sum(1 for r in records
               if r.get("event") == "phase_rewind"
               and r.get("feature") == feature
               and r.get("ts", "") > anchor)


def is_stall(records: list[dict], feature: Any, limit: int) -> bool:
    """True if a NEW rewind attempt would exceed the budget. `limit` rewinds
    are allowed; the next attempt escalates. `limit <= 0` disables the control."""
    if limit <= 0:
        return False
    return rewind_count(records, feature) >= limit


def thrash_advisory(records: list[dict], feature: Any) -> str | None:
    """Early STRUCTURAL-blocker signal (fb-983dac02ac8d). A sprint that burns
    multiple fix-rounds (plus convergence-stalls) without the reviewer ever reaching a
    PASS is usually blocked on PRUSIK-MECHANICS — worktree→root assembly, fan-out
    homelessness, capture/stall — not a product defect. Surface it BEFORE more effort is
    spent (one sprint burned ~1.9M tokens, 3 fix-rounds + 2 escalations, before this
    became clear). Pure ledger derivation (no hot-path state); ADVISORY — the operator
    decides. Complements the rewind hard-stop (`is_stall`): this is the earlier, softer
    composite that fires at the SECOND fix-round. Returns the advisory text, or None."""
    rounds = sum(1 for r in records if r.get("event") == "fix_round_start"
                 and r.get("feature") == feature)
    if rounds < 2:
        return None
    if any(r.get("event") == "phase_advance" and r.get("to_phase") == "integrating"
           and r.get("feature") == feature for r in records):
        return None                        # the sprint reached a PASS → not thrashing
    stalls = sum(1 for r in records if r.get("event") == "convergence_stall"
                 and r.get("feature") == feature)
    extra = f" + {stalls} convergence-stall(s)" if stalls else ""
    return (f"[prusik] ⚠ thrash signal: {rounds} fix-round(s){extra}, reviewer still "
            f"NOT passing. This pattern is usually a STRUCTURAL / prusik-mechanics blocker "
            f"(worktree→root assembly, fan-out homelessness, capture/stall), not a "
            f"product defect. Before spending another round, weigh escalating to the "
            f"operator — `prusik gate fix-round escalate --feature {feature} --decision "
            f"<extend-once|integrate-with-flag|abandon> --rationale \"...\"`. The fastest "
            f"fix for a prusik blocker is the operator, not another round. (fb-983dac02ac8d)")
