"""Self-learning loop — the PIPE and, since v0.194.0, the ACTUATOR (instrument
layer, piece 5).

The MOAT: consume the labeled outcome ledger to tune which gates fire PER the
fleet, so the guardrails get better over time without a maintainer hand-patching
every recurrence. Two halves:

  PIPE (v0.51.0) — ledger → per-gate calibration signal → recommendation. Pure
  read; advisory text only. `calibration_signals` / `run`. Per-adopter this stays
  advisory by design: on one codebase (N=1) the labels overfit one project's
  quirks (`is_fueled` is False on a single ledger), so nothing auto-applies.

  ACTUATOR (v0.194.0, refined v0.196.0) — promotion is advisory→gating, and there
  are TWO paths to it, split after the first real-fleet run (fb-f737a0b753bd):
    - AUTO (the loop): `fleet_suggestions` (fed per-adopter signals by HQ's
      `hq.calibrate`) names AUTO_PROMOTABLE detectors the FLEET has proven; CROSS-
      VALIDATED — the precision bar cleared in ≥2 codebases INDEPENDENTLY (never one
      noisy project summed into a quorum), the reason it waited for N≥2. AUTO_PROMOTABLE
      is EMPTY today by design: sound auto-promotion needs a high-volume tunable
      detector whose false-block rate the ledger can observe; the recall detectors
      don't qualify (rare + asymmetric auto-resolution), so the loop is correctly
      DORMANT, not broken.
    - HUMAN (the operator): `apply` promotes any GATEABLE detector (the recall
      detectors), a deliberate operator decision informed by the recall instrument /
      trust-report. The operator owns the false-block risk, the same as `--strict`.
  Both paths are TIGHTENING-ONLY: advisory→gating only. Neither loosens, disables, or
  tunes a preventive control — a defect cannot become a feature via this loop, and
  there is no `apply` path that turns a gate off. HQ only ever SUGGESTS; it never
  reaches into a config.

It also encodes the measurement caveat so it never gives wrong advice: catch-
PRECISION is the right lens for DETECTORS but the WRONG lens for PREVENTIVE
controls (gates, rewinds) — whose value is the divergence they PREVENT, invisible
by construction. The advisor never recommends loosening a preventive control on
precision, no matter how low it is.

Composes `catch_quality` (never re-derives). The PIPE changes no config; the
ACTUATOR writes only the machine-owned `.sprint/calibration.json` overlay, and
only ever to tighten.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from prusik import catch_quality as cq
from prusik import ledger

# Preventive controls: value = divergence PREVENTED (invisible). Precision is NOT
# a tuning signal for these — never loosen on it (strategy §8 caveat).
_PREVENTIVE = {"writable_gate", "phase_gate", "rewind"}
# Detector/evidence: precision IS meaningful — low precision + fuel = a candidate.
# Includes the out-of-diff recall detectors (v0.173–v0.175): their precision
# accrues in catch_quality, so calibration can reason about whether they've EARNED
# gating across the fleet.
_DETECTOR = {"evidence_gate", "critic", "binding_detector", "test_reach_detector",
             "skip_detector", "custom_detector", "trivial_lane",
             "absence_detector", "narrative_detector", "delta_detector",
             "ui_coverage_detector"}

# Below this many LABELED (true+false) fires, even a detector signal is too thin
# to act on — refuse to tune on a handful of points.
_MIN_RESOLVED = 10
# A detector whose precision clears this in a codebase has earned trust THERE.
_PRECISION_BAR = 0.8

# ── Two distinct universes: who may gate, vs what the loop may AUTO-promote ────
#
# The v0.194 design conflated these; v0.196 splits them after the first real-fleet
# run (fb-f737a0b753bd) showed why they differ.
#
# GATEABLE — advisory recall checks a HUMAN may turn into a hard gate (rc≠0 on a
# flag), via `prusik calibrate apply`, the `gate_on` overlay/config, or `--strict`.
# Gating one is a HUMAN decision: the operator owns the false-block risk, informed
# by the recall instrument / trust-report. Tightening-only (advisory→gating); never
# loosens, never touches a preventive control. Maps gate key → overlay knob name.
GATEABLE = {
    "absence_detector":     "absence_detector",
    "narrative_detector":   "narrative_detector",
    "ui_coverage_detector": "ui_coverage_detector",
}

# AUTO_PROMOTABLE — detectors the calibration LOOP may auto-SUGGEST promoting from
# cross-fleet precision. EMPTY today BY DESIGN: sound auto-promotion needs a
# HIGH-VOLUME tunable detector whose false-block rate the ledger can OBSERVE. The
# recall detectors (absence/narrative/ui) don't qualify — they are rare-by-design
# and their auto-resolution is asymmetric: a true catch only when a flagged gap
# LATER closes, and NO auto-false rule, so the most valuable catches (real omissions
# never remediated) stay unresolved forever and auto-precision is sparse +
# upward-biased + blind to the false-block rate a gate must be judged on. So they
# earn gating by OPERATOR DECISION (GATEABLE), not by the loop. The loop's actuator
# stays correct and DORMANT until a high-volume advisory detector exists — register
# it here {gate_key: overlay_knob} and it becomes auto-suggestable. (See [[project-
# prusik-strategic-posture]] / fb-f737a0b753bd for the category-mismatch finding.)
AUTO_PROMOTABLE: dict[str, str] = {}

# RESERVED: a detector deliberately kept human-adjudicated PER-FLAG — its flags need
# a human call each time (a skip-delta is benign env-gating OR real silent loss; only
# a person can tell), so it is neither blanket-gateable nor auto-promotable.
_RESERVED = {"delta_detector"}

# The machine-OWNED overlay the actuator writes — kept OUT of the hand-authored,
# heavily-commented sprint-config.yaml (editing that YAML would clobber comments;
# see the v0.124 additive-merge footgun). The config read path UNIONs this with any
# `gate_on` the operator sets in sprint-config.yaml, so both channels compose.
OVERLAY_PATH = ".sprint/calibration.json"


def classify(gate: str) -> str:
    if gate in _PREVENTIVE:
        return "preventive"
    if gate in _DETECTOR:
        return "detector"
    return "unknown"


def _recommend(kind: str, precision: float | None,
               resolved: int) -> tuple[str, str]:
    if kind == "preventive":
        return ("keep", "preventive control — precision is NOT a tuning signal; "
                "its value is the divergence it prevents (invisible). "
                "Never loosen on precision.")
    if kind == "unknown":
        return ("observe", "unclassified gate — observe before any tuning.")
    if resolved < _MIN_RESOLVED or precision is None:
        return ("insufficient", f"only {resolved} labeled fires — too thin to "
                f"tune (need ≥{_MIN_RESOLVED}; label with `prusik catch`).")
    if precision >= 0.8:
        return ("keep", f"effective ({precision:.0%} precision) — keep.")
    return ("review", f"noisy ({precision:.0%} precision) — review the false "
            f"blocks before any change.")


def calibration_signals(records: list[dict] | None = None) -> dict[str, dict[str, Any]]:
    """Per-gate calibration signal + recommendation, derived from catch-quality.
    Advisory — encodes the preventive-vs-detector caveat; applies nothing."""
    recs = ledger.read_all() if records is None else records
    summary = cq.summarize(cq.resolve_catches(cq.extract_catches(recs), recs))
    out: dict[str, dict[str, Any]] = {}
    for gate, s in summary.items():
        kind = classify(gate)
        true_c, false_c = s[cq.TRUE_CATCH], s[cq.FALSE_BLOCK]
        resolved = true_c + false_c
        rec, why = _recommend(kind, s["precision"], resolved)
        out[gate] = {
            "kind": kind, "fired": s["fired"], "true": true_c, "false": false_c,
            "open": s[cq.UNRESOLVED], "precision": s["precision"],
            "resolved": resolved, "recommendation": rec, "why": why,
        }
    return out


def is_fueled(signals: dict[str, dict[str, Any]], *, codebases: int = 1) -> bool:
    """Whether the self-learning loop COULD be closed. False BY DESIGN on a
    single codebase — one project's labels overfit. Closing needs cross-codebase
    fuel (N≥2) AND at least one detector with enough labeled data. A future
    actuator must gate on this; on a single ledger it always returns False, so
    nothing is auto-applied."""
    if codebases < 2:
        return False
    return any(s["kind"] == "detector" and s["resolved"] >= _MIN_RESOLVED
               for s in signals.values())


# ── the overlay: which detectors are currently PROMOTED to gating ─────────────

def _overlay(root: Path | None = None) -> dict[str, Any]:
    root = root or ledger.project_root()
    path = root / OVERLAY_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}   # a corrupt overlay must never block a check — fail to advisory


def gate_on(root: Path | None = None) -> set[str]:
    """The set of detectors currently PROMOTED to gating, from BOTH channels: the
    actuator-written overlay AND any `gate_on` list the operator set by hand in
    sprint-config.yaml. Union — the operator can promote manually too."""
    root = root or ledger.project_root()
    promoted: set[str] = set()
    ov = _overlay(root).get("gate_on")
    if isinstance(ov, list):
        promoted |= {str(d) for d in ov}
    try:
        from prusik import phases
        cfg = (phases.load_sprint_config(root) or {}).get("gate_on")
        if isinstance(cfg, list):
            promoted |= {str(d) for d in cfg}
    except Exception:  # noqa: BLE001 — a config read must never break a detector
        pass
    return promoted & set(GATEABLE)   # only ever a human-gateable detector is honored


def is_promoted(detector: str, root: Path | None = None) -> bool:
    """Has this advisory detector been promoted to a hard gate (overlay or config)?
    A promoted detector's check runs with effective `--strict` everywhere — the
    one place the closed loop changes runtime behavior, and only ever to TIGHTEN."""
    return detector in gate_on(root)


# ── cross-adopter signal → tighten-only suggestion (the brain) ────────────────

def signals_from_trust(trust: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-gate calibration signal from ONE adopter's exported `metrics.trust`
    block (fired/true_catch/false_block/precision). The HQ-side analog of
    `calibration_signals` (which reads a local ledger): the export already carries
    the per-gate counts, so HQ derives the same kind+recommendation without the raw
    ledger — keeping the precision-vs-preventive caveat in this one module."""
    out: dict[str, dict[str, Any]] = {}
    for gate, s in (trust or {}).items():
        true_c = int(s.get("true_catch", 0) or 0)
        false_c = int(s.get("false_block", 0) or 0)
        resolved = true_c + false_c
        prec = s.get("precision")
        kind = classify(gate)
        rec, why = _recommend(kind, prec, resolved)
        out[gate] = {"kind": kind, "fired": int(s.get("fired", 0) or 0),
                     "true": true_c, "false": false_c, "precision": prec,
                     "resolved": resolved, "recommendation": rec, "why": why}
    return out


