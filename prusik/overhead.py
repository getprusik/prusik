"""`prusik overhead` — the COST half of the overhead-to-catch ratio.

`catches` answers "are the gates catching real defects?" (value); this answers
"what does the harness itself cost?" — active time per phase with idle gaps
split out honestly, each gate's block→retry cost, fix-round loops, and a
directly measured hook latency (`--hook-bench`). A slow gate gets NAMED and
becomes a tuning decision instead of the whole harness getting disabled on
feel (moat-finding:fb-e7fe8177cc8c).

Span math is delegated to `effort.extract_spans` — one source of truth for
phase timing. Reading is deliberately NOT `ledger.read_all()`: field ledgers
must be analyzable even with malformed/unknown lines (backtest criterion), so
this module reads tolerantly and SURFACES the skipped count — a corrupted
ledger never silently understates cost. Read-only throughout: measuring
overhead must not add overhead or events.
"""

from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from prusik import effort, ledger

_GATE_EVENTS = ("gate_blocked", "advance_blocked")
_BENCH_TIMEOUT_SEC = 60


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def read_events_text(text: str) -> tuple[list[dict], int]:
    """Tolerant ledger parse: (events, skipped_line_count). Unknown event
    TYPES are kept (analyze skips what it doesn't understand); only lines
    that aren't valid JSON objects are skipped — and counted, never hidden."""
    events: list[dict] = []
    skipped = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if isinstance(r, dict):
            events.append(r)
        else:
            skipped += 1
    return events, skipped


def analyze(events: list[dict], idle_min: int = 30,
            skipped_lines: int = 0) -> dict:
    """Pure cost analysis over parsed ledger events.

    Idle honesty: within each phase span, silence between consecutive ledger
    events longer than `idle_min` minutes accrues to `idle_seconds` (human
    away), not to the phase's active `seconds` — elapsed totals still include
    it, so nothing is hidden either way."""
    ordered = sorted(events, key=lambda r: r.get("ts", ""))
    threshold = idle_min * 60
    all_ts = [t for t in (_parse_ts(r.get("ts")) for r in ordered) if t]

    phases: dict[str, dict] = {}
    for s in effort.extract_spans(ordered):
        if s["seconds"] is None:
            continue
        a, b = _parse_ts(s["start"]), _parse_ts(s["end"])
        idle = 0.0
        if a and b:
            points = sorted({a, b, *(t for t in all_ts if a <= t <= b)})
            for x, y in zip(points, points[1:]):
                gap = (y - x).total_seconds()
                if gap > threshold:
                    idle += gap
        d = phases.setdefault(s["phase"], {"phase": s["phase"], "seconds": 0.0,
                                           "idle_seconds": 0.0, "spans": 0})
        d["seconds"] += s["seconds"] - idle
        d["idle_seconds"] += idle
        d["spans"] += 1

    gates: dict[str, dict] = {}
    for i, r in enumerate(ordered):
        ev = r.get("event")
        if ev not in _GATE_EVENTS:
            continue
        if ev == "advance_blocked":
            key = f"advance_blocked: {r.get('to_phase') or '?'}"
        else:
            key = f"gate_blocked: {r.get('reason') or r.get('tool') or '?'}"
        t0 = _parse_ts(r.get("ts"))
        retry = 0.0
        for nxt in ordered[i + 1:]:
            if nxt.get("event") in _GATE_EVENTS:
                continue                      # another block is not progress
            t1 = _parse_ts(nxt.get("ts"))
            if t0 and t1:
                # A recovery gap beyond the idle threshold means the session
                # ended/paused after the block — that's idle, not an
                # enforcement loop; cap so one abandoned block can't read as
                # hours of gate cost (seen on a real field ledger: 167h).
                retry = min((t1 - t0).total_seconds(), float(threshold))
            break
        g = gates.setdefault(key, {"gate": key, "blocks": 0,
                                   "retry_seconds": 0.0})
        g["blocks"] += 1
        g["retry_seconds"] += retry

    rounds, fix_sec = 0, 0.0
    pending: datetime | None = None
    for r in ordered:
        if r.get("event") == "fix_round_start":
            rounds += 1
            pending = _parse_ts(r.get("ts"))
        elif r.get("event") == "fix_round_end" and pending:
            t1 = _parse_ts(r.get("ts"))
            if t1:
                fix_sec += (t1 - pending).total_seconds()
            pending = None

    elapsed = ((all_ts[-1] - all_ts[0]).total_seconds()
               if len(all_ts) >= 2 else 0.0)
    return {
        "phases": sorted(phases.values(), key=lambda p: -p["seconds"]),
        "gates": sorted(gates.values(), key=lambda g: -g["retry_seconds"]),
        "fix_rounds": {"rounds": rounds, "seconds": fix_sec},
        "totals": {
            "elapsed_seconds": elapsed,
            "gate_overhead_seconds": sum(g["retry_seconds"]
                                         for g in gates.values()),
            "idle_seconds": sum(p["idle_seconds"] for p in phases.values()),
        },
        "skipped_lines": skipped_lines,
    }


