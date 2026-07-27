"""Pre-flight safety checks for `prusik init` — due diligence the operator
should never have to remember.

Installing prusik over an unclean working tree entangles the files it writes
(`.claude/settings.json` merge, the `.gitignore` block, working dirs) with the
operator's in-flight changes, so a clean uninstall can't be verified afterward.
So init FAILS CLOSED on a dirty tree (no silent proceed) — overridable only by an
explicit `--allow-dirty`. It also warns (but does not block) when the target
isn't a git repo, since prusik relies on git worktrees to isolate builders.

Pure-ish: shells out to `git status --porcelain` once; no mutation.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_UNSET = object()


def git_status(target: Path) -> str:
    """'clean' | 'dirty' | 'no-git' | 'no-git-binary'."""
    try:
        r = subprocess.run(
            ["git", "-C", str(target), "status", "--porcelain"],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        return "no-git-binary"
    if r.returncode != 0:
        return "no-git"
    return "dirty" if r.stdout.strip() else "clean"


def init_guard(target: Path, allow_dirty: bool) -> tuple[bool, str]:
    """Decide whether `prusik init` may proceed. Returns (ok, message).

    Fail-closed contract: a dirty tree returns ok=False unless `allow_dirty`.
    A missing git repo / git binary returns ok=True with a WARNING (prusik can
    install, but worktree-isolated builds need git later)."""
    status = git_status(target)

    if status == "no-git-binary":
        return True, ("[prusik-init] WARNING: `git` not found. prusik isolates "
                      "builders in git worktrees — install git before your first "
                      "sprint.")
    if status == "no-git":
        return True, ("[prusik-init] WARNING: not a git repository. prusik uses "
                      "git worktrees to isolate builders — run `git init` and make "
                      "an initial commit before your first sprint.")
    if status == "dirty" and not allow_dirty:
        return False, (
            "[prusik-init] REFUSING: the working tree is not clean.\n"
            "  Installing over uncommitted changes entangles prusik's files with\n"
            "  your work and makes a clean uninstall impossible to verify.\n"
            "  → Commit or stash first. Tip: use a dedicated branch —\n"
            "      git switch -c prusik-trial\n"
            "  Override with --allow-dirty only if you understand the risk.")
    if status == "dirty":  # allow_dirty
        return True, ("[prusik-init] NOTE: --allow-dirty set — proceeding over a "
                      "dirty tree; clean-uninstall verification is on you.")
    return True, ""  # clean


def hook_resolution_warning(which=_UNSET, prefix: str | None = None,
                            base_prefix: str | None = None) -> str:
    """fb-41f8936e3a85: the scaffolded hooks invoke bare `prusik`, resolved
    against the PATH of whatever shell launches Claude Code — NOT the PATH of
    the init run. A venv-only install whose venv isn't on that PATH makes
    EVERY hook exit 127 (command not found), so the harness reads as wired but
    errors on every tool call. Checked at init AND doctor so the drift is
    surfaced at install time and stays surfaced if the environment changes.

    Returns '' when `prusik` resolves globally, an informational note when it
    resolves only inside the active virtualenv, and a loud warning when it
    does not resolve at all. Params are injectable for tests; defaults read
    the live process."""
    if which is _UNSET:
        which = shutil.which("prusik")
    prefix = prefix or sys.prefix
    base_prefix = base_prefix or sys.base_prefix
    if which is None:
        return ("⚠ `prusik` does not resolve on PATH — the gate hooks in "
                ".claude/settings.json will exit 127 (command not found) in a "
                "Claude Code session. Install a PATH-global binary "
                "(`pipx install prusik` or `uv tool install prusik`), or "
                "always launch claude from the environment that has prusik.")
    if prefix != base_prefix:                      # running inside a venv
        try:
            inside = Path(which).resolve().is_relative_to(Path(prefix).resolve())
        except OSError:
            inside = False
        if inside:
            return ("· prusik resolves inside this virtualenv only — the gate "
                    "hooks work when Claude Code is launched from the "
                    "activated venv. For a machine-global install: "
                    "`pipx install prusik` or `uv tool install prusik`.")
    return ""


def branch_recommendation(target: Path) -> str:
    """A one-line nudge toward branch isolation, shown after a clean install."""
    status = git_status(target)
    if status not in ("clean", "dirty"):
        return ""
    return ("Tip: run prusik on a dedicated branch (`git switch -c prusik-trial`) "
            "so a trial is trivially reversible.")