def fleet_suggestions(adopter_signals: list[dict[str, dict[str, Any]]],
                      codebases: int) -> list[dict[str, Any]]:
    """The actuator's BRAIN: turn per-adopter calibration signals into AUTO-promotion
    suggestions — cross-validated, tightening-only.

    Anti-overfit by CROSS-VALIDATION (the whole reason the loop waited for N≥2):
    a promotion is suggested only when an AUTO_PROMOTABLE detector clears the
    precision bar with enough labeled fires in ≥2 codebases INDEPENDENTLY — never by
    summing one noisy project's points into a quorum. A signal that holds on one repo
    is that repo's quirk; a signal that holds across repos is real.

    Iterates AUTO_PROMOTABLE, which is EMPTY today by design (the recall detectors are
    human-gated via GATEABLE, not auto-promoted — see that constant). So on the current
    fleet this correctly returns [] : the loop is dormant, not broken. It comes alive
    the moment a high-volume tunable detector is registered in AUTO_PROMOTABLE.

    NEVER suggests loosening (a noisy detector → human REVIEW, never an auto-relax),
    NEVER touches a preventive control, NEVER auto-promotes the reserved detector.
    Returns suggestions only; applying one is a separate, human-approved act."""
    suggestions: list[dict[str, Any]] = []
    if codebases < 2:
        return suggestions   # is_fueled's precondition — no cross-validation possible
    for detector in sorted(AUTO_PROMOTABLE):
        labeled = [s[detector] for s in adopter_signals
                   if detector in s and s[detector]["resolved"] >= _MIN_RESOLVED]
        proven = [s for s in labeled
                  if s["precision"] is not None and s["precision"] >= _PRECISION_BAR]
        noisy = [s for s in labeled
                 if s["precision"] is not None and s["precision"] < _PRECISION_BAR]
        if len(proven) >= 2:
            precs = [s["precision"] for s in proven]
            suggestions.append({
                "detector": detector, "action": "promote", "knob": "gate_on",
                "value": AUTO_PROMOTABLE[detector],
                "codebases_proven": len(proven),
                "min_precision": min(precs), "resolved_total": sum(s["resolved"] for s in proven),
                "rationale": (f"{detector} clears {_PRECISION_BAR:.0%} precision with "
                              f"≥{_MIN_RESOLVED} labeled fires in {len(proven)} codebases "
                              f"(min {min(precs):.0%}) — cross-validated, advisory→gating "
                              f"(tightens; never loosens)."),
                "apply": f"prusik calibrate apply {detector}",
            })
        elif len(noisy) >= 2:
            suggestions.append({
                "detector": detector, "action": "review", "knob": None, "value": None,
                "codebases_noisy": len(noisy),
                "max_precision": max(s["precision"] for s in noisy),
                "rationale": (f"{detector} is noisy (<{_PRECISION_BAR:.0%}) across "
                              f"{len(noisy)} codebases — a HUMAN reviews the false blocks; "
                              f"the loop never auto-loosens a gate."),
                "apply": None,
            })
    return suggestions


