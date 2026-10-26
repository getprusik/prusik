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
        kind, payload = _budget_check(root, feature, phase, budget)
        # Deduped like criteria_already_met: an open incident for this feature + phase
        # stands until the operator resolves (removes) it — no re-filing every poll.
        if kind and not _incident_open(root, kind, feature=feature, phase=phase):
            p = _write_incident(kind, {"phase": phase, "feature": feature, **payload}, root)
            incidents.append(str(p.relative_to(root)))

    from prusik import push_guard
    pp = push_guard.watchdog_check(root, phase)
    if pp:
        kind, payload = pp
        p = _write_incident(kind, {**payload, "phase": phase,
                                   "feature": feature}, root)
        incidents.append(str(p.relative_to(root)))

    # Phase-entry reality (fb-8637c2416504): every prove_red criterion green
    # on base at an early phase = the goal may already be achieved outside
    # this sprint. Deduped: an existing incident for this feature stands
    # until the operator resolves (removes) it — no hourly re-spam.
    from prusik import ground_truth
    gp = ground_truth.base_probe(feature, root, phase)
    if gp:
        if not _incident_open(root, "criteria_already_met", feature=feature):
            p = _write_incident("criteria_already_met",
                                {**gp, "phase": phase, "feature": feature},
                                root)
            incidents.append(str(p.relative_to(root)))

    if not incidents:
        print(f"[watchdog] all clear. phase={phase} feature={feature}")
    else:
        print(f"[watchdog] {len(incidents)} incident(s) filed:")
        for inc in incidents:
            print(f"  - {inc}")
    return 0 if not incidents else 1


def _incident_open(root: Path, kind: str, **match) -> bool:
    """Is an incident of `kind` whose fields equal `match` still on disk (unresolved)?"""
    inc_dir = root / ".sprint" / "incidents"
    if not inc_dir.is_dir():
        return False
    for f in inc_dir.glob(f"*-{kind}.json"):
        try:
            rec = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if all(rec.get(k) == v for k, v in match.items()):
            return True
    return False


def _budget_check(root: Path, feature: str | None, phase: str | None,
                  budget: int) -> tuple[str | None, dict]:
    """Measured phase usage against its budget (fb-a1b7db140a5e): (incident kind, payload),
    or (None, {}) when within budget. A budget that can't be measured is LOUD — never a
    silent pass."""
    from prusik import ledger, usage
    # usage reads the ledger + session registry of project_root(); an explicit foreign root
    # would compare one project's budget against another's usage (fb-5b83acbaab4a). Paths
    # are compared resolved; a linked-worktree root counts as foreign (loud, never wrong),
    # since canonicalizing it is ledger's private concern (DEV-005).
    if Path(root).resolve() != ledger.project_root().resolve():
        return "budget_unmeasurable", {
            "budget": budget,
            "reason": f"watchdog root {root} is not this process's project root "
                      f"{ledger.project_root()}; usage is read from the project root "
                      f"(fb-5b83acbaab4a)"}
    u = usage.measure(str(feature), phase=phase)
    if not u.measured:
        return "budget_unmeasurable", {"budget": budget, "reason": u.reason}
    if u.tokens > budget:
        return "budget_exceeded", {"tokens_used": u.tokens, "budget": budget,
                                   "by_role": u.by_role, "by_model": u.by_model,
                                   "by_session": u.by_session}
    return None, {}


def poll(interval_min: float = 15.0, root: Path | None = None) -> int:
    """Run check() in a loop. Intended for a terminal session; Ctrl-C to stop."""
    while True:
        try:
            check(root)
        except Exception as e:
            print(f"[watchdog] error: {e}")
        time.sleep(interval_min * 60)
