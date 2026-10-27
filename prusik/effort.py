"""Effort-telemetry lens — where does the journey spend time and effort?

The VALUE half of the instrument layer. `catch_quality.py` answers "are the
guardrails catching real defects?" (TRUST); this answers "what does the journey
COST?" (VALUE) — time per phase, rewind-churn, and friction counts per feature.

Like catch-quality, everything here is DERIVED from the append-only ledger at
read-time — **zero hot-path cost**. The ledger already stamps every event with a
UTC `ts` and tags phase transitions with `from_phase`/`to_phase`/`feature`;
phase durations, churn, and friction fall straight out of that timeline. Nothing
new is emitted on the gate hot path — measuring effort must not itself add effort.

This is the lens that *licenses* friction-removers: a tool (e.g. a context graph)
earns its build only when this lens shows the step it targets is a dominant cost.
Because it is pure derivation, it is low-regret — re-derive it differently later
and the raw ledger is unchanged.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from prusik import ledger

# A sprint enters `scoping` at `sprint_started` (gate.py prints "Phase: scoping"),
# so the first span begins there — there is no separate advance into scoping.
_START_PHASE = "scoping"


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def extract_spans(records: list[dict]) -> list[dict]:
    """Derive per-phase time spans from the event timeline.

    Walks events in `ts` order, tracking each feature's current phase and the
    ts it was entered. A span closes on every `phase_advance` / `phase_rewind`
    (labelled with the ledger's `from_phase` — the source of truth) and on
    `sprint_complete`. Returns one dict per closed span:
    `{feature, phase, start, end, seconds, via}` with via ∈ {advance, rewind,
    complete, start} (`start`: the `briefing` span a `sprint_started` closes).
    A span whose endpoints don't parse keeps `seconds=None` (it is still listed,
    just untimed).
    """
    return _walk(records)[0]


def phase_windows(records: list[dict], feature: Any) -> list[dict]:
    """A feature's phase windows `{phase, start, end}` in order — the closed spans plus the
    still-open current phase (`end=None`). The same walk as `extract_spans`, so time and
    token attribution (prusik.usage) share one timeline."""
    spans, open_span = _walk(records)
    out = [{"phase": s["phase"], "start": s["start"], "end": s["end"]}
           for s in spans if s["feature"] == feature]
    if feature in open_span:
        phase, start = open_span[feature]
        out.append({"phase": phase, "start": start, "end": None})
    return out


def _walk(records: list[dict]) -> tuple[list[dict], dict[Any, tuple[str, str]]]:
    """The one timeline walk: (closed spans, still-open {feature: (phase, entry_ts)}).

    A feature's latest `briefing_started` (fb-1e3ecda9d011) is held PENDING, not open: its
    `sprint_started` consumes it into a closed `briefing` span (via="start"), clipped to start
    no earlier than the latest closed-span end of any feature so far — so a briefing never
    overlaps a completed sprint's window and no token is counted twice. A still-open span
    never clips, and a `briefing_started` with no later `sprint_started` yields nothing."""
    ordered = sorted(records, key=lambda r: r.get("ts", ""))
    open_span: dict[Any, tuple[str, str]] = {}   # feature -> (phase, entry_ts)
    spans: list[dict] = []
    pending: dict[Any, str] = {}                 # feature -> latest briefing_started ts
    last_closed: tuple[datetime, str] | None = None   # (parsed end, end string)

    def note_closed(end_ts: str) -> None:
        nonlocal last_closed
        e = _parse_ts(end_ts)
        if e is None:
            return
        if last_closed is None or e > last_closed[0]:
            last_closed = (e, end_ts)

    def close(feature: Any, end_ts: str, via: str) -> None:
        cur = open_span.pop(feature, None)
        if cur is None:
            return
        phase, start_ts = cur
        a, b = _parse_ts(start_ts), _parse_ts(end_ts)
        seconds = (b - a).total_seconds() if a and b else None
        spans.append({"feature": feature, "phase": phase, "start": start_ts,
                      "end": end_ts, "seconds": seconds, "via": via})
        note_closed(end_ts)

    for r in ordered:
        ev = r.get("event")
        feat = r.get("feature")
        ts = r.get("ts", "")
        if ev == "briefing_started":
            pending[feat] = ts                   # the latest one wins
        elif ev == "sprint_started":
            b = pending.pop(feat, None)          # consume (SC-9): a re-start opens none
            bd, td = _parse_ts(b), _parse_ts(ts)
            if b is not None and bd is not None and td is not None:
                if last_closed is not None and last_closed[0] > bd:
                    start_dt, start_s = last_closed
                else:
                    start_dt, start_s = bd, b
                if start_dt < td:
                    spans.append({"feature": feat, "phase": "briefing", "start": start_s,
                                  "end": ts, "seconds": (td - start_dt).total_seconds(),
                                  "via": "start"})
                    note_closed(ts)
            open_span[feat] = (_START_PHASE, ts)
        elif ev in ("phase_advance", "phase_rewind"):
            via = "rewind" if ev == "phase_rewind" else "advance"
            if feat in open_span:
                _, entry = open_span[feat]
                from_phase = r.get("from_phase") or open_span[feat][0]
                open_span[feat] = (from_phase, entry)
                close(feat, ts, via)
            # open the next phase (even with no prior span, so the NEXT is timed)
            open_span[feat] = (r.get("to_phase") or "?", ts)
        elif ev == "sprint_complete":
            close(feat, ts, "complete")
    return spans, open_span


def summarize_features(records: list[dict]) -> dict[Any, dict[str, Any]]:
    """Per-feature journey cost: wall-clock, time-per-phase, churn, friction."""
    ordered = sorted(records, key=lambda r: r.get("ts", ""))
    feats: dict[Any, dict[str, Any]] = {}

    def f(feat: Any) -> dict[str, Any]:
        return feats.setdefault(feat, {
            "phases": defaultdict(float), "wall_clock_sec": None,
            "started": None, "ended": None, "rewinds": 0, "blocks": 0,
            "fix_rounds": 0, "complete": False, "tokens": None,
            "recorded_duration_min": None,
        })

    last_ts: dict[Any, str] = {}
    for r in ordered:
        feat = r.get("feature")
        if not feat:
            continue
        ev = r.get("event")
        ts = r.get("ts", "")
        last_ts[feat] = ts
        if ev == "sprint_started":
            d = f(feat)
            if d["started"] is None:
                d["started"] = ts
        elif ev == "phase_rewind":
            f(feat)["rewinds"] += 1
        elif ev == "gate_blocked":
            f(feat)["blocks"] += 1
        elif ev == "fix_round_start":
            f(feat)["fix_rounds"] += 1
        elif ev == "sprint_complete":
            d = f(feat)
            d["complete"] = True
            d["ended"] = ts
            actual = r.get("actual") or {}
            d["tokens"] = actual.get("tokens")
            d["recorded_duration_min"] = actual.get("duration_min")

    for s in extract_spans(ordered):
        if s["seconds"] is not None and s["feature"] in feats:
            feats[s["feature"]]["phases"][s["phase"]] += s["seconds"]

    for feat, d in feats.items():
        end = d["ended"] or last_ts.get(feat)
        a, b = _parse_ts(d["started"]), _parse_ts(end)
        d["wall_clock_sec"] = (b - a).total_seconds() if a and b else None
        d["phases"] = dict(d["phases"])
    return feats


def summarize_phases(records: list[dict]) -> dict[str, dict[str, Any]]:
    """Across all features: total time, visits, mean per phase — where the
    journey's effort actually goes (the friction-remover license)."""
    agg: dict[str, dict[str, Any]] = {}
    for s in extract_spans(records):
        if s["seconds"] is None:
            continue
        a = agg.setdefault(s["phase"], {"total_sec": 0.0, "visits": 0})
        a["total_sec"] += s["seconds"]
        a["visits"] += 1
    for a in agg.values():
        a["mean_sec"] = a["total_sec"] / a["visits"] if a["visits"] else None
    return agg


