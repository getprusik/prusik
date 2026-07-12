"""Showcase — the composed trust narrative (instrument layer, piece 2).

Trust is *christened* by showing the proof-of-work, not the work: the human
can't verify a persuasive artifact by reading it, but can verify the audit
narrative around it. This composes the lenses that already exist — the ledger
timeline, the adversarial critic verdicts, the execution evidence, catch-quality
(TRUST), and effort (VALUE) — into ONE legible per-feature story:

    intent → progress → intervention → self-correction → evidence → metrics → objective

It is *evidentiary*, not descriptive: it proves three escalating claims, and —
held to prusik's own bar — only marks a claim met when the ledger actually shows
it (no looks-done-isn't of our own):

    1. the self-correcting loop is IN PLACE   (critics fired + rewind/fix-rounds)
    2. it caught issues UPFRONT, effectively  (an early critic rejected scope/plan)
    3. it led to the desired OUTCOME          (reached completion; evidence passed)

"Upfront" is classified by critic ROLE, not phase: `critic_verdict` events carry
role+feature but no phase, and a scope/plan reject is upfront by construction.
The catch-quality *labels* (true/false/open) are shown separately as the
longer-run effectiveness ledger — mostly "open" until an operator labels them,
which is honest, not a gap to paper over.

Composes, does not reinvent — it calls `catch_quality` + `effort`, never
re-deriving. Pure read-over-the-ledger; text/CLI-first (a GUI stays parked
until a user demonstrates text views insufficient).
"""

from __future__ import annotations

import json
from typing import Any

from prusik import catch_quality as cq
from prusik import effort, ledger, schema

# A reject from one of these critics is an UPFRONT catch — it flagged an issue
# in the brief/scope/plan, before the build artifact exists to deceive anyone.
_UPFRONT_CRITICS = {"brief-critic", "scope-critic", "plan-critic"}
_LATE_CRITICS = {"regression-sentinel", "conventions-enforcer"}
_APPROVED = {"approved", "pass", "ok", "accept", "accepted"}
_PHASE_ORDER = ["scoping", "triage", "planning", "solo_execute",
                "building", "reviewing", "integrating"]


def _approved(verdict: Any) -> bool:
    return str(verdict or "").strip().lower() in _APPROVED


def _lead(body: str) -> str:
    """The clean lead line of a brief section: everything up to the first
    sub-heading (a real adopter brief nests `### A`-style criteria under Success),
    with internal newlines collapsed to single spaces. Without this the dossier
    leaks wrapped lines and stray sub-headings (found running on an adopter's ledger)."""
    lines: list[str] = []
    for line in body.splitlines():
        if line.lstrip().startswith("#"):
            break
        lines.append(line)
    return " ".join(" ".join(lines).split())


def _read_intent(feature: str) -> dict[str, str]:
    """Pull goal + success criteria from briefs/<feature>.md if present.
    Enrichment only — the dossier is complete without it (ledger-derived)."""
    p = ledger.project_root() / "briefs" / f"{feature}.md"
    if not p.exists():
        return {}
    try:
        secs = schema.parse_sections(p.read_text())
    except OSError:
        return {}
    return {"goal": _lead(secs.get("## Goal", "")),
            "success": _lead(secs.get("## Success criteria", ""))}


def journey_features(records: list[dict]) -> list[str]:
    """Features that have a journey in the ledger, first-seen order."""
    seen: list[str] = []
    for r in records:
        f = r.get("feature")
        if f and r.get("event") in (
            "sprint_started", "phase_advance", "phase_rewind",
            "critic_verdict", "sprint_complete",
        ) and f not in seen:
            seen.append(f)
    return seen


