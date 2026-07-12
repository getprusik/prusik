"""Critic-recall lens — what the critics MISSED (the unmeasured half of trust).

Precision (catch_quality, v0.168) answers "of what the critics flagged, how much
was real?" and is now derivable, because a CATCH writes a
rejection→revision→approval trail. RECALL — "of the real defects, how many did the
critics catch?" — cannot be derived the same way: a MISS leaves no trail, because
the critic passed (An adopter, 2026-06-09). So recall is inferred from DOWNSTREAM
CATCHES WITH PROVENANCE: when a later gate (the evidence gate, a CI criterion, the
integrator, or the operator) catches a defect a critic had approved, that delta is
a recall failure — and the later-catcher names which critic should have owned it.

An adopter's escape taxonomy — the CLASS matters more than the instance, because each is
a pattern the critics are structurally blind to: all OUT of the diff, where strong
in-diff correctness review can't reach.

Honest by construction (mirrors catch_quality's unresolved-excluded rule): an
inferred candidate is NOT a confirmed miss. recall_pct is computed over LABELLED
misses only and is reported as an UPPER BOUND while candidates are pending — every
real-but-unconfirmed candidate only lowers it, so a clean number can never hide an
unrecorded miss. Candidates are surfaced for a human/agent to confirm (or dismiss
as structurally-downstream, e.g. cross_integration, which the CI layer owns, not a
reviewing critic).
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from prusik import catch_quality, ledger

# An adopter's escape taxonomy. `pre_integration_detectable` = whether a reviewing-phase
# critic COULD have caught it; a False class is owned by a later layer (CI), so a
# miss there is NOT charged to a reviewing critic — it's a different gap.
ESCAPE_CLASSES: dict[str, dict[str, Any]] = {
    "absence": {
        "label": "planned deliverable silently not produced (no diff to review)",
        "owner": "scope-critic / conventions-enforcer",
        "pre_integration_detectable": True,
    },
    "cross_integration": {
        "label": "defect only manifests post-merge (co-render / interaction)",
        "owner": "ci-criterion (structurally post-integration)",
        "pre_integration_detectable": False,
    },
    "narrative_claim": {
        "label": "un-gated builder assertion (baseline-proven / pre-existing / flake)",
        "owner": "builder-claim gate",
        "pre_integration_detectable": True,
    },
    "unexamined_delta": {
        "label": "worktree↔integrated metric delta nobody ran down (e.g. skip count)",
        "owner": "integrator",
        "pre_integration_detectable": True,
    },
}

_APPROVED = catch_quality._APPROVED_VERDICTS


def _approvals(records: list[dict]) -> dict[Any, list[tuple[str, Any]]]:
    """feature → [(ts, role), …] approving critic verdicts, in ledger order."""
    out: dict[Any, list[tuple[str, Any]]] = defaultdict(list)
    for r in records:
        if (r.get("event") == "critic_verdict"
                and str(r.get("verdict", "")).strip().lower() in _APPROVED):
            out[r.get("feature")].append((r.get("ts", ""), r.get("role")))
    return out


def infer_candidates(records: list[dict]) -> list[dict]:
    """Downstream factual catches that POSTDATE a critic approval on the same
    feature = candidate recall-misses. Conservative auto-signals — each a real
    defect the ledger bears out, occurring AFTER a critic said the feature was fine:
      * `reviewer_execution_verified` ok=False (the evidence gate caught a
        claimed-clean-that-wasn't), and
      * `phase_rewind` (the FSM went backward — something approved was wrong).
    These are CANDIDATES, not charges: the approving role is a *suggested* owner the
    operator confirms or dismisses (the catch may be on a different axis than the
    critic reviewed). Provenance, not blame."""
    approvals = _approvals(records)
    # collapse by (feature, catcher): repeated same-kind catches on one feature are
    # ONE frontier item to triage, not N — a count, not noise. Keeps the earliest
    # occurrence + a sample summary; merges the suggested owners.
    grouped: dict[tuple, dict] = {}
    for r in records:
        ev = r.get("event")
        if ev == "reviewer_execution_verified" and not r.get("ok", True):
            catcher = "evidence_gate"
        elif ev == "phase_rewind":
            catcher = "rewind"
        else:
            continue
        feat, ts = r.get("feature"), r.get("ts", "")
        prior_roles = {role for a_ts, role in approvals.get(feat, [])
                       if role and a_ts and a_ts < ts}
        if not prior_roles:
            continue
        key = (feat, catcher)
        g = grouped.get(key)
        if g is None:
            grouped[key] = {
                "feature": feat,
                "ts": ts,
                "downstream_catcher": catcher,
                "candidate_owners": set(prior_roles),
                "count": 1,
                "summary": catch_quality._summary_of(r),
            }
        else:
            g["count"] += 1
            g["candidate_owners"] |= prior_roles
            if ts and ts < g["ts"]:
                g["ts"] = ts
    cands = []
    for g in grouped.values():
        g["candidate_owners"] = sorted(g["candidate_owners"])
        cands.append(g)
    return sorted(cands, key=lambda c: (-c["count"], str(c["feature"])))


def _confirmed_misses(records: list[dict]) -> list[dict]:
    return [r for r in records if r.get("event") == "critic_miss"]


def recall_summary(records: list[dict]) -> dict[str, Any]:
    """catches = critic true-catches (catch_quality); misses = confirmed
    `critic_miss` events. recall_pct = catches / (catches + misses) is an UPPER
    BOUND while candidates are pending (every real candidate lowers it)."""
    catches = catch_quality.resolve_catches(
        catch_quality.extract_catches(records), records)
    critic_catches = sum(1 for c in catches
                         if c["gate"] == "critic"
                         and c["verdict"] == catch_quality.TRUE_CATCH)
    misses = _confirmed_misses(records)
    by_class: dict[str, int] = defaultdict(int)
    by_owner: dict[str, int] = defaultdict(int)
    for m in misses:
        by_class[m.get("defect_class", "?")] += 1
        by_owner[m.get("owner", "?")] += 1
    denom = critic_catches + len(misses)
    candidates = infer_candidates(records)
    return {
        "catches": critic_catches,
        "misses": len(misses),
        "recall_pct": round(100 * critic_catches / denom) if denom else None,
        "is_upper_bound": bool(candidates),
        "pending_candidates": len(candidates),
        "by_class": dict(by_class),
        "by_owner": dict(by_owner),
        "candidates": candidates,
    }


def record_miss(defect_class: str, owner: str, feature: str | None = None,
                source: str = "operator", reason: str = "") -> int:
    """Record an observed escape — a real defect a critic passed — with provenance
    (which class, which critic should have owned it). The honest way recall accrues:
    a miss leaves no trail of its own, so the later-catcher writes one here."""
    if defect_class not in ESCAPE_CLASSES:
        print(f"[prusik-recall] unknown class {defect_class!r}; choose from: "
              f"{', '.join(ESCAPE_CLASSES)}")
        return 2
    if not owner:
        print("[prusik-recall] --owner is required (which critic should have "
              "caught it, or the later layer that did)")
        return 2
    ledger.append("critic_miss", defect_class=defect_class, owner=owner,
                  feature=feature, source=source, reason=reason)
    print(f"[prusik-recall] recorded miss: {defect_class} → owner {owner}"
          + (f"  ({reason})" if reason else ""))
    return 0


def run(json_output: bool = False) -> int:
    records = ledger.read_all()
    s = recall_summary(records)
    if json_output:
        print(json.dumps(s, indent=2, default=str))
        return 0

    rp = "—" if s["recall_pct"] is None else f"{s['recall_pct']}%"
    bound = " (upper bound)" if s["is_upper_bound"] else ""
    print(f"Critic recall — {s['catches']} caught · {s['misses']} missed "
          f"(confirmed) → recall {rp}{bound}\n")
    print("  recall = critic true-catches / (true-catches + confirmed misses). "
          "A miss leaves no ledger\n  trail, so it must be RECORDED "
          "(`prusik critic-miss`); candidates below are unconfirmed and\n  only "
          "LOWER the true recall — a clean number can't hide an unrecorded miss.")

    if s["by_class"]:
        print("\n  confirmed misses by class:")
        for k in sorted(s["by_class"]):
            lbl = ESCAPE_CLASSES.get(k, {}).get("label", "")
            print(f"    {k:18s} {s['by_class'][k]:3d}  {lbl}")

    cands = s["candidates"]
    if cands:
        print(f"\n  {s['pending_candidates']} candidate miss(es) — a downstream "
              f"catch postdated a critic approval.\n  Confirm with `prusik "
              f"critic-miss --class <c> --owner <role> --feature <f>`, or dismiss:")
        for c in cands[-15:]:
            owners = ",".join(c["candidate_owners"]) or "-"
            print(f"    {(c['feature'] or '-'):16.16s}  caught-by:"
                  f"{c['downstream_catcher']:13s} owner?:{owners:24.24s} "
                  f"{c['summary'][:34]}")
        if len(cands) > 15:
            print(f"    … and {len(cands) - 15} more (use --json for all)")
    else:
        print("\n  no candidate misses inferred from the ledger.")

    print("\n  escape classes (field taxonomy — critics are strong in-diff, blind "
          "out-of-diff):")
    for k, v in ESCAPE_CLASSES.items():
        det = "pre-integration" if v["pre_integration_detectable"] else "post-merge only"
        print(f"    {k:18s} [{det:15s}] {v['label']}")
    return 0
