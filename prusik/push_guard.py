"""Push-or-park — completed work must reach origin, mechanically.

fb-eef892a3e033: a COMPLETED 25-commit sprint sat local-only for a week;
after a session collision the only copy of a migration survived in reflog-
recovered branches. The lease (fb-0b9d0a6d1ce6) serializes writers; this
guards the surviving copy. Every signal here is git's own state — upstream
tracking and `rev-list --count` — never an agent claim, and prusik never
pushes for you (an outward action the operator owns): it detects, names the
branch and the exact command, and (opt-in) blocks.

Default is ADVISORY at reviewing/integrating entry and sprint-complete, plus
a watchdog incident; `push_or_park: {require: true}` in sprint-config makes
the same condition block the advance. A repo with no remote is loudly
inapplicable — visible in the output, never a silent skip and never a block
(remote-lessness is the operator's environment choice, not a defect).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_CHECKED_PHASES = ("reviewing", "integrating")


def _git(root: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(root), *args],
                           capture_output=True, text=True, timeout=30,
                           check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def state(root: Path) -> dict:
    """Push state from git's own plumbing. `ahead` is None when there is no
    upstream to count against."""
    remotes = _git(root, "remote")
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    upstream = _git(root, "rev-parse", "--abbrev-ref", "@{upstream}")
    ahead = None
    if upstream:
        n = _git(root, "rev-list", "--count", "@{upstream}..HEAD")
        ahead = int(n) if n and n.isdigit() else None
    return {"remote": bool(remotes), "branch": branch,
            "detached": branch == "HEAD", "upstream": upstream,
            "ahead": ahead}


def verdict(st: dict) -> tuple[bool, str]:
    """(ok, human reason). ok=False means unpushed work exists. A remote-less
    repo is ok with an explicit inapplicability reason — loud, never silent."""
    if not st["remote"]:
        return True, "no remote configured — push-or-park inapplicable"
    branch = st["branch"] or "?"
    if st["detached"]:
        return False, ("detached HEAD — this work is on no branch origin "
                       "knows; park it: git switch -c <branch> && git push "
                       "-u origin <branch>")
    if not st["upstream"]:
        return False, (f"branch '{branch}' has no upstream — origin holds "
                       f"NO copy; park it: git push -u origin {branch}")
    if st["ahead"]:
        n = st["ahead"]
        return False, (f"{n} commit(s) on '{branch}' exist only on this "
                       f"disk (ahead of {st['upstream']}); park them: "
                       f"git push")
    return True, f"'{branch}' is on {st['upstream']} — parked"


def gate_check(root: Path, config: dict, feature: str | None,
               phase: str) -> bool:
    """Advance-path check: advisory (True) by default with an
    `unpushed_sprint_work` ledger event; blocking (False) when sprint-config
    sets `push_or_park: {require: true}`."""
    ok, reason = verdict(state(root))
    if ok:
        if "inapplicable" in reason:
            print(f"[prusik-gate] push-or-park: {reason}")
        return True
    from prusik import ledger
    ledger.append("unpushed_sprint_work", feature=feature, phase=phase,
                  reason=reason)
    require = bool((config.get("push_or_park") or {}).get("require"))
    tone = "BLOCKED" if require else "ADVISORY"
    print(f"[prusik-gate] push-or-park {tone} — {reason}. A single-disk "
          f"copy of sprint work is data-loss exposure (fb-eef892a3e033)."
          + ("" if require else " (Set push_or_park: {require: true} in "
                               "sprint-config to hard-block.)"))
    return not require


def watchdog_check(root: Path, phase: str | None) -> tuple[str, dict] | None:
    """Incident payload when the active sprint is at a checked phase with
    unpushed work; None when clean/inapplicable/other phase."""
    if phase not in _CHECKED_PHASES:
        return None
    ok, reason = verdict(state(root))
    if ok:
        return None
    return "unpushed_sprint_work", {"reason": reason}
