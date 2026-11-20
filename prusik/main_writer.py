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
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from prusik import ledger

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


def host_process() -> dict | None:
    """The host process this call runs under: {"pid", "since"}, or None when the host gives
    none (the lease then keeps the TTL rule). `since` is when the lease first recorded this
    pid: a process that STARTED after it is not the holder (the pid was reused). Recording it
    spawns nothing, which the hook hot path requires."""
    return _proc(_env_pid())


def _proc(pid: int | None) -> dict[str, object] | None:
    if not pid:
        return None
    return {"pid": pid, "since": datetime.now(timezone.utc).isoformat()}


def process_started_at(pid: int) -> datetime | None:
    """When `pid` started, or None when it can't be read. Linux reads /proc; elsewhere `ps`
    (lazy import: only a liveness check of ANOTHER session's lease pays for it)."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            ticks = int(f.read().rsplit(")", 1)[1].split()[19])      # field 22, starttime
        with open("/proc/stat", encoding="utf-8") as f:
            btime = next(int(ln.split()[1]) for ln in f if ln.startswith("btime "))
        return datetime.fromtimestamp(btime + ticks / os.sysconf("SC_CLK_TCK"), timezone.utc)
    except (OSError, IndexError, ValueError, StopIteration):
        pass
    import subprocess
    import time
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True,
                             text=True, timeout=5, check=False,
                             env={**os.environ, "LC_ALL": "C"}).stdout.strip()
        return datetime.fromtimestamp(time.mktime(time.strptime(out, "%a %b %d %H:%M:%S %Y")),
                                      timezone.utc)
    except (OSError, subprocess.SubprocessError, ValueError, OverflowError):
        return None


_REUSE_SLACK = timedelta(seconds=5)       # lstart has 1s resolution; clocks round


def _proc_alive(pid: object, since: object = None) -> bool:
    """Is process `pid` running, and not a later process that reused the pid (it started after
    `since`, when the lease recorded it)? Unverifiable answers fail safe toward alive: a wrong
    "dead" lets a second session write the shared tree."""
    if not _alive(pid):
        return False
    try:
        recorded = datetime.fromisoformat(str(since)) if since else None
    except ValueError:
        recorded = None
    if recorded is not None:
        started = process_started_at(pid)      # type: ignore[arg-type]
        if started is not None and started > recorded + _REUSE_SLACK:
            return False
    return True


def _holder_dead(lease: dict) -> bool:
    """True only when the lease records a holder process and it is provably gone. No record
    (older lease, host with no process) is never dead: that lease keeps the TTL rule."""
    host = lease.get("host")
    if not isinstance(host, dict) or not isinstance(host.get("pid"), int):
        return False
    return not _proc_alive(host["pid"], host.get("since"))


def _gone(lease: dict, now: datetime) -> str | None:
    """Why the lease no longer binds: 'stale' (TTL) or 'dead' (holder process gone), else None.
    The one liveness rule behind check_and_touch, foreign_holder and live_owner."""
    if _is_stale(lease, now):
        return "stale"
    return "dead" if _holder_dead(lease) else None


def _write(root: Path, lease: dict) -> None:
    """Atomic lease write: tmp + replace, so a concurrent reader never sees half a file."""
    path = _lease_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(lease, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def check_and_touch(root: Path, session: str, *, feature: str | None = None,
                    phase: str | None = None) -> tuple[bool, dict | None, str]:
    """The whole protocol, one call per shared-tree mutation:
      no lease            → acquire            → (True,  None,   'acquired')
      own live lease      → refresh            → (True,  None,   'refreshed')
      delegate, live      → refresh timestamp  → (True,  None,   'refreshed')
      other, stale        → take over          → (True,  old,    'stolen_stale')
      other, holder dead  → take over          → (True,  old,    'stolen_dead')
      other, live         → DENY               → (False, holder, 'denied')
    A delegate (`delegate`) acts as the holder and never changes the holder's identity.
    """
    now = datetime.now(timezone.utc)
    lease = load(root)
    prior: dict | None = None
    if lease is not None:
        if lease.get("session") == session:
            host = lease.get("host")
            env_pid = _env_pid()
            if env_pid is not None and not (isinstance(host, dict) and host.get("pid") == env_pid):
                host = host_process()           # a new host process for this session
            lease.update(refreshed_at=now.isoformat(), feature=feature, phase=phase)
            lease.pop("drive_stamp", None)      # a write by the holder ends the Stop exemption
            if host:
                lease["host"] = host
            _write(root, lease)
            return True, None, "refreshed"
        gone = _gone(lease, now)
        if gone is None and session in (lease.get("delegates") or []):
            lease["refreshed_at"] = now.isoformat()
            _write(root, lease)
            return True, None, "refreshed"
        if gone is None:
            return False, lease, "denied"
        action, prior = f"stolen_{gone}", lease
    else:
        action = "acquired"
    fresh: dict[str, object] = {"session": session, "feature": feature, "phase": phase,
                                "acquired_at": now.isoformat(),
                                "refreshed_at": now.isoformat()}
    host = host_process()
    if host:
        fresh["host"] = host
    _write(root, fresh)
    return True, prior, action


def _env_pid() -> int | None:
    from prusik.platform_adapter import get_adapter
    return get_adapter().host_pid()


def foreign_holder(root: Path, session: str | None) -> dict | None:
    """The live lease when another session holds it: its `session` is neither `session` nor
    one of its `delegates`. None when there is no lease, it is stale, or it is held by or
    delegated to `session`. Read-only: never writes the lease."""
    lease = load(root)
    if not isinstance(lease, dict):
        return None
    if session and (lease.get("session") == session
                    or session in (lease.get("delegates") or [])):
        return None                       # own lease: no liveness lookup on the hot path
    return None if _gone(lease, datetime.now(timezone.utc)) else lease


def delegate(root: Path, holder: str, delegate_session: str, *, role: str,
             feature: str) -> bool:
    """The live lease holder `holder` lets a session it spawned (`prusik drive`) write the
    shared tree as the holder (fb-13faaeb53496). False unless `holder` holds the live lease."""
    lease = load(root)
    if lease is None or lease.get("session") != holder or _is_stale(
            lease, datetime.now(timezone.utc)):
        return False
    delegates = list(lease.get("delegates") or [])
    if delegate_session not in delegates:
        delegates.append(delegate_session)
    lease["delegates"] = delegates
    lease["refreshed_at"] = datetime.now(timezone.utc).isoformat()
    _write(root, lease)
    ledger.append("writer_delegated", holder=holder, delegate=delegate_session,
                  role=role, feature=feature)
    return True


def heartbeat(root: Path, holder: str) -> bool:
    """Refresh the lease when `holder` holds it live. A builder's worktree writes never
    refresh the shared-tree lease, so drive calls this while a long spawn runs."""
    lease = load(root)
    if lease is None or lease.get("session") != holder or _is_stale(
            lease, datetime.now(timezone.utc)):
        return False
    lease["refreshed_at"] = datetime.now(timezone.utc).isoformat()
    _write(root, lease)
    return True


def set_driver(root: Path, holder: str, feature: str) -> bool:
    """Record that this process, a `prusik drive` run by the live holder `holder`, owns
    `feature`'s phases until `clear_driver` (fb-5861062129e7). False unless `holder` holds the
    live lease."""
    lease = load(root)
    if lease is None or lease.get("session") != holder or _is_stale(
            lease, datetime.now(timezone.utc)):
        return False
    lease["driver"] = {"pid": os.getpid(), "feature": feature,
                       "since": datetime.now(timezone.utc).isoformat()}
    _write(root, lease)
    return True


def clear_driver(root: Path) -> None:
    """Drop this process's driver record, leaving another process's alone."""
    lease = load(root)
    if lease is not None and (lease.get("driver") or {}).get("pid") == os.getpid():
        del lease["driver"]
        _write(root, lease)


def live_owner(root: Path, feature: str | None, session: str | None) -> bool:
    """True when the phase work of `feature` is owned by someone other than a person: the
    lease is live, and either `session` is a session the holder delegated to (a role drive
    spawned) or a drive process for `feature` is still running."""
    lease = load(root)
    if lease is None or _gone(lease, datetime.now(timezone.utc)):
        return False
    if session and session in (lease.get("delegates") or []):
        return True
    return _driver_live(lease, feature)


def _driver_live(lease: dict, feature: str | None) -> bool:
    driver = lease.get("driver") or {}
    return (bool(feature) and driver.get("feature") == feature
            and _proc_alive(driver.get("pid"), driver.get("since")))


def stamp_for_drive(root: Path, session: str | None, feature: str, phase: str | None) -> bool:
    """`sprint-start` run by the live lease holder: record the sprint on the lease (the pre-tool
    refresh recorded the state from before the command ran) and mark it for drive. The stamp is
    its own field; the holder's next refresh clears it. False unless `session` holds the lease."""
    lease = load(root)
    if (not session or lease is None or lease.get("session") != session
            or _gone(lease, datetime.now(timezone.utc))):
        return False
    lease.update(feature=feature, phase=phase, drive_stamp=feature)
    _write(root, lease)
    return True


