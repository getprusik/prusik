"""Repeat-bounce report — the REMEDY-quality lens, the value-side companion to
`overhead`'s cost lens.

A gate block is a teaching moment: it stops the agent and names a fix. If the
agent re-hits the SAME gate class within the SAME sprint, the remedy didn't
land — the agent bounced off the fence twice. That re-bounce is pure waste (an
extra fix-round with a known cause), and it's a signal ON THE REMEDY, exactly as
`catch_quality` is a signal on the gate. This report makes it measurable:
per gate class, how often it fired and how much of that was wasted re-bounce —
so the worst remedy gets rewritten FIRST and the rewrite's effect is provable
(re-run, watch the rate drop), never asserted.

Read-only over the append-only ledger, so it works retrospectively on the whole
history (no new sprint required) and the same shape feeds the ceremony question:
a class that re-bounces heavily on small work is a proportional-rigor smell.

Classes come from `gate_class.classify` (stable emitted key, else derived from
history) — the report never normalizes free-text reasons, which is what gets
reworded when a remedy improves.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from prusik import gate_class, ledger, overhead


def analyze(events: list[dict]) -> dict:
    """Repeat-bounce stats from a ledger event list — pure, so it's testable
    without a file. A repeat-bounce = the (sprint, gate-class) pair firing ≥2×;
    the wasted count is every fire beyond the first (the first block is the
    legitimate catch, the rest are the remedy failing to land)."""
    pair: Counter = Counter()          # (feature, class) -> fires
    for e in events:
        if e.get("event") not in gate_class.BLOCK_EVENTS:
            continue
        feature = e.get("feature") or "?"
        pair[(feature, gate_class.classify(e))] += 1

    total = sum(pair.values())
    n_pairs = len(pair)
    repeat_pairs = sum(1 for c in pair.values() if c >= 2)
    wasted = sum(c - 1 for c in pair.values() if c >= 2)

    by_class: dict[str, dict] = {}
    for (feature, cls), c in pair.items():
        d = by_class.setdefault(cls, {"fires": 0, "rebounces": 0, "sprints": 0,
                                      "bounced_sprints": 0})
        d["fires"] += c
        d["sprints"] += 1
        if c >= 2:
            d["rebounces"] += c - 1
            d["bounced_sprints"] += 1

    classes = [
        {"gate_class": cls, **d,
         "rebounce_rate": round(d["rebounces"] / d["fires"], 3) if d["fires"] else 0.0}
        for cls, d in by_class.items()
    ]
    # Worst remedy first: most wasted re-bounces, then most fires.
    classes.sort(key=lambda r: (-r["rebounces"], -r["fires"]))

    return {
        "total_block_events": total,
        "sprint_gate_pairs": n_pairs,
        "repeat_bounce_pairs": repeat_pairs,
        "repeat_bounce_pair_rate": round(repeat_pairs / n_pairs, 3) if n_pairs else 0.0,
        "wasted_rebounces": wasted,
        "wasted_rebounce_rate": round(wasted / total, 3) if total else 0.0,
        "by_class": classes,
    }


def _render(a: dict) -> str:
    if not a["total_block_events"]:
        return ("[prusik-bounce] no block events in this ledger — nothing to "
                "measure (an agent that never hit a gate, or an empty ledger).")
    lines = [
        "[prusik-bounce] remedy-quality: repeat-bounces (same gate class re-hit "
        "in one sprint = remedy didn't land)",
        f"  {a['total_block_events']} block events over {a['sprint_gate_pairs']} "
        f"(sprint, gate-class) pairs",
        f"  {a['repeat_bounce_pairs']} pairs re-bounced "
        f"({a['repeat_bounce_pair_rate']:.0%} of pairs)",
        f"  {a['wasted_rebounces']} wasted re-bounces "
        f"({a['wasted_rebounce_rate']:.0%} of all block events)",
        "",
        "  gate class                     rebounces / fires   rate   sprints  (remedy-rewrite priority ↓)",
    ]
    for r in a["by_class"]:
        lines.append(
            f"  {r['gate_class']:<28} {r['rebounces']:>6} / {r['fires']:<6} "
            f"{r['rebounce_rate']:>5.0%}   {r['bounced_sprints']}/{r['sprints']}")
    if any(r["gate_class"] == gate_class.UNCLASSIFIED for r in a["by_class"]):
        lines.append("")
        lines.append("  note: `unclassified` = legacy block events that recorded no "
                     "identifying field; new events self-label at the gate.")
    return "\n".join(lines)


def run(json_output: bool = False, ledger_path: str | None = None) -> int:
    path = Path(ledger_path) if ledger_path else ledger.ledger_path()
    if not path.exists():
        msg = (f"no ledger at {path}. An absent ledger is unknown remedy "
               f"quality, not a clean one.")
        print(json.dumps({"error": msg}) if json_output else f"[prusik-bounce] {msg}")
        return 1
    events, _ = overhead.read_events_text(path.read_text())
    a = analyze(events)
    print(json.dumps(a, indent=2) if json_output else _render(a))
    return 0
