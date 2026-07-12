"""Catch-quality ledger — was each gate/critic fire a real catch or just friction?

The strategic gap (v0.45.0): the ledger records every time a gate blocks, a
critic flags, the evidence gate trips, or the FSM rewinds — but never whether
that fire was a TRUE catch (forced a real correction) or a FALSE block (friction
the operator routed around). Without that label you cannot tune friction
(which gates to relax), prove value (does the discipline catch real defects),
or tell which gates earn their cost. It is the one asset neither a single
sprint count nor the platform can supply.

Design: catches are DERIVED from the fire-events already in the ledger — one
source of truth, no double-emission. Resolutions are overlaid:
  1. operator labels (`catch_resolved` events) — authoritative;
  2. two unambiguous auto-rules —
       * evidence_gate fire (reviewer_execution_verified ok=False) is a TRUE
         catch by definition (it caught claimed-clean-that-wasn't);
       * a block immediately followed by `prusik disable` (no `enable`
         between) is a FALSE block — the operator routed around it;
  3. everything else stays UNRESOLVED until an operator labels it (no faked
     confidence).

The per-gate true/false ratio (`precision`) is the friction-value ratio.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from prusik import ledger

TRUE_CATCH = "true_catch"
FALSE_BLOCK = "false_block"
UNRESOLVED = "unresolved"

# fire-event -> (gate name, default severity). A catch CANDIDATE is one
# occurrence of a gate/critic doing its job: blocking, flagging, or rewinding.
_GATE_OF = {
    "reviewer_execution_verified": ("evidence_gate", "high"),    # only ok=False
    "critic_verdict":              ("critic", "high"),            # only non-approved
    "gate_blocked":                ("writable_gate", "medium"),
    "advance_blocked":             ("phase_gate", "medium"),
    "sprint_init_blocked":         ("phase_gate", "medium"),
    "sprint_start_blocked":        ("phase_gate", "medium"),
    "phase_rewind":                ("rewind", "medium"),
    "reviewer_binding_flagged":    ("binding_detector", "medium"),
    "reviewer_test_set_reach":     ("test_reach_detector", "low"),
    "reviewer_skip_flagged":       ("skip_detector", "medium"),
    "detector_flagged":            ("custom_detector", "medium"),
    "trivial_lane_rejected":       ("trivial_lane", "low"),
    # recall detectors (v0.173–v0.175) — out-of-diff escape catches. ADVISORY: a flag
    # is a CANDIDATE (a deferred artifact / a quoted-not-dismissed failure / an
    # intended skip-change can be a false-positive), so it accrues precision here and
    # EARNS gating only when proven, the way critic-capture earned its 1.0 (An adopter:
    # "a distrusted gate is worse than none"). No auto-rule → unresolved until labelled.
    "absence_flagged":             ("absence_detector", "medium"),
    "narrative_flagged":           ("narrative_detector", "high"),
    "delta_flagged":               ("delta_detector", "low"),
    "ui_e2e_flagged":              ("ui_coverage_detector", "high"),
}

_BLOCK_EVENTS = {"gate_blocked", "advance_blocked",
                 "sprint_init_blocked", "sprint_start_blocked"}

_APPROVED_VERDICTS = {"approved", "pass", "ok", "accept", "accepted"}


# --- writable-gate outcome capture (v0.46.0) -----------------------------
# The writable gate fires when an agent writes outside its phase's writable
# set. Outcome capture is derived from the append-only ledger ALONE — no
# sidecar, no hot-path I/O, no mutable shared state (see _build_blocks_resolved
# in resolve_catches). Key insight: in `building`/`solo_execute` the ONLY
# writable area IS the worktree, so a block there is always a correct
# worktree-isolation catch; if the feature later advances past build, the work
# was necessarily redirected into the worktree → confirmed true catch.

_BUILD_PHASES = {"building", "solo_execute"}


def catch_id(record: dict) -> str:
    """Stable 12-char id for a fire event. A ledger ts is effectively unique
    per event; the event name guards against a malformed/duplicate ts
    colliding two different fire types."""
    basis = f"{record.get('ts', '')}|{record.get('event', '')}"
    return hashlib.sha1(basis.encode()).hexdigest()[:12]


def _is_catch_fire(r: dict) -> bool:
    ev = r.get("event")
    if ev not in _GATE_OF:
        return False
    if ev == "reviewer_execution_verified":
        return not r.get("ok", True)          # only a non-run/failure is a catch
    if ev == "critic_verdict":
        return str(r.get("verdict", "")).strip().lower() not in _APPROVED_VERDICTS
    return True


def _summary_of(r: dict) -> str:
    ev = r.get("event")
    if ev == "critic_verdict":
        return f"{r.get('role', 'critic')} {r.get('verdict', '')}: {r.get('artifact', '')}".strip()
    if ev == "reviewer_execution_verified":
        return f"claimed-clean but ok=False: {r.get('command') or r.get('kind', '')}"
    for k in ("reason", "command", "target", "url", "contract_id", "detector"):
        if r.get(k):
            return str(r[k])
    return ev or ""


def extract_catches(records: list[dict]) -> list[dict]:
    """Derive catch candidates (one per qualifying fire event), in order."""
    out: list[dict] = []
    for r in records:
        if _is_catch_fire(r):
            gate, sev = _GATE_OF[r["event"]]
            out.append({
                "id": catch_id(r),
                "gate": gate,
                "severity": sev,
                "event": r["event"],
                "feature": r.get("feature"),
                "phase": r.get("phase") or r.get("from_phase"),
                "ts": r.get("ts", ""),
                "summary": _summary_of(r),
            })
    return out


def _infer_routed_around(records: list[dict]) -> set[str]:
    """A block whose next prusik toggle was `disable` (no `enable` between)
    was routed around → false_block. Conservative: only the single most-recent
    unresolved block before each `kit_disabled` is implicated."""
    auto: set[str] = set()
    last_block_id: str | None = None
    for r in records:
        ev = r.get("event")
        if ev in _BLOCK_EVENTS:
            last_block_id = catch_id(r)
        elif ev == "kit_enabled":
            last_block_id = None
        elif ev == "kit_disabled" and last_block_id:
            auto.add(last_block_id)
            last_block_id = None
    return auto


def _features_advanced_past_build(records: list[dict]) -> set:
    """Features that reached reviewing/integrating or completed — meaning their
    build phase finished, so any build-phase writable-gate block for them was
    necessarily redirected into the worktree (build's only writable area)."""
    out = set()
    for r in records:
        ev = r.get("event")
        if ev == "phase_advance" and r.get("to_phase") in ("reviewing", "integrating"):
            out.add(r.get("feature"))
        elif ev == "sprint_complete":
            out.add(r.get("feature"))
    return out


def _phase_gate_enforced(records: list[dict]) -> set[str]:
    """phase_gate blocks whose blocked transition LATER SUCCEEDED — meaning the gate
    enforced a missing exit-artifact / pre-sprint requirement that was then produced, and
    the transition went through once it existed (a true catch). The direct analog of the
    writable-gate's `_features_advanced_past_build` outcome signal, for the phase gate
    (closes the unmeasured-phase_gate gap noted in the v0.46.0 outcome-capture):

      - `advance_blocked` (feature F, → phase Y)  enforced by a later `phase_advance`
        (F, → Y) — the exit criterion the advance demanded was produced, then it advanced.
      - `sprint_start_blocked` / `sprint_init_blocked` (F) enforced by a later
        `sprint_started` (F) — the missing brief-critique / map / discovery was produced,
        then the sprint started.

    Ordered scan over the append-only ledger; a block with NO later matching success stays
    UNRESOLVED (we never claim a catch the ledger doesn't bear out). Multiple retries
    before success all count — the gate held until the requirement was met."""
    enforced: set[str] = set()
    pending_advance: dict[tuple, list[str]] = {}   # (feature, to_phase) → blocked ids
    pending_start: dict[Any, list[str]] = {}       # feature → blocked ids
    for r in records:
        ev = r.get("event")
        if ev == "advance_blocked":
            pending_advance.setdefault(
                (r.get("feature"), r.get("to_phase")), []).append(catch_id(r))
        elif ev in ("sprint_start_blocked", "sprint_init_blocked"):
            pending_start.setdefault(r.get("feature"), []).append(catch_id(r))
        elif ev == "phase_advance":
            for cid in pending_advance.pop((r.get("feature"), r.get("to_phase")), []):
                enforced.add(cid)
        elif ev == "sprint_started":
            for cid in pending_start.pop(r.get("feature"), []):
                enforced.add(cid)
    return enforced


def _critic_enforced(records: list[dict]) -> set[str]:
    """Critic non-approvals (reject/revise) that the ledger PROVES forced a real
    correction → true catches. The critic is the gate adopters value most ("it
    externalizes the adversary"), yet it had NO auto outcome-capture — every
    critic catch sat UNRESOLVED until a human hand-labelled it, so the gate's
    precision read blind by default (a fleet could show critic fired=16,
    true_catch=0 purely because nobody ran `prusik catch`). This closes that.

    Signal — the analog of `_phase_gate_enforced`, tightened by the content hash
    the critic gate already records:

      `critic_verdict`(role R, feature F, artifact A, verdict=NON-approved, hash=H1)
      enforced by a LATER `critic_verdict`(R, F, A, verdict=APPROVED, hash=H2)
      with H2 != H1 — the artifact was actually REVISED (hash changed) between the
      rejection and the approval, so the objection forced a correction the critic
      then accepted.

    The hash delta is what makes this honest-by-construction: an approval that
    re-stamps the SAME hash it just rejected is an override, not a correction, and
    does NOT count. A reject with no later differing-hash approval stays
    UNRESOLVED — we never claim a catch the ledger doesn't bear out. Multiple
    rejects before the approval all count (the gate held until the bar was met).
    """
    enforced: set[str] = set()
    # (role, feature, artifact) -> [(catch_id, content_hash), ...] pending rejects
    pending: dict[tuple, list[tuple[str, Any]]] = {}
    for r in records:
        if r.get("event") != "critic_verdict":
            continue
        keyt = (r.get("role"), r.get("feature"), r.get("artifact"))
        verdict = str(r.get("verdict", "")).strip().lower()
        if verdict not in _APPROVED_VERDICTS:
            pending.setdefault(keyt, []).append((catch_id(r), r.get("content_hash")))
        else:
            kept: list[tuple[str, Any]] = []
            for cid, h in pending.get(keyt, []):
                if h != r.get("content_hash"):
                    enforced.add(cid)        # content changed → objection enforced
                else:
                    kept.append((cid, h))    # same hash = override, not a catch
            if keyt in pending:
                pending[keyt] = kept
    return enforced


def _narrative_enforced(records: list[dict]) -> set[str]:
    """A narrative-detector flag that the ledger PROVES did its job → true catch.
    The flag says "a red was dismissed without proof"; if a proven baseline for that
    feature later appears, the flag forced exactly the proof it demanded — derivable
    from the ledger, NOT hand-labelled (An adopter: don't reintroduce the treadmill
    critic-capture climbed off). A flag with no later proof stays UNRESOLVED."""
    enforced: set[str] = set()
    pending: dict[Any, list[str]] = defaultdict(list)
    for r in records:
        ev = r.get("event")
        if ev == "narrative_flagged":
            pending[r.get("feature")].append(catch_id(r))
        elif ev == "known_failure_baseline" and r.get("proven"):
            for cid in pending.pop(r.get("feature"), []):
                enforced.add(cid)
    return enforced


def resolve_catches(catches: list[dict], records: list[dict]) -> list[dict]:
    """Attach verdict/source/reason to each catch. Operator labels win; then
    the auto-rules; else unresolved. Pure — re-derivable from the ledger."""
    operator: dict[str, tuple[Any, str]] = {}
    for r in records:
        if r.get("event") == "catch_resolved":
            cid = r.get("catch_id")
            if cid:                       # ignore a malformed resolution w/o id
                operator[cid] = (r.get("verdict"), r.get("reason", ""))
    routed_around = _infer_routed_around(records)
    advanced_past_build = _features_advanced_past_build(records)
    phase_enforced = _phase_gate_enforced(records)
    critic_enforced = _critic_enforced(records)
    narrative_enforced = _narrative_enforced(records)
    for c in catches:
        build_block = (c["event"] == "gate_blocked"
                       and c.get("phase") in _BUILD_PHASES
                       and c.get("feature") in advanced_past_build)
        if c["id"] in operator:
            c["verdict"], c["reason"] = operator[c["id"]]
            c["source"] = "operator"
        elif c["event"] == "reviewer_execution_verified":
            c["verdict"], c["reason"], c["source"] = (
                TRUE_CATCH, "evidence gate caught a non-run/failure", "auto")
        elif build_block:
            c["verdict"], c["reason"], c["source"] = (
                TRUE_CATCH,
                "worktree-isolation block; feature advanced past build → "
                "the write was redirected into the worktree", "auto")
        elif c["gate"] == "phase_gate" and c["id"] in phase_enforced:
            c["verdict"], c["reason"], c["source"] = (
                TRUE_CATCH,
                "phase gate enforced a missing exit-artifact/requirement; the blocked "
                "transition later succeeded once it was produced", "auto")
        elif c["gate"] == "critic" and c["id"] in critic_enforced:
            c["verdict"], c["reason"], c["source"] = (
                TRUE_CATCH,
                "critic rejected; the artifact was revised (content hash changed) and a "
                "later critic verdict approved it → the objection forced a correction", "auto")
        elif c["gate"] == "narrative_detector" and c["id"] in narrative_enforced:
            c["verdict"], c["reason"], c["source"] = (
                TRUE_CATCH,
                "narrative flag of a proofless dismissal; a proven baseline for the "
                "feature later appeared → the flag forced the proof it demanded", "auto")
        elif c["id"] in routed_around:
            c["verdict"], c["reason"], c["source"] = (
                FALSE_BLOCK, "hooks disabled right after this block", "auto")
        else:
            c["verdict"], c["reason"], c["source"] = UNRESOLVED, "", ""
    return catches


def summarize(catches: list[dict]) -> dict[str, dict[str, Any]]:
    """Per-gate fired/true/false/unresolved counts + precision (true/resolved)."""
    by_gate: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"fired": 0, TRUE_CATCH: 0, FALSE_BLOCK: 0, UNRESOLVED: 0})
    for c in catches:
        g = by_gate[c["gate"]]
        g["fired"] += 1
        g[c["verdict"]] += 1
    for g in by_gate.values():
        resolved = g[TRUE_CATCH] + g[FALSE_BLOCK]
        g["precision"] = (g[TRUE_CATCH] / resolved) if resolved else None
    return dict(by_gate)