def stamped_for_drive(root: Path, session: str | None, feature: str | None) -> bool:
    """True when the live lease is `session`'s and stamped for drive on `feature`."""
    lease = load(root)
    return bool(session and feature and lease and lease.get("session") == session
                and lease.get("drive_stamp") == feature
                and not _gone(lease, datetime.now(timezone.utc)))


def hand_off_to_drive(root: Path, drive_session: str, feature: str) -> dict | None:
    """`prusik drive <feature>` takes the live lease of a host session working that feature,
    when no drive for it already runs. Returns the prior lease, or None (nothing changed) when
    there is no live lease, it is drive's own, its feature differs, or a live driver exists.
    Writes a fresh lease for drive: no delegates, host = drive's own process."""
    now = datetime.now(timezone.utc)
    lease = load(root)
    if (lease is None or lease.get("session") == drive_session or _gone(lease, now)
            or not feature or lease.get("feature") != feature
            or _driver_live(lease, feature)):
        return None
    _write(root, {"session": drive_session, "feature": feature, "phase": lease.get("phase"),
                  "acquired_at": now.isoformat(), "refreshed_at": now.isoformat(),
                  "host": _proc(os.getpid()), "handed_off_from": lease.get("session")})
    return lease


def _alive(pid: object) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def release(root: Path) -> dict | None:
    """Delete the lease (explicit hand-off / sprint completion). Returns the
    released lease for the audit trail, or None if none was held."""
    lease = load(root)
    try:
        _lease_path(root).unlink(missing_ok=True)
    except OSError:
        pass
    return lease


