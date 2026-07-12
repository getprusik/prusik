"""Worktree setup (v0.74.0, field seam #2 — findings #10/#11): the deps-install
+ workspace-build a fresh JS/TS worktree needs before it can typecheck/test."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from prusik import detect


def _tmp():
    return Path(tempfile.mkdtemp(prefix="kit-ws-"))


def test_pnpm_turbo_monorepo_install_then_build():
    d = _tmp()
    try:
        (d / "package.json").write_text('{"name": "x"}')
        (d / "pnpm-workspace.yaml").write_text("packages:\n  - 'packages/*'\n")
        (d / "turbo.json").write_text("{}")
        cmds = detect.worktree_setup_commands(d)
        # fb-1a95785eddf3: build runs via `pnpm exec` so the local turbo
        # binary resolves (bare `turbo` is exit-127 in a fresh worktree).
        assert cmds == ["pnpm install --prefer-offline", "pnpm exec turbo run build"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_pnpm_workspace_without_turbo_uses_recursive_build():
    d = _tmp()
    try:
        (d / "package.json").write_text('{"name": "x"}')
        (d / "pnpm-workspace.yaml").write_text("packages:\n  - 'packages/*'\n")
        cmds = detect.worktree_setup_commands(d)
        assert cmds == ["pnpm install --prefer-offline", "pnpm -r build"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_npm_single_package_install_only():
    d = _tmp()
    try:
        (d / "package.json").write_text('{"name": "x"}')
        (d / "package-lock.json").write_text("{}")
        cmds = detect.worktree_setup_commands(d)
        assert cmds == ["npm ci"]          # no workspaces → no build step
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_npm_workspaces_adds_build():
    d = _tmp()
    try:
        (d / "package.json").write_text('{"name": "x", "workspaces": ["pkgs/*"]}')
        (d / "package-lock.json").write_text("{}")
        cmds = detect.worktree_setup_commands(d)
        assert cmds[0] == "npm ci"
        assert any("--workspaces" in c for c in cmds[1:])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_python_project_no_setup():
    d = _tmp()
    try:
        (d / "pyproject.toml").write_text("[project]\nname='x'\n")
        assert detect.worktree_setup_commands(d) == []   # partial-mirror runs from root
    finally:
        shutil.rmtree(d, ignore_errors=True)