# ── the guarded apply (the hand) — tighten-only, human-invoked, audited ───────

def apply(detector: str, root: Path | None = None, *,
          evidence: dict[str, Any] | None = None) -> int:
    """Promote ONE advisory detector to gating — the human-approved actuator.

    This is the OPERATOR's gate decision (informed by HQ suggestions when the loop
    has them, or by the recall instrument / trust-report for the recall detectors).
    The hard guards (a defect cannot become a feature here):
      - ONLY a GATEABLE detector (advisory recall check) — refuses preventive
        controls, already-gating gates (evidence/critic), the reserved detector,
        and anything unknown, fail-closed (rc≠0).
      - The mutation is monotone-TIGHTENING: it can only turn a gate ON. There is
        no `apply` path that turns one off (loosening is a deliberate hand-edit).
    Writes the machine-owned overlay (never the commented YAML), additively, and
    records a `calibration_applied` ledger event so every promotion is auditable."""
    root = root or ledger.project_root()
    detector = detector.strip()
    if detector in _RESERVED:
        print(f"[prusik-calibrate] REFUSED: {detector} is human-adjudicated per-flag — "
              f"its flags need a person's call each time, not a blanket gate. Review "
              f"with `prusik delta-check`.")
        ledger.append("calibration_apply_refused", detector=detector,
                      reason="reserved-human-adjudicated")
        return 2
    if detector not in GATEABLE:
        print(f"[prusik-calibrate] REFUSED: {detector!r} is not a gateable advisory "
              f"detector. `apply` only promotes advisory→gating (tightening); it never "
              f"disables a gate or tunes a preventive control. Gateable: "
              f"{', '.join(sorted(GATEABLE))}.")
        ledger.append("calibration_apply_refused", detector=detector,
                      reason="not-gateable")
        return 2
    path = root / OVERLAY_PATH
    data = _overlay(root)
    gated = data.get("gate_on")
    if not isinstance(gated, list):
        gated = []
    if detector in gated:
        print(f"[prusik-calibrate] already gating: {detector}")
        return 0
    gated.append(detector)
    data["gate_on"] = gated
    log = data.get("log")
    if not isinstance(log, list):
        log = []
    log.append({"detector": detector, "evidence": evidence or {}})
    data["log"] = log
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    ledger.append("calibration_applied", detector=detector, knob="gate_on",
                  evidence=evidence or {})
    print(f"[prusik-calibrate] promoted {detector} → GATING. Its check now returns "
          f"rc≠0 when it flags (was advisory).")
    if evidence:
        print(f"  basis: {evidence}")
    print(f"  Local overlay ({OVERLAY_PATH}) — for a team-wide, committed "
          "promotion add it to `gate_on` in .claude/sprint-config.yaml.")
    print("  Tightening-only; reversible by hand (remove it from the overlay or "
          "config).")
    return 0