def release_if_held(root: Path, holder: str) -> dict | None:
    """Delete the lease only when `holder` holds it (`prusik drive` at exit, fb-d50f2764de21).
    Returns the released lease, or None when there was none or someone else holds it, whose
    lease is left exactly as it is. The holder check lives with the delete, not in callers."""
    lease = load(root)
    if lease is None or lease.get("session") != holder:
        return None
    return release(root)


def deny_message(holder: dict, command: str = "") -> str:
    ses = (holder.get("session") or "?")[:12]
    ctx = ""
    if holder.get("handed_off_from") or holder.get("driver"):
        feat = holder.get("feature") or (holder.get("driver") or {}).get("feature") or "?"
        return (f"[prusik-gate] the shared main tree is held by `prusik drive {feat}` "
                f"(feature {feat}) — it took the single-writer lease from the session that "
                f"started the sprint, and returns it when it exits. Writing the shared tree "
                f"now would collide with it (fb-0b9d0a6d1ce6).\n"
                f"  → Wait for the drive run to finish, work in a worktree "
                f"(worktrees/<role>/…), or stop the drive run."
                f"{_release_chained_note(command)}")
    if holder.get("feature") or holder.get("phase"):
        ctx = f" (feature {holder.get('feature') or '?'}, phase {holder.get('phase') or '?'})"
    return (f"[prusik-gate] the shared main tree has an active writer — session "
            f"{ses}…{ctx} holds the single-writer lease (refreshes on each of its "
            f"writes; expires after {LEASE_TTL_MIN}m idle). Two sessions writing the "
            f"same checkout is how 25 commits got silently discarded "
            f"(fb-0b9d0a6d1ce6).\n"
            f"  → Work in an isolated worktree instead (worktrees/<role>/…), or — "
            f"ONLY if the other session is finished — hand off with "
            f"`prusik gate release-writer`, run as its own command."
            f"{_release_chained_note(command)}")


def _release_chained_note(command: str) -> str:
    """fb-3e80f782a066: `prusik gate release-writer && git push` was refused with a hint
    that named release-writer and did not say why it had not worked. The gate judges a
    whole command before any of it runs, so the release in it had not happened yet."""
    if "gate release-writer" not in " ".join((command or "").split()):
        return ""
    return ("\n  → This command already contains `gate release-writer`, but a command is "
            "judged whole before any of it runs, so the lease was still held when it was "
            "checked. Run `prusik gate release-writer` alone first, then the rest.")
