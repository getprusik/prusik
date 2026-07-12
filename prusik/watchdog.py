"""Watchdog: polls heartbeats + phase duration, files incidents.

Designed to run out-of-band (via Claude Code `/schedule`, cron, or a terminal
loop). Does not interrupt an active session — just records state.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from prusik import phases
from prusik.ledger import project_root, append


def _now_ts() -> str:
    return datetime.now(timezone.utc).isoformat().replace(":", "-")


def _mtime_age_min(path: Path) -> float:
    import os as _os
    return (time.time() - _os.path.getmtime(path)) / 60.0


def _write_incident(kind: str, payload: dict, root: Path) -> Path:
    incidents_dir = root / ".sprint" / "incidents"
    incidents_dir.mkdir(parents=True, exist_ok=True)
    f = incidents_dir / f"{_now_ts()}-{kind}.json"
    f.write_text(json.dumps({"ts": datetime.now(timezone.utc).isoformat(),
                             "kind": kind, **payload}, indent=2))
    append("watchdog_incident", kind=kind, incident_file=str(f.relative_to(root)), **payload)
    return f


def check(root: Path | None = None) -> int:
    root = root or project_root()
    config = phases.load_sprint_config(root)
    if not config:
        print("[watchdog] no sprint-config.yaml; nothing to check")
        return 0
    state = phases.current_sprint_state(root)
    if not state:
        print("[watchdog] no active sprint")
        return 0

    feature = state.get("feature")
    phase = state.get("phase")
    incidents: list[str] = []

    heartbeat_stale_min = (config.get("watchdog") or {}).get("heartbeat_stale_min", 30)
    status_dir = root / ".sprint" / "status"
    if status_dir.exists():
        for f in status_dir.glob("*.txt"):
            age = _mtime_age_min(f)
            if age > heartbeat_stale_min:
                p = _write_incident("stale_heartbeat", {
                    "teammate": f.stem, "age_min": round(age, 1),
                    "threshold_min": heartbeat_stale_min,
                    "phase": phase, "feature": feature,
                }, root)
                incidents.append(str(p.relative_to(root)))

    state_file = root / ".sprint" / "state.json"
    if state_file.exists():
        age_h = _mtime_age_min(state_file) / 60.0
        max_phase_hours = (config.get("watchdog") or {}).get("max_phase_hours", 24)
        if age_h > max_phase_hours:
            p = _write_incident("phase_stalled", {
                "phase": phase, "feature": feature,
                "age_hours": round(age_h, 1),
                "threshold_hours": max_phase_hours,
            }, root)
            incidents.append(str(p.relative_to(root)))

    phase_spec = phases.get_phase_spec(config, phase) or {}
    budget = phase_spec.get("budget_tokens")
    if budget:
        tokens_used = _estimate_phase_tokens(root, phase, feature)
        if tokens_used and tokens_used > budget:
            p = _write_incident("budget_exceeded", {
                "phase": phase, "feature": feature,
                "tokens_used": tokens_used, "budget": budget,
            }, root)
            incidents.append(str(p.relative_to(root)))

    if not incidents:
        print(f"[watchdog] all clear. phase={phase} feature={feature}")
    else:
        print(f"[watchdog] {len(incidents)} incident(s) filed:")
        for inc in incidents:
            print(f"  - {inc}")
    return 0 if not incidents else 1


def _estimate_phase_tokens(root: Path, phase: str | None, feature: str | None) -> int | None:
    """Sum any 'tokens' field on ledger events since the current phase began."""
    from prusik.ledger import read_all
    records = read_all()
    phase_start_ts = None
    for r in records:
        if r.get("event") == "phase_advance" and r.get("to_phase") == phase:
            phase_start_ts = r["ts"]
    if phase_start_ts is None:
        return None
    total = 0
    for r in records:
        if r["ts"] < phase_start_ts:
            continue
        if "tokens" in r:
            total += int(r["tokens"])
    return total or None


def poll(interval_min: float = 15.0, root: Path | None = None) -> int:
    """Run check() in a loop. Intended for a terminal session; Ctrl-C to stop."""
    while True:
        try:
            check(root)
        except Exception as e:
            print(f"[watchdog] error: {e}")
        time.sleep(interval_min * 60)
