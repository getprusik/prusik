"""A sprint never moves the version pin; the gate refuses it at the builder's hand-off
(fb-dc6e3239ce99).

The plan told backend-builder to bump `__version__`. The integrator merged it, the checkout went
ahead of the installed engine, and the pin gate refused every Bash, Edit, Write and Stop for the
integrator and the operator. `gate advance` out of building / solo_execute / reviewing now
refuses a worktree whose branch moves the pin, while the shared tree still matches the engine.
The adversarial cases: a pin the RELEASE moved on main after the fork is not the builder's, and
a branch that changes everything but the pin passes.

moat-finding: fb-dc6e3239ce99
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tests._common import _copy_sprint_config, _mktmp_project

from prusik import __version__, gate, gate_class, ledger, phases, pin, worktrees

FEATURE = "feat"
ROLE = "backend-builder"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t",
                    *args], check=True, capture_output=True)


def _commit_all(cwd: Path, msg: str) -> None:
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-q", "-m", msg)


def _source_repo(version: str) -> Path:
    tmp = _mktmp_project()
    (tmp / "pyproject.toml").write_text('[project]\nname = "prusik"\n')
    (tmp / "prusik").mkdir()
    (tmp / "prusik" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (tmp / ".gitignore").write_text("worktrees/\n")
    _git(tmp, "init", "-q", "-b", "main")
    _commit_all(tmp, "init")
    return tmp


def _manifest_project(version: str) -> Path:
    tmp = _mktmp_project()
    _copy_sprint_config(tmp)
    (tmp / pin.MANIFEST_REL).write_text(json.dumps({"template_surface_version": version}))
    (tmp / ".gitignore").write_text("worktrees/\n.sprint/\n")
    _git(tmp, "init", "-q", "-b", "main")
    _commit_all(tmp, "init")
    return tmp


def _worktree(root: Path, role: str = ROLE) -> Path:
    branch = worktrees.branch_name(FEATURE, role)
    wt = root / "worktrees" / role
    _git(root, "worktree", "add", "-q", "-b", branch, str(wt))
    _git(root, "update-ref", worktrees.base_ref(branch), "HEAD")
    return wt


def test_a_branch_that_bumps_the_source_version_is_a_move():
    root = _source_repo("1.2.3")
    try:
        wt = _worktree(root)
        (wt / "prusik" / "__init__.py").write_text('__version__ = "1.3.0"\n')
        _commit_all(wt, "bump")
        moves = pin.sprint_moves(root, FEATURE)
        assert [(m["where"], m["file"], m["from"], m["to"]) for m in moves] == [
            (f"sprint/{FEATURE}-{ROLE}", "prusik/__init__.py", "1.2.3", "1.3.0")]
        assert f"git -C worktrees/{ROLE} checkout" in moves[0]["revert"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_branch_that_leaves_the_pin_alone_is_not_a_move():
    root = _source_repo("1.2.3")
    try:
        wt = _worktree(root)
        (wt / "prusik" / "feature.py").write_text("x = 1\n")
        (wt / "prusik" / "__init__.py").write_text('"""doc"""\n__version__ = "1.2.3"\n')
        _commit_all(wt, "work")
        assert pin.sprint_moves(root, FEATURE) == []
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_release_on_main_after_the_fork_is_not_the_builders_move():
    root = _source_repo("1.2.3")
    try:
        wt = _worktree(root)
        (wt / "prusik" / "feature.py").write_text("x = 1\n")
        _commit_all(wt, "work")
        (root / "prusik" / "__init__.py").write_text('__version__ = "1.2.4"\n')
        _commit_all(root, "release 1.2.4")
        assert pin.sprint_moves(root, FEATURE) == [], \
            "judged against the branch's fork point, not the moving main"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_another_features_branch_is_not_judged():
    root = _source_repo("1.2.3")
    try:
        branch = worktrees.branch_name("other", ROLE)
        wt = root / "worktrees" / ROLE
        _git(root, "worktree", "add", "-q", "-b", branch, str(wt))
        (wt / "prusik" / "__init__.py").write_text('__version__ = "9.9.9"\n')
        _commit_all(wt, "bump")
        assert pin.sprint_moves(root, FEATURE) == []
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_manifest_surface_version_change_is_a_move():
    root = _manifest_project("1.2.3")
    try:
        wt = _worktree(root)
        (wt / pin.MANIFEST_REL).write_text(json.dumps({"template_surface_version": "1.3.0"}))
        _commit_all(wt, "bump")
        moves = pin.sprint_moves(root, FEATURE)
        assert [(m["file"], m["from"], m["to"]) for m in moves] == [
            (".claude/.prusik-manifest.json", "1.2.3", "1.3.0")]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_non_git_mirror_with_a_different_pin_is_a_move():
    root = _mktmp_project()
    try:
        _copy_sprint_config(root)
        (root / pin.MANIFEST_REL).write_text(json.dumps({"template_surface_version": "1.2.3"}))
        mirror = root / "worktrees" / ROLE / ".claude"
        mirror.mkdir(parents=True)
        (mirror / ".prusik-manifest.json").write_text(
            json.dumps({"template_surface_version": "1.3.0"}))
        (root / "worktrees" / "frontend-builder").mkdir()   # no pin file: left alone
        moves = pin.sprint_moves(root, FEATURE)
        assert [(m["where"], m["to"]) for m in moves] == [(f"worktrees/{ROLE}", "1.3.0")]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _advance(phase: str = "reviewing"):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = gate.advance(argparse.Namespace(phase=phase, feature=FEATURE,
                                             allow_rewind=False))
    return rc, out.getvalue() + err.getvalue()


def test_advance_out_of_building_refuses_a_branch_that_moves_the_pin():
    root = _manifest_project(__version__)
    try:
        phases.set_sprint_state({"feature": FEATURE, "phase": "building"}, root)
        wt = _worktree(root)
        (wt / pin.MANIFEST_REL).write_text(json.dumps({"template_surface_version": "99.0.0"}))
        _commit_all(wt, "bump")
        rc, text = _advance()
        assert rc == 2, text
        assert "changes the version pin" in text and f"{__version__} → 99.0.0" in text
        assert phases.current_sprint_state()["phase"] == "building"
        blocked = [e for e in ledger.read_all() if e["event"] == "advance_blocked"]
        assert blocked and gate_class.classify(blocked[-1]) == gate_class.PIN_MOVED
        # The shared tree still matches the engine: the operator can still act.
        assert pin.check(root).status == pin.EQUAL
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_legacy_pin_moved_record_classifies_without_the_field():
    assert gate_class.classify({"event": "advance_blocked",
                                "reason": "pin moved: sprint/x-y"}) == gate_class.PIN_MOVED
