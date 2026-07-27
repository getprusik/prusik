"""Single-writer lease on the SHARED main tree (fb-0b9d0a6d1ce6).

Field collision: two concurrent Claude sessions both held write access to the
same checkout — one (a sprint in 'integrating', writable '**') was working
post-lane-merge on main; the other (an ad-hoc security responder with NO
active sprint, which the gate never even saw) hard-reset main and silently
discarded 25 unpushed commits, while the first session's in-flight edits were
reverted under it. The structural gap: worktrees isolate BUILDERS, but nothing
serialized the shared tree itself across sessions.

The primitive: a TTL lease (`.sprint/main-writer.json`) keyed by the host
session id. The first session to mutate the shared tree acquires it; every
allowed mutation refreshes it; a DIFFERENT session mutating the shared tree is
denied while the lease is live. Worktrees stay fully concurrent — only the
shared tree is single-writer. Self-healing: a crashed/idle holder expires by
staleness (no daemon, no lock server), and an operator can hand off explicitly
with `prusik gate release-writer` (audited). Enforced for EVERY session —
sprint or sprint-less — because the field damage came from the sprint-less one.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

# A holder that hasn't written for this long is presumed gone (crashed session,
# closed laptop). Long enough to span a think-pause mid-sprint, short enough
# that a genuinely dead session doesn't wedge the repo for the day.
LEASE_TTL_MIN = 30


def _lease_path(root: Path) -> Path:
    return root / ".sprint" / "main-writer.json"


def load(root: Path) -> dict | None:
    try:
        return json.loads(_lease_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _is_stale(lease: dict, now: datetime) -> bool:
    try:
        refreshed = datetime.fromisoformat(lease["refreshed_at"])
    except (KeyError, ValueError, TypeError):
        return True                       # unreadable timestamp = unproven = stale
    return (now - refreshed) > timedelta(minutes=LEASE_TTL_MIN)


def check_and_touch(root: Path, session: str, *, feature: str | None = None,
                    phase: str | None = None) -> tuple[bool, dict | None, str]:
    """The whole protocol, one call per shared-tree mutation:
      no lease            → acquire            → (True,  None,   'acquired')
      own live lease      → refresh            → (True,  None,   'refreshed')
      other, stale        → take over          → (True,  old,    'stolen_stale')
      other, live         → DENY               → (False, holder, 'denied')
    """
    now = datetime.now(timezone.utc)
    lease = load(root)
    action = "acquired"
    prior: dict | None = None
    if lease is not None:
        if lease.get("session") == session:
            action = "refreshed"
        elif _is_stale(lease, now):
            action, prior = "stolen_stale", lease
        else:
            return False, lease, "denied"
    acquired_at = (lease["acquired_at"] if action == "refreshed"
                   and lease and lease.get("acquired_at") else now.isoformat())
    path = _lease_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "session": session, "feature": feature, "phase": phase,
        "acquired_at": acquired_at, "refreshed_at": now.isoformat(),
    }, indent=1), encoding="utf-8")
    return True, prior, action


def release(root: Path) -> dict | None:
    """Delete the lease (explicit hand-off / sprint completion). Returns the
    released lease for the audit trail, or None if none was held."""
    lease = load(root)
    try:
        _lease_path(root).unlink(missing_ok=True)
    except OSError:
        pass
    return lease


def deny_message(holder: dict) -> str:
    ses = (holder.get("session") or "?")[:12]
    ctx = ""
    if holder.get("feature") or holder.get("phase"):
        ctx = f" (feature {holder.get('feature') or '?'}, phase {holder.get('phase') or '?'})"
    return (f"[prusik-gate] the shared main tree has an active writer — session "
            f"{ses}…{ctx} holds the single-writer lease (refreshes on each of its "
            f"writes; expires after {LEASE_TTL_MIN}m idle). Two sessions writing the "
            f"same checkout is how 25 commits got silently discarded "
            f"(fb-0b9d0a6d1ce6).\n"
            f"  → Work in an isolated worktree instead (worktrees/<role>/…), or — "
            f"ONLY if the other session is finished — hand off with "
            f"`prusik gate release-writer`.")