def fmt_tokens(n: int | None) -> str:
    """Human-readable token count: '850' / '12k' / '3.4M'. Public — composed by gate's
    advance usage line and `run()` below."""
    if n is None:
        return "—"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    return f"{n / 1000:.0f}k" if n >= 1000 else str(n)


def fmt_duration(sec: float | None) -> str:
    """Human-readable duration: '45s' / '12m' / '1.5h'. Public — composed by
    the showcase lens (instrument layer)."""
    if sec is None:
        return "—"
    if sec < 90:
        return f"{sec:.0f}s"
    m = sec / 60
    return f"{m:.0f}m" if m < 90 else f"{m / 60:.1f}h"


def run(json_output: bool = False) -> int:
    records = ledger.read_all()
    feats = summarize_features(records)
    phases = summarize_phases(records)
    from prusik import usage   # function-local: usage imports effort lazily (acyclic)
    phase_tokens = usage.rollup(records)["by_phase"]

    if json_output:
        print(json.dumps({"features": feats, "by_phase": phases,
                          "tokens_by_phase": phase_tokens}, indent=2, default=str))
        return 0

    if not feats:
        print("[prusik-effort] no sprint journeys in the ledger yet.")
        return 0

    print(f"Effort-telemetry — {len(feats)} journeys\n")
    print(f"  {'feature':24s} {'wall':>6s} {'tokens':>7s} {'rwnd':>5s} {'blk':>4s} "
          f"{'fix':>4s}  status")
    for feat in sorted(feats, key=lambda k: -(feats[k]["wall_clock_sec"] or 0)):
        d = feats[feat]
        status = "done" if d["complete"] else "open"
        print(f"  {str(feat)[:24]:24s} {fmt_duration(d['wall_clock_sec']):>6s} "
              f"{fmt_tokens(d['tokens']):>7s} "
              f"{d['rewinds']:5d} {d['blocks']:4d} {d['fix_rounds']:4d}  {status}")

    if phases:
        print("\n  where the effort goes (per phase, across all journeys):")
        print(f"    {'phase':14s} {'total':>7s} {'visits':>7s} {'mean':>7s} {'tokens':>7s}")
        for ph in sorted(phases, key=lambda k: -phases[k]["total_sec"]):
            a = phases[ph]
            print(f"    {ph:14s} {fmt_duration(a['total_sec']):>7s} "
                  f"{a['visits']:7d} {fmt_duration(a['mean_sec']):>7s} "
                  f"{fmt_tokens(phase_tokens.get(ph)):>7s}")

    print("\nDerived from the append-only ledger (zero hot-path cost). "
          "rwnd=rewinds, blk=writable-gate blocks, fix=fix-rounds. "
          "tokens = measured from session transcripts at sprint-complete (input + output + "
          "cache writes + cache reads); set a phase's budget_tokens from these. "
          "Wall-clock includes any pause time.")
    return 0
