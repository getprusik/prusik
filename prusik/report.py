"""Product report — one composed health snapshot for an adopter (v0.54.0).

The unified "how is my product doing under prusik" view: it composes the
instrument layer — catch-quality (TRUST), effort (VALUE/progress), the ledger
(health), calibration (improvement) — into one value-chain-framed snapshot,
rather than making the operator stitch `catches` + `effort` + `calibrate`
together by hand. Pure read over the append-only ledger; composes, never
re-derives.

This is the PER-PRODUCT monitoring surface. Fleet/HQ aggregation is deliberately
NOT here: prusik never phones home (the ledger stays local — the non-coupling
property that makes it adoptable). Cross-product visibility is an explicit,
opt-in export an adopter chooses to share, never silent telemetry.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from prusik import __version__
from prusik import calibration as cal
from prusik import catch_quality as cq
from prusik import effort, ledger

# Bump only on a breaking change to the export payload shape, so an HQ-side
# aggregator can refuse / adapt to versions it doesn't understand.
EXPORT_SCHEMA_VERSION = "1.0"
DEFAULT_EXPORT_PATH = ".sprint/report-export.json"


def build(records: list[dict]) -> dict[str, Any]:
    """Compose the value-chain snapshot from the ledger."""
    catches = cq.resolve_catches(cq.extract_catches(records), records)
    trust = cq.summarize(catches)

    feats = effort.summarize_features(records)
    phases = effort.summarize_phases(records)
    done = sum(1 for f in feats.values() if f.get("complete"))
    n = len(feats)

    return {
        "events": len(records),
        "journeys": n,
        "completed": done,
        "completion_pct": (100 * done // n) if n else 0,
        "total_wall_sec": sum(f.get("wall_clock_sec") or 0 for f in feats.values()),
        "open_features": [k for k, f in feats.items() if not f.get("complete")],
        "convergence_stalls": sum(1 for r in records
                                  if r.get("event") == "convergence_stall"),
        "trust": trust,
        "phases": phases,
        "loop_fueled": cal.is_fueled(cal.calibration_signals(records)),
    }


def _product_hash(root: Path) -> str:
    """One-way, stable per-repo id. Lets an HQ aggregator count DISTINCT adopters
    and dedupe re-submissions WITHOUT learning the path, repo name, or anything
    about the source — the hash is irreversible and carries no identifying text."""
    return hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]


def timeseries(records: list[dict]) -> list[dict[str, Any]]:
    """Per-MONTH activity derived from ledger timestamps — the raw material for
    TRENDS (the snapshot→trajectory upgrade, A1). Counts journeys started /
    completed and total events per `YYYY-MM` bucket. Pure derivation from the
    timestamps already in the ledger: no names, no wall-clock, no paths — so it's
    export-safe by construction and lets HQ plot adoption velocity / value
    trajectory over time instead of a single 'as of' snapshot."""
    buckets: dict[str, dict[str, Any]] = {}
    for r in records:
        ts = r.get("ts")
        if not isinstance(ts, str) or len(ts) < 7:
            continue
        b = buckets.setdefault(ts[:7], {"period": ts[:7], "started": 0,
                                        "completed": 0, "events": 0})
        b["events"] += 1
        ev = r.get("event")
        if ev == "sprint_started":
            b["started"] += 1
        elif ev == "sprint_complete":
            b["completed"] += 1
    return [buckets[k] for k in sorted(buckets)]


def _anonymize_feedback(records: list[dict],
                        include_detail: bool = False) -> list[dict[str, Any]]:
    """Carry filed findings to the HQ spine (C3). Feedback is DELIBERATELY-AUTHORED,
    opt-in content — the adopter filed it AND chose to export — so unlike the
    aggregate metrics it carries the authored `title` (the finding's one-line
    summary, about prusik friction by construction) plus the metadata the spine
    needs (id/content_hash/kind/severity/status/ts/version). It still DROPS the two
    highest-leak fields: `feature` (product intent — same rule as journeys) and
    `detail` (verbatim tool output → paths/secrets stay LOCAL); `has_detail` tells
    HQ that repro exists for an internal pull / deliberate share.

    `include_detail` is the TRUST opt-in (`feedback_include_detail`, default off):
    a design partner who has decided to trust HQ with their verbatim repro carries
    `detail` too — the fix substrate without a round-trip. `feature` stays dropped
    regardless (product intent is never needed to fix a prusik finding)."""
    out: list[dict[str, Any]] = []
    for r in records:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        item: dict[str, Any] = {
            "id": r.get("id"),
            "content_hash": r.get("content_hash"),
            "kind": r.get("kind"),
            "severity": r.get("severity"),
            "title": r.get("title", ""),
            "status": r.get("status", "open"),
            "phase": r.get("phase"),               # prusik's own phase names — safe
            "prusik_version": r.get("prusik_version"),
            "ts": r.get("ts"),
            "has_detail": bool(r.get("detail")),   # detail itself stays local…
        }
        if include_detail and r.get("detail"):
            item["detail"] = r["detail"]           # …unless the partner opted in
        out.append(item)
    return out


def _include_detail(root: Path) -> bool:
    """The adopter's trust opt-in: `feedback_include_detail: true` in sprint-config
    carries verbatim repro to HQ. Default OFF — detail stays local unless a trusted
    design partner deliberately turns it on."""
    try:
        from prusik import phases
        return bool((phases.load_sprint_config(root) or {}).get(
            "feedback_include_detail"))
    except Exception:  # noqa: BLE001 — config read must never break export
        return False


def _ticket_states(root: Path) -> list[dict[str, Any]]:
    """Export-safe per-finding ticket statuses (best-effort — never break export)."""
    try:
        from prusik import feedback_store
        return [feedback_store.ticket_status(t) for t in feedback_store.load_all(root)]
    except Exception:  # noqa: BLE001
        return []


def export_payload(records: list[dict], product: str, root: Path) -> dict[str, Any]:
    """Build the ANONYMIZED, portable artifact an adopter may opt to share.

    Composition rule: the same aggregate metrics as build(), MINUS every
    identifying field. Crucially it drops `open_features` (feature NAMES could
    leak product intent) and keeps only the count; it carries no file paths, no
    raw events — only counts, prusik's own gate/phase names, durations, and
    (C3) filed findings minus their `feature`+`detail`. `as_of`/`window` derive
    from ledger timestamps (data, not wall-clock) so the export is deterministic.
    """
    from prusik import feedback as _feedback
    findings = _anonymize_feedback(_feedback.load_all(root),
                                   include_detail=_include_detail(root))
    r = build(records)
    ts = sorted(rec["ts"] for rec in records if isinstance(rec.get("ts"), str))
    trust = {
        g: {"fired": s["fired"], "true_catch": s[cq.TRUE_CATCH],
            "false_block": s[cq.FALSE_BLOCK], "precision": s.get("precision")}
        for g, s in r["trust"].items()
    }
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "prusik_version": __version__,
        "product": product or "unnamed-product",
        "product_hash": _product_hash(root),
        "as_of": ts[-1] if ts else None,
        "window": {"first_event": ts[0] if ts else None,
                   "last_event": ts[-1] if ts else None},
        "metrics": {
            "events": r["events"],
            "journeys": r["journeys"],
            "completed": r["completed"],
            "completion_pct": r["completion_pct"],
            "open_feature_count": len(r["open_features"]),   # COUNT, not names
            "total_wall_sec": r["total_wall_sec"],
            "convergence_stalls": r["convergence_stalls"],
            "loop_fueled": r["loop_fueled"],
            "trust": trust,            # gate names are prusik-internal — safe
            "phases": r["phases"],     # phase names are prusik-internal — safe
            "feedback_count": len(findings),
        },
        "feedback": findings,          # C3 — filed findings (feature+detail dropped)
        # Feedback-pipeline operational health (NOT a telemetry metric — kept out of
        # the `metrics` block so it flows through hq.feedback_spine/eng-insights, not
        # hq.fleet). Bloat watch: on-disk size of the local append-only store.
        "feedback_store_bytes": _feedback.store_bytes(root),
        # Per-finding TICKET states (status + metadata only, no thread/detail) so HQ
        # can reconcile adopter-side vs prusik-side truth by id across repos (cross-repo
        # identity; designed with live-cc, fb-c91c2be85603).
        "tickets": _ticket_states(root),
        "timeseries": timeseries(records),   # A1 — per-month activity for trends
    }


def export(records: list[dict], product: str, out: str | None, to_stdout: bool) -> int:
    """Opt-in export. Writes the anonymized artifact to disk (or stdout) — prusik
    NEVER transmits it; sharing with HQ is a deliberate act the operator takes
    afterward with the file."""
    root = ledger.project_root()
    payload = export_payload(records, product, root)
    text = json.dumps(payload, indent=2, default=str)
    if to_stdout:
        print(text)
        return 0
    out_path = root / (out or DEFAULT_EXPORT_PATH)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text + "\n")
    rel = out_path.relative_to(root) if out_path.is_relative_to(root) else out_path
    print(f"[prusik-report] wrote anonymized export → {rel}")
    print(f"  product_hash {payload['product_hash']} · "
          f"{payload['metrics']['journeys']} journeys · "
          f"{payload['metrics']['events']} events · schema "
          f"{payload['schema_version']}")
    print("  Aggregate metrics ONLY — no feature names, file paths, or source. "
          "prusik did not transmit this.")
    print("  Review it, then share with HQ if you choose.")
    return 0


def _prec(trust: dict, gate: str) -> str:
    s = trust.get(gate)
    if not s or s.get("precision") is None:
        return "—"
    return f"{100 * s['precision']:.0f}%"


def run(json_output: bool = False, export_artifact: bool = False,
        product: str | None = None, out: str | None = None,
        to_stdout: bool = False) -> int:
    records = ledger.read_all()
    if not records:
        msg = ("nothing to export yet." if export_artifact
               else "no product activity yet.")
        print(f"[prusik-report] ledger is empty — {msg}")
        return 0
    if export_artifact:
        return export(records, product or "", out, to_stdout)
    r = build(records)
    if json_output:
        print(json.dumps(r, indent=2, default=str))
        return 0

    print(f"prusik report — {r['journeys']} journeys, {r['events']} ledger events\n")

    # TRUST
    print("TRUST — are the gates catching real defects?")
    if r["trust"]:
        print(f"  {'gate':18s} {'fired':>5s} {'true':>5s} {'false':>5s}  precision")
        for g in sorted(r["trust"], key=lambda k: -r["trust"][k]["fired"]):
            s = r["trust"][g]
            print(f"  {g:18s} {s['fired']:5d} {s[cq.TRUE_CATCH]:5d} "
                  f"{s[cq.FALSE_BLOCK]:5d}  {_prec(r['trust'], g):>8s}")
    else:
        print("  (no gate/critic fires yet)")
    print("  → `prusik inject` proves the deterministic gates catch defects "
          "on this config.")

    # VALUE
    print("\nVALUE — cost & convergence")
    print(f"  {r['completed']}/{r['journeys']} features completed "
          f"({r['completion_pct']}%) · {effort.fmt_duration(r['total_wall_sec'])} "
          f"total wall-clock")
    if r["phases"]:
        top = sorted(r["phases"], key=lambda k: -r["phases"][k]["total_sec"])[:3]
        where = " · ".join(
            f"{p} {effort.fmt_duration(r['phases'][p]['total_sec'])}" for p in top)
        print(f"  where the effort goes: {where}")
    print(f"  health: {r['convergence_stalls']} convergence-stall(s), "
          f"{len(r['open_features'])} open feature(s)")

    # IMPROVEMENT
    print("\nIMPROVEMENT (MOAT) — self-learning loop is OPEN (advisory)")
    print(f"  fueled: {r['loop_fueled']}  ·  label open catches with `prusik catch` "
          f"to sharpen the per-gate ratio.")

    # value-chain one-liner
    print("\n── value chain ──")
    print(f"  VALUE {r['completion_pct']}% done · "
          f"TRUST evidence {_prec(r['trust'], 'evidence_gate')} / "
          f"critic {_prec(r['trust'], 'critic')} · "
          f"LEVERAGE {len(r['open_features'])} open · "
          f"MOAT loop open")
    print("\nDerived from the append-only ledger (zero hot-path). Per-product only "
          "— prusik never phones home. Share fleet metrics on YOUR terms with "
          "`prusik report --export` (anonymized; you send it, not us).")
    return 0