def resolve(cid: str, verdict: str, reason: str = "") -> int:
    """Operator labels a catch. Emits a `catch_resolved` event."""
    if verdict not in (TRUE_CATCH, FALSE_BLOCK):
        print(f"[prusik-catch] verdict must be {TRUE_CATCH} or {FALSE_BLOCK}")
        return 2
    catches = extract_catches(ledger.read_all())
    if not any(c["id"] == cid for c in catches):
        print(f"[prusik-catch] unknown catch id {cid!r} "
              f"(run `prusik catches` to list ids)")
        return 1
    ledger.append("catch_resolved", catch_id=cid, verdict=verdict,
                  reason=reason, source="operator")
    print(f"[prusik-catch] {cid} → {verdict}"
          + (f"  ({reason})" if reason else ""))
    return 0


def run(json_output: bool = False) -> int:
    records = ledger.read_all()
    catches = resolve_catches(extract_catches(records), records)
    summary = summarize(catches)
    if json_output:
        print(json.dumps({"total": len(catches), "by_gate": summary,
                          "catches": catches}, indent=2))
        return 0

    if not catches:
        print("[prusik-catch] no gate/critic fires in the ledger yet.")
        return 0

    print(f"Catch-quality — {len(catches)} fires across {len(summary)} gates\n")
    print(f"  {'gate':20s} {'fired':>5s} {'true':>5s} {'false':>5s} "
          f"{'open':>5s}  precision")
    for gate in sorted(summary, key=lambda g: -summary[g]["fired"]):
        s = summary[gate]
        prec = "—" if s["precision"] is None else f"{100 * s['precision']:.0f}%"
        print(f"  {gate:20s} {s['fired']:5d} {s[TRUE_CATCH]:5d} "
              f"{s[FALSE_BLOCK]:5d} {s[UNRESOLVED]:5d}  {prec:>9s}")

    unresolved = [c for c in catches if c["verdict"] == UNRESOLVED]
    if unresolved:
        print(f"\n{len(unresolved)} unresolved — label with "
              f"`prusik catch <id> --true|--false [--reason ...]`:")
        for c in unresolved[-15:]:
            print(f"  {c['id']}  {c['gate']:18s} {(c['phase'] or '-'):12s} "
                  f"{c['summary'][:60]}")
        if len(unresolved) > 15:
            print(f"  … and {len(unresolved) - 15} more (use --json for all)")
    print("\nprecision = true_catch / (true_catch + false_block); '—' = "
          "nothing resolved yet. Unresolved are excluded — label them to "
          "sharpen the ratio.")
    return 0
