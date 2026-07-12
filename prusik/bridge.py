"""Shared-document bridge between a live Claude Code session and a separate
author/operator session.

OPT-IN feature, default OFF. The bridge imposes nothing until you turn it on
with `prusik bridge on`; `sprint-run` checks `is_enabled()` and skips all
bridge steps while off, so a solo adopter never sees it. Turn it on for a
live collaboration/trial where a second session (the `prusik-author`) watches
and feeds FIX/GUIDANCE back into the running session.

Mechanism:
- A shared file at $PRUSIK_BRIDGE_PATH (recommended:
  ~/.claude/prusik/bridges/<slug>/bridge.md) is appended to by both sessions
  using structured `### [HH:MM] role → other — KIND` headers.
- The live session's `.claude/settings.json` gets a `UserPromptSubmit` hook
  that runs `prusik bridge poll`, which reads the bridge, finds any new
  `prusik-author → live-cc` entries since the last poll, and injects them as
  `additionalContext`. The live session sees updates on its next user turn
  automatically.
- The author side tails the bridge file via Monitor; each new
  `live-cc → prusik-author` entry surfaces within seconds and can be
  responded to by appending to the same file.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# --- vocabulary ------------------------------------------------------------
# (Env var was KIT_BRIDGE_PATH and the author role was prusik-author before the
# kit→prusik rename; the back-compat fallbacks were removed in v0.33.0 once
# the sole install was migrated.)
ENV_VAR = "PRUSIK_BRIDGE_PATH"
ROLE_LIVE = "live-cc"
ROLE_AUTHOR = "prusik-author"
VALID_ROLES = (ROLE_LIVE, ROLE_AUTHOR)
POLL_HOOK_COMMAND = "prusik bridge poll"


def bridge_path() -> Path | None:
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env).expanduser()
    return _configured_bridge_path()


def _configured_bridge_path() -> Path | None:
    """The DURABLE configured path from `.claude/settings.local.json` — what
    `bridge on` just wrote. Distinct from bridge_path()'s env-FIRST resolution: the
    session `PRUSIK_BRIDGE_PATH` is injected by Claude Code at session START, so
    after a mid-session `bridge on --slug NEW` the env var is STALE until restart.
    `status` must report this configured value, not the stale session cache, else it
    confusingly shows the old slug right after a successful re-slug (fb-1e1badf0b6ef)."""
    local = Path.cwd() / ".claude" / "settings.local.json"
    if not local.exists():
        return None
    try:
        data = json.loads(local.read_text())
        p = (data.get("env") or {}).get(ENV_VAR)
        return Path(p).expanduser() if p else None
    except (json.JSONDecodeError, OSError):
        return None


def _has_poll_hook(ups: list) -> bool:
    """True if the UserPromptSubmit hook list already wires `prusik bridge poll`."""
    return any(
        h.get("command") == POLL_HOOK_COMMAND
        for block in ups
        for h in block.get("hooks", [])
    )


def is_enabled(root: Path | None = None) -> bool:
    """The bridge is ON for this project iff the poll hook is wired into
    .claude/settings.json. This is the single source of truth the toggle
    (`on`/`off`) flips and that `sprint-run` branches on — default OFF, so the
    bridge imposes nothing on adopters who never turn it on."""
    settings = (root or Path.cwd()) / ".claude" / "settings.json"
    if not settings.exists():
        return False
    try:
        data = json.loads(settings.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    return _has_poll_hook(data.get("hooks", {}).get("UserPromptSubmit", []))


def _offset_marker() -> Path:
    return Path.cwd() / ".sprint" / "bridge-last-offset.txt"


def _read_offset() -> int:
    p = _offset_marker()
    if not p.exists():
        return 0
    try:
        return int(p.read_text().strip())
    except (ValueError, OSError):
        return 0


def _write_offset(offset: int) -> None:
    p = _offset_marker()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(offset))


_ENTRY_START_RE = re.compile(
    rf"^### \[\d{{2}}:\d{{2}}\] ({ROLE_LIVE}|{ROLE_AUTHOR}) → ({ROLE_LIVE}|{ROLE_AUTHOR}) — ",
    re.MULTILINE,
)
# prusik-author → live-cc entries (what poll surfaces into the live session).
_AUTHOR_ENTRY_RE = re.compile(
    rf"(### \[\d{{2}}:\d{{2}}\] {ROLE_AUTHOR} → {ROLE_LIVE} — .*?)(?=\n### \[|\Z)",
    re.DOTALL,
)


def _filter_author_entries(text: str) -> str:
    matches = _AUTHOR_ENTRY_RE.findall(text)
    return "\n\n".join(m.strip() for m in matches)


def poll() -> int:
    """UserPromptSubmit hook entry. Prints new prusik-author entries as context."""
    bp = bridge_path()
    if not bp or not bp.exists():
        return 0
    size = bp.stat().st_size
    last = _read_offset()
    if size <= last:
        return 0
    try:
        with open(bp, "rb") as f:
            f.seek(last)
            new_bytes = f.read()
    except OSError:
        return 0
    _write_offset(size)
    new_text = new_bytes.decode("utf-8", errors="replace")
    entries = _filter_author_entries(new_text)
    if not entries.strip():
        return 0
    context = (
        "[prusik-bridge] new entries from prusik-author since last turn — "
        "apply any FIX/GUIDANCE, then append an UPDATE entry to the bridge.\n\n"
        + entries
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }))
    return 0


def enable(slug: str | None = None) -> int:
    """Provision the bridge dir, set env var, wire the UserPromptSubmit hook."""
    settings = Path.cwd() / ".claude" / "settings.json"
    if not settings.exists():
        print(f"[prusik-bridge] no {settings} — run `prusik init` first", file=sys.stderr)
        return 1

    if not slug:
        slug = datetime.now().strftime("%Y-%m-%d") + "-trial"
    bridge_dir = Path.home() / ".claude" / "prusik" / "bridges" / slug
    bridge_dir.mkdir(parents=True, exist_ok=True)
    (bridge_dir / "patches").mkdir(exist_ok=True)
    bridge_file = bridge_dir / "bridge.md"
    errors_log = bridge_dir / "errors.log"
    if not bridge_file.exists() or bridge_file.stat().st_size == 0:
        header = (
            f"# Prusik bridge — {slug}\n\n"
            f"Started: {datetime.now(timezone.utc).isoformat()}\n\n"
            f"Protocol: append `### [HH:MM] <role> → <other> — KIND` entries.\n"
            f"Roles: `{ROLE_LIVE}`, `{ROLE_AUTHOR}`. Kinds: QUESTION, BUG,\n"
            f"OBSERVATION, FIX, GUIDANCE, DIAGNOSTIC, UPDATE.\n\n"
        )
        bridge_file.write_text(header)
    errors_log.touch(exist_ok=True)

    settings_local = Path.cwd() / ".claude" / "settings.local.json"
    local_data: dict = {}
    if settings_local.exists():
        try:
            local_data = json.loads(settings_local.read_text())
        except json.JSONDecodeError:
            local_data = {}
    local_data.setdefault("env", {})[ENV_VAR] = str(bridge_file)
    settings_local.write_text(json.dumps(local_data, indent=2) + "\n")

    data = json.loads(settings.read_text())
    hooks = data.setdefault("hooks", {})
    ups = hooks.setdefault("UserPromptSubmit", [])
    if not _has_poll_hook(ups):
        ups.append({
            "hooks": [{
                "type": "command",
                "command": POLL_HOOK_COMMAND,
                "timeout": 5,
            }]
        })
        settings.write_text(json.dumps(data, indent=2) + "\n")

    print("[prusik-bridge] enabled (on)")
    print(f"  bridge file:  {bridge_file}")
    print(f"  errors log:   {errors_log}")
    print(f"  patches dir:  {bridge_dir / 'patches'}")
    print(f"  hook:         UserPromptSubmit → `{POLL_HOOK_COMMAND}` (injects new {ROLE_AUTHOR} entries)")
    print(f"  env:          {ENV_VAR} in .claude/settings.local.json")
    return 0


def disable() -> int:
    settings = Path.cwd() / ".claude" / "settings.json"
    if not settings.exists():
        print(f"[prusik-bridge] no {settings}; nothing to disable")
        return 0
    data = json.loads(settings.read_text())
    hooks = data.get("hooks", {})
    ups = hooks.get("UserPromptSubmit", [])
    new_ups = []
    for block in ups:
        kept = [h for h in block.get("hooks", [])
                if h.get("command") != POLL_HOOK_COMMAND]
        if kept:
            block["hooks"] = kept
            new_ups.append(block)
    if new_ups:
        hooks["UserPromptSubmit"] = new_ups
    else:
        hooks.pop("UserPromptSubmit", None)
    if not hooks:
        data.pop("hooks", None)
    settings.write_text(json.dumps(data, indent=2) + "\n")

    local = Path.cwd() / ".claude" / "settings.local.json"
    if local.exists():
        try:
            local_data = json.loads(local.read_text())
            env = local_data.get("env") or {}
            env.pop(ENV_VAR, None)
            if env:
                local_data["env"] = env
            else:
                local_data.pop("env", None)
            if local_data:
                local.write_text(json.dumps(local_data, indent=2) + "\n")
            else:
                local.unlink()
        except json.JSONDecodeError:
            pass

    offset = _offset_marker()
    if offset.exists():
        offset.unlink()
    print("[prusik-bridge] disabled (off) — hook + env var removed; bridge file on disk retained")
    return 0


def on(slug: str | None = None) -> int:
    """Turn the bridge ON (alias for `enable`)."""
    return enable(slug)


def off() -> int:
    """Turn the bridge OFF (alias for `disable`)."""
    return disable()


def status() -> int:
    enabled = is_enabled()
    print(f"bridge:       {'ON' if enabled else 'OFF'}")
    # Report the CONFIGURED path (settings.local.json — what `bridge on` wrote), not
    # the env-first bridge_path(): the session env var is loaded at start and goes
    # stale after a mid-session re-slug (fb-1e1badf0b6ef). Flag the divergence so
    # the operator knows the running hooks still use the old path until restart.
    configured = _configured_bridge_path()
    active_env = os.environ.get(ENV_VAR)
    active = Path(active_env).expanduser() if active_env else None
    bp = configured or active
    print(f"bridge path:  {bp or '(not set)'}")
    if configured and active and configured != active:
        print(f"  ⚠ session-active path differs: {active}")
        print(f"    Claude Code loads {ENV_VAR} at session start — restart for the "
              f"configured path to take effect (running hooks still use the old one).")
    if bp and bp.exists():
        size = bp.stat().st_size
        mtime = datetime.fromtimestamp(bp.stat().st_mtime).isoformat(timespec="seconds")
        print(f"  exists:     yes, {size} bytes, last modified {mtime}")
    else:
        print("  exists:     no")

    settings = Path.cwd() / ".claude" / "settings.json"
    if settings.exists():
        print(f"  hook:       {'installed' if enabled else 'not installed'}")
    else:
        print("  hook:       (no .claude/settings.json)")

    offset = _read_offset()
    print(f"  last offset: {offset} byte(s) processed by poll")
    if not enabled:
        print("  (bridge is OFF — `prusik bridge on [--slug <s>]` to enable; "
              "sprint-run skips bridge steps while off)")
    return 0


# v0.11.2 — bridge append must NEVER silently lose a message. Surfaced by
# live-cc on the m4-test-infra-hardening trial (bridge [22:55]): a first
# BUG append was lost to a write race. The old path was an unlocked
# buffered text append — concurrent `prusik bridge write` calls (or an
# out-of-band whole-file rewrite) interleave or clobber, and the loss is
# SILENT, in the exact channel the evidence loop depends on (same
# safe-signal-over-unsafe-reality meta-shape as Candidate F). Fix:
# flock-guarded SINGLE atomic O_APPEND write, then read-back verification;
# an unverifiable append is recorded to errors.log and surfaced loudly
# (rc 1) — never silently dropped. flock only binds cooperating writers;
# verify+record is what makes a non-cooperating clobber loud, not lost.
def _locked_append_verified(bp: Path, entry: str, attempts: int = 4) -> bool:
    try:
        import fcntl
    except ImportError:
        fcntl = None  # type: ignore[assignment]  # non-POSIX: atomic-append + verify, no lock
    for _ in range(attempts):
        try:
            fd = os.open(bp, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        except OSError:
            return False
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, entry.encode("utf-8"))  # single O_APPEND syscall = atomic
            os.fsync(fd)
        except OSError:
            os.close(fd)
            return False
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        os.close(fd)
        try:
            if entry in bp.read_text(errors="replace"):
                return True  # persisted and not clobbered
        except OSError:
            pass
        time.sleep(0.05)  # a clobberer landed between write and verify — retry
    return False


def _record_bridge_loss(bp: Path, entry: str) -> Path:
    """Persist an unverifiable append to the bridge dir's errors.log so it
    is recoverable and LOUD — the invariant is never-silent, not never-fail."""
    log = bp.parent / "errors.log"
    try:
        with open(log, "a") as f:
            f.write(f"\n[{datetime.now(timezone.utc).isoformat()}] "
                    f"UNVERIFIED BRIDGE APPEND (possible write-race loss):\n"
                    f"{entry}\n")
    except OSError:
        pass
    return log


def write_entry(role: str, kind: str, body: str) -> int:
    bp = bridge_path()
    if not bp:
        print(f"[prusik-bridge] {ENV_VAR} not set — run `prusik bridge on` first", file=sys.stderr)
        return 1
    if role not in VALID_ROLES:
        print(f"[prusik-bridge] role must be {ROLE_LIVE} or {ROLE_AUTHOR} (got {role!r})", file=sys.stderr)
        return 1
    other = ROLE_AUTHOR if role == ROLE_LIVE else ROLE_LIVE
    ts = datetime.now().strftime("%H:%M")
    entry = f"\n### [{ts}] {role} → {other} — {kind.upper()}\n{body.rstrip()}\n"
    bp.parent.mkdir(parents=True, exist_ok=True)
    if _locked_append_verified(bp, entry):
        print(f"[prusik-bridge] appended to {bp}")
        return 0
    log = _record_bridge_loss(bp, entry)
    print(f"[prusik-bridge] WARNING: append could NOT be verified after retries "
          f"(write-race / out-of-band rewrite). Entry recorded to {log} — "
          f"NOT silently lost. Re-issue or recover from errors.log.",
          file=sys.stderr)
    return 1