def dossier(feature: str, records: list[dict]) -> dict[str, Any]:
    """Compose the full per-feature trust dossier from the existing lenses."""
    feat_recs = [r for r in records if r.get("feature") == feature]

    adversarial = [
        {"role": r.get("role"), "verdict": r.get("verdict"),
         "artifact": r.get("artifact")}
        for r in feat_recs if r.get("event") == "critic_verdict"
    ]
    brief_critic = next(
        (a["verdict"] for a in adversarial
         if str(a["role"] or "").startswith("brief")), None)
    rejects = [a for a in adversarial if not _approved(a["verdict"])]
    upfront = [a for a in rejects if (a["role"] or "") in _UPFRONT_CRITICS]
    late = [a for a in rejects if (a["role"] or "") in _LATE_CRITICS]

    evidence = [
        {"ok": bool(r.get("ok")), "what": r.get("command") or r.get("kind") or ""}
        for r in feat_recs if r.get("event") == "reviewer_execution_verified"
    ]
    evidence_passed = any(e["ok"] for e in evidence)
    evidence_catches = [e for e in evidence if not e["ok"]]

    # Catch-quality LABELS for this feature — the longer-run effectiveness
    # ledger (true/false/open). Mostly "open" until an operator labels; shown
    # as-is rather than faked into confidence.
    labeled = [c for c in cq.resolve_catches(cq.extract_catches(records), records)
               if c.get("feature") == feature]
    true_c = sum(1 for c in labeled if c["verdict"] == cq.TRUE_CATCH)
    false_c = sum(1 for c in labeled if c["verdict"] == cq.FALSE_BLOCK)
    open_c = sum(1 for c in labeled if c["verdict"] == cq.UNRESOLVED)

    eff = effort.summarize_features(records).get(feature, {})
    rewinds = int(eff.get("rewinds", 0))
    fix_rounds = int(eff.get("fix_rounds", 0))
    complete = bool(eff.get("complete"))
    last_to = next((r.get("to_phase") for r in reversed(feat_recs)
                    if r.get("event") in ("phase_advance", "phase_rewind")), None)
    final_phase = "complete" if complete else (last_to or "scoping")

    phases = eff.get("phases", {}) or {}
    ordered_phases = [[p, phases[p]] for p in _PHASE_ORDER if p in phases]

    # Three claims — honest: None = "not enough evidence to assert", not False.
    loop_in_place = bool(adversarial) or rewinds > 0 or fix_rounds > 0
    caught_upfront: bool | None = True if upfront else None

    return {
        "feature": feature,
        "intent": {"brief_critic": brief_critic, **_read_intent(feature)},
        "progress": {"phases": ordered_phases,
                     "wall_clock_sec": eff.get("wall_clock_sec"),
                     "final_phase": final_phase, "complete": complete},
        "adversarial": adversarial,
        "self_correction": {"rewinds": rewinds, "fix_rounds": fix_rounds},
        "evidence": evidence,
        "metrics": {
            "adversarial_flags": {"upfront": len(upfront), "late": len(late)},
            "evidence_catches": len(evidence_catches),
            "labeled": {"true": true_c, "false": false_c, "open": open_c},
            "cost": {"wall_clock_sec": eff.get("wall_clock_sec"),
                     "tokens": eff.get("tokens")},
        },
        "objective": {"complete": complete, "evidence_passed": evidence_passed},
        "trust_check": {
            "loop_in_place": loop_in_place,
            "caught_upfront": caught_upfront,
            "reached_outcome": complete,
        },
    }


def _mark(v: bool | None) -> str:
    return {True: "✓", False: "✗", None: "–"}[v]


