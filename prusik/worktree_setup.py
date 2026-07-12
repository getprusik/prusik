"""Worktree setup for team builds (v0.74.0, field seam #2 — findings #10/#11).

A fresh builder worktree on a JS/TS monorepo can't typecheck or test until its
deps are installed AND the workspace packages are built (`dist/`, which
cross-package imports resolve to). This emits — or runs — the package-manager-
aware setup commands for the detected stack, so prusik's team-build does the
worktree prep instead of every builder rediscovering it.

Fails CLOSED: with `--run`, a non-zero from any setup command stops the sequence
and returns rc≠0 (a half-set-up worktree must not look ready). Empty for stacks
that need no setup (a Python partial-mirror sprint runs from the project root).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SETUP_TIMEOUT_SEC = 600


def _carry_env_files(root: Path, target: Path, do_link: bool) -> list[str]:
    """A fresh git worktree does NOT inherit gitignored files. A project that keeps
    real secrets in a gitignored `.env` then sees secret-dependent tests FALSE-RED
    in the worktree — and those false reds MASK the real failure the full-suite gate
    exists to catch (fb-91c75e51c214: 28 spurious 'Plaid not configured' reds
    hid 1 real regression). So never let it pass silently:

      - WARN (always) about root `.env*` files absent from the worktree — the cause
        of the spurious reds, made visible so they're carried deliberately.
      - LINK (opt-in) the files the project allowlisted in `worktree_env_files` —
        symlinked to the canonical root file (no secret COPY on disk), removing the
        friction for partners who want it. Off by default — prusik never surfaces
        secrets into a worktree unless the project asked.

    Returns the linked filenames (for the ledger)."""
    from prusik import phases
    config = phases.load_sprint_config(root) or {}
    detected = [p.name for p in sorted(root.glob(".env*"))
                if p.is_file() and not (target / p.name).exists()]
    if not detected:
        return []
    patterns = config.get("worktree_env_files")
    allow = set()
    if isinstance(patterns, list):
        for name in detected:
            if any(name == str(pat) or Path(name).match(str(pat))
                   for pat in patterns):
                allow.add(name)
    linked: list[str] = []
    warned: list[str] = []
    for name in detected:
        if name in allow and do_link:
            try:
                (target / name).symlink_to(root / name)
                linked.append(name)
            except OSError:
                warned.append(name)      # link failed → fall back to a warning
        else:
            warned.append(name)
    if linked:
        print(f"[worktree-setup] linked {len(linked)} gitignored env file(s) into "
              f"worktree: {', '.join(linked)} (worktree_env_files)")
    if warned:
        print(f"[worktree-setup] ⚠ {len(warned)} gitignored env file(s) in project "
              f"root are absent from this worktree: {', '.join(warned)}.\n"
              f"  a fresh git worktree does not inherit gitignored files, so "
              f"secret-dependent tests can FALSE-RED here and mask real failures.\n"
              f"  carry them deliberately, or list them under `worktree_env_files` "
              f"in sprint-config to auto-link on setup.", file=sys.stderr)
    return linked


def run(dir_: str | None = None, do_run: bool = False,
        json_output: bool = False, root: Path | None = None) -> int:
    from prusik import detect, ledger
    root = root or ledger.project_root()
    target = Path(dir_).resolve() if dir_ else root
    # fb-7a24578db48e: a `--dir` that doesn't exist yet raised a raw
    # FileNotFoundError from subprocess's cwd= deep in the run loop. Fail CLOSED
    # and VISIBLY instead — worktree-setup PREPS an existing worktree (deps +
    # build); it doesn't create one (that's `git worktree add`).
    if dir_ and not target.is_dir():
        print(f"[worktree-setup] dir does not exist: {target}\n"
              f"  worktree-setup prepares an EXISTING worktree (install + build); "
              f"create it first (e.g. `git worktree add`), then re-run.",
              file=sys.stderr)
        return 2
    cmds = detect.worktree_setup_commands(root)

    if json_output:
        import json
        print(json.dumps({"dir": str(target), "commands": cmds}, indent=2))
        return 0

    # A real worktree (not the partial-mirror root) may be missing gitignored env
    # files → spurious secret-dependent test reds that mask real failures. Warn
    # always; link the allowlisted ones with --run (fb-91c75e51c214).
    linked: list[str] = []
    if target != root:
        linked = _carry_env_files(root, target, do_run)

    if not cmds:
        print("[worktree-setup] no setup needed for this stack "
              "(not a JS/TS workspace).")
        return 0

    if not do_run:
        print(f"# worktree setup for {target} (run with --run):")
        for c in cmds:
            print(c)
        return 0

    for c in cmds:
        print(f"[worktree-setup] $ {c}")
        proc = subprocess.run(["/bin/bash", "-c", c], cwd=str(target),
                              timeout=_SETUP_TIMEOUT_SEC, check=False)
        if proc.returncode != 0:
            print(f"[worktree-setup] FAILED ({proc.returncode}) on: {c} — "
                  f"stopping (worktree is not ready).")
            ledger.append("worktree_setup", dir=str(target), ok=False,
                          failed_on=c)
            return proc.returncode
    ledger.append("worktree_setup", dir=str(target), ok=True, commands=cmds,
                  env_linked=linked)
    print(f"[worktree-setup] worktree ready: {len(cmds)} command(s) ran clean.")
    return 0