def hook_bench(n: int = 20) -> dict:
    """Measure the real PreToolUse hook path: spawn `python -m prusik gate
    pre-tool` with a benign Read payload N times (the same entry every tool
    call pays), wall-clock each. Read tools never append ledger events, so
    the bench is read-only by construction."""
    payload = json.dumps({"tool_name": "Read",
                          "tool_input": {"file_path": "/dev/null"}})
    times_ms: list[float] = []
    for _ in range(max(1, n)):
        t0 = time.perf_counter()
        subprocess.run([sys.executable, "-m", "prusik", "gate", "pre-tool"],
                       input=payload, capture_output=True, text=True,
                       timeout=_BENCH_TIMEOUT_SEC, check=False)
        times_ms.append((time.perf_counter() - t0) * 1000)
    times_ms.sort()
    p90 = times_ms[min(len(times_ms) - 1, round(0.9 * (len(times_ms) - 1)))]
    return {"median_ms": round(statistics.median(times_ms), 1),
            "p90_ms": round(p90, 1), "n": len(times_ms)}


def _render(a: dict) -> str:
    fmt = effort.fmt_duration
    out = ["Overhead — where the harness's time actually went\n"]
    out.append(f"  {'phase':14s} {'active':>8s} {'idle':>8s} {'spans':>6s}")
    for p in a["phases"]:
        out.append(f"  {p['phase']:14s} {fmt(p['seconds']):>8s} "
                   f"{fmt(p['idle_seconds']) if p['idle_seconds'] else '—':>8s} "
                   f"{p['spans']:6d}")
    if a["gates"]:
        out.append("\n  gate blocks → retry cost (the enforcement loop):")
        for g in a["gates"]:
            out.append(f"    {g['gate'][:56]:56s} {g['blocks']:3d}× "
                       f"{fmt(g['retry_seconds']):>7s}")
    fr = a["fix_rounds"]
    if fr["rounds"]:
        out.append(f"\n  fix-rounds: {fr['rounds']} costing "
                   f"{fmt(fr['seconds'])}")
    t = a["totals"]
    out.append(f"\n  totals: elapsed {fmt(t['elapsed_seconds'])} · "
               f"gate overhead {fmt(t['gate_overhead_seconds'])} · "
               f"idle {fmt(t['idle_seconds'])}")
    if a.get("hook_bench"):
        hb = a["hook_bench"]
        out.append(f"  hook latency: median {hb['median_ms']}ms · "
                   f"p90 {hb['p90_ms']}ms (n={hb['n']}) — paid on EVERY "
                   f"tool call")
    if a["skipped_lines"]:
        out.append(f"\n  ⚠ {a['skipped_lines']} unparseable ledger line(s) "
                   f"skipped — cost may be understated.")
    out.append("\nIdle = silence between events longer than the idle "
               "threshold (human away), split out so it never reads as "
               "harness cost. Pair with `prusik catches` (the value side).")
    return "\n".join(out)


def run(json_output: bool = False, hook_bench_flag: bool = False,
        bench_n: int = 20, ledger_path: str | None = None,
        idle_min: int = 30) -> int:
    path = Path(ledger_path) if ledger_path else ledger.ledger_path()
    if not path.exists():
        msg = (f"nothing to measure — no ledger at {path}. An absent ledger "
               f"is unknown overhead, not zero overhead.")
        if json_output:
            print(json.dumps({"error": msg}))
        else:
            print(f"[prusik-overhead] {msg}")
        return 1
    events, skipped = read_events_text(path.read_text())
    analysis = analyze(events, idle_min=idle_min, skipped_lines=skipped)
    if hook_bench_flag:
        analysis["hook_bench"] = hook_bench(n=bench_n)
    if json_output:
        print(json.dumps(analysis, indent=2))
    else:
        print(_render(analysis))
    return 0