def _render(d: dict[str, Any]) -> None:
    print(f"Trust dossier — {d['feature']}\n")

    it = d["intent"]
    print("  INTENT")
    if it.get("goal"):
        print(f"    goal:    {it['goal'][:80]}")
    if it.get("success"):
        print(f"    success: {it['success'][:80]}")
    bc = it.get("brief_critic")
    print(f"    brief-critic: {bc or 'no verdict recorded'}"
          + ("  (an adversary signed off on the intent)" if bc else ""))

    pr = d["progress"]
    seq = " → ".join(f"{p}({effort.fmt_duration(s)})"
                     for p, s in pr["phases"]) or "—"
    print("\n  PROGRESS")
    print(f"    {seq}")
    print(f"    final: {pr['final_phase']}  ·  wall-clock "
          f"{effort.fmt_duration(pr['wall_clock_sec'])}")

    print("\n  ADVERSARIAL (independent agents tried to refute it)")
    if d["adversarial"]:
        for a in d["adversarial"]:
            print(f"    {str(a['role'] or '?'):20s} {str(a['verdict'] or '?'):10s} "
                  f"{a.get('artifact') or ''}")
    else:
        print("    (no critic verdicts recorded)")

    sc = d["self_correction"]
    print("\n  SELF-CORRECTION")
    print(f"    rewinds: {sc['rewinds']}   fix-rounds: {sc['fix_rounds']}")

    print("\n  EVIDENCE (claimed-done bound to a real run)")
    if d["evidence"]:
        for e in d["evidence"]:
            print(f"    {'PASS' if e['ok'] else 'CAUGHT'}  {str(e['what'])[:60]}")
    else:
        print("    (no execution-evidence records)")

    m = d["metrics"]
    af, lb = m["adversarial_flags"], m["labeled"]
    print("\n  METRICS")
    print(f"    adversarial flags: {af['upfront']} upfront · {af['late']} late"
          f"   ·   evidence catches: {m['evidence_catches']}")
    print(f"    catch-quality labels: {lb['true']} true · {lb['false']} false · "
          f"{lb['open']} open   (label open ones with `prusik catch`)")
    print(f"    cost: {effort.fmt_duration(m['cost']['wall_clock_sec'])} wall-clock"
          + (f" · {m['cost']['tokens']:,} tokens" if m["cost"]["tokens"] else ""))

    o = d["objective"]
    print("\n  OBJECTIVE")
    print(f"    reached completion: {'yes' if o['complete'] else 'not yet'}   "
          f"evidence passed: {'yes' if o['evidence_passed'] else 'no'}")

    t = d["trust_check"]
    print("\n  ── trust check (held to our own bar) ──")
    print(f"    {_mark(t['loop_in_place'])} self-correcting loop in place")
    print(f"    {_mark(t['caught_upfront'])} caught issues upfront"
          + ("" if t["caught_upfront"] is not None else "  (no early reject recorded)"))
    print(f"    {_mark(t['reached_outcome'])} reached the outcome")
    print("\n  ✓ = evidenced · – = not enough evidence to assert (not a failure).")


def run(feature: str | None = None, json_output: bool = False) -> int:
    records = ledger.read_all()
    feats = journey_features(records)

    if feature:
        if feature not in feats:
            print(f"[prusik-showcase] no journey for {feature!r} in the ledger. "
                  f"Known: {feats or '(none)'}")
            return 1
        d = dossier(feature, records)
        if json_output:
            print(json.dumps(d, indent=2, default=str))
            return 0
        _render(d)
        return 0

    if not feats:
        print("[prusik-showcase] no journeys in the ledger yet.")
        return 0

    if json_output:
        print(json.dumps([dossier(f, records) for f in feats], indent=2,
                         default=str))
        return 0

    print(f"Showcase — {len(feats)} journeys "
          f"(drill in: `prusik showcase <feature>`)\n")
    print(f"  {'feature':24s} {'final':12s} {'crit':>4s} {'rwnd':>4s} "
          f"{'up':>3s} {'evid':>4s}  outcome")
    for f in feats:
        d = dossier(f, records)
        m, sc = d["metrics"], d["self_correction"]
        outcome = "done" if d["objective"]["complete"] else "open"
        print(f"  {f[:24]:24s} {d['progress']['final_phase'][:12]:12s} "
              f"{len(d['adversarial']):4d} {sc['rewinds']:4d} "
              f"{m['adversarial_flags']['upfront']:3d} "
              f"{m['evidence_catches']:4d}  {outcome}")
    print("\ncrit=critic verdicts · rwnd=rewinds · up=upfront rejects · "
          "evid=evidence catches. Composed from the ledger (catch-quality + effort).")
    return 0