def run(json_output: bool = False) -> int:
    signals = calibration_signals()
    fueled = is_fueled(signals)   # single ledger → codebases=1 → False

    if json_output:
        print(json.dumps({"loop": "open", "fueled": fueled, "signals": signals,
                          "note": "advisory only; nothing applied (loop is open "
                          "until N≥2 + labeled fuel)"}, indent=2))
        return 0

    if not signals:
        print("[prusik-calibrate] no gate/critic fires in the ledger yet — "
              "nothing to calibrate.")
        return 0

    print("Calibration — self-learning loop is OPEN (advisory only, nothing "
          "applied)\n")
    print(f"  {'gate':18s} {'kind':10s} {'fired':>5s} {'prec':>5s}  recommendation")
    for gate in sorted(signals, key=lambda g: -signals[g]["fired"]):
        s = signals[gate]
        prec = "—" if s["precision"] is None else f"{100 * s['precision']:.0f}%"
        print(f"  {gate:18s} {s['kind']:10s} {s['fired']:5d} {prec:>5s}  "
              f"{s['recommendation']}")

    print("\n  why each:")
    for gate in sorted(signals, key=lambda g: -signals[g]["fired"]):
        print(f"    {gate}: {signals[gate]['why']}")

    promoted = sorted(gate_on())
    if promoted:
        print("\n  already promoted to GATING (advisory→gating, via the actuator):")
        for d in promoted:
            print(f"    ✓ {d}")

    print("\n── this view is per-codebase, so it stays ADVISORY ──")
    print("  On ONE codebase the labels overfit that project — tuning on them would")
    print("  bake in noise (the looks-done-isn't failure we exist to prevent), so")
    print("  nothing here is ever auto-applied. The loop CLOSES across the fleet:")
    print("  when ≥2 codebases independently prove an advisory detector, HQ's")
    print("  `python -m hq.calibrate` surfaces it and you promote it with")
    print("  `prusik calibrate apply <detector>` (tightening-only, you approve).")
    print(f"\n  fueled: {fueled}   (single codebase → advisory only, never auto-applied)")
    return 0
