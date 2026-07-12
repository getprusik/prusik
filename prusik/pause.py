"""`prusik pause` / `prusik resume` — suspend Stop-hook exit-artifact enforcement.

When the operator pauses a sprint mid-phase (e.g. to fix a prusik issue,
yield to user input, or take a break), the Stop hook would otherwise fire
on the pause turn and refuse the session-end with "unsatisfied exit
artifacts." This is correct enforcement in principle but noisy during
deliberate pauses.

`prusik pause [reason]` writes `.sprint/paused` (JSON: started_at + optional
reason). The Stop hook honors it: if present, skip the exit-artifact
check. `prusik resume` removes the marker; both write ledger events for
digest analysis.

v0.6.3 (B8): `pause` now accepts an optional reason argument. Pre-v0.6.3
the CLI rejected any positional/keyword args with exit 2 — but the
`/sprint-pause` slash command's `ARGUMENTS:` slot tempted reasonable
agents to forward the user's reason text. Friendlier behavior: accept
the reason, record it in the marker JSON and ledger event for diagnostic
visibility in `prusik status`.

Idempotent: repeat-pause and repeat-resume are no-ops.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from prusik import ledger
from prusik.ledger import project_root


def _marker(root: Path | None = None) -> Path:
    return (root or project_root()) / ".sprint" / "paused"


def is_paused(root: Path | None = None) -> bool:
    return _marker(root).exists()


def _read_marker(root: Path | None = None) -> dict | None:
    """Return the marker's contents as a dict, or None if not paused.

    Backward compat:
      - Empty marker file (legacy v0.3.8 form)        → {}
      - Non-JSON text in marker (unexpected, treat as raw reason) → {"reason": text}
      - Valid JSON                                     → parsed dict
    """
    m = _marker(root)
    if not m.exists():
        return None
    text = m.read_text().strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"reason": text}


def paused_reason(root: Path | None = None) -> str | None:
    state = _read_marker(root)
    if state is None:
        return None
    return state.get("reason")


def pause(reason: str | None = None) -> int:
    m = _marker()
    if m.exists():
        existing = _read_marker()
        existing_reason = (existing or {}).get("reason")
        if existing_reason:
            print(f"[prusik-pause] already paused — reason: {existing_reason}")
        else:
            print("[prusik-pause] already paused")
        return 0
    m.parent.mkdir(parents=True, exist_ok=True)
    state: dict = {"started_at": datetime.now(timezone.utc).isoformat()}
    if reason:
        state["reason"] = reason
    m.write_text(json.dumps(state, indent=2))
    ledger.append("pause_started", reason=reason)
    print("[prusik-pause] Stop hook will skip exit-artifact enforcement until `prusik resume`")
    if reason:
        print(f"  reason: {reason}")
    print(f"  marker: {m.relative_to(project_root())}")
    return 0


def resume() -> int:
    m = _marker()
    if not m.exists():
        print("[prusik-resume] not paused; nothing to do")
        return 0
    state = _read_marker() or {}
    reason = state.get("reason")
    started_at = state.get("started_at")
    duration_sec = None
    if started_at:
        try:
            start = datetime.fromisoformat(started_at)
            duration_sec = int(
                (datetime.now(start.tzinfo) - start).total_seconds()
            )
        except ValueError:
            pass
    m.unlink()
    ledger.append("pause_ended", reason=reason, duration_sec=duration_sec)
    print("[prusik-resume] Stop hook re-engaged (exit-artifact checks active)")
    if duration_sec is not None:
        print(f"  paused for: {duration_sec}s"
              + (f" — {reason}" if reason else ""))
    return 0


def status() -> int:
    state = _read_marker()
    if state is None:
        print("[prusik-pause] active (not paused)")
        return 0
    reason = state.get("reason")
    if reason:
        print(f"[prusik-pause] PAUSED — reason: {reason}")
    else:
        print("[prusik-pause] PAUSED — Stop hook will skip exit-artifact checks")
    return 0
