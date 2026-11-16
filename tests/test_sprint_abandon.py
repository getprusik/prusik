"""`prusik gate sprint-abandon`: end a stopped sprint without completing it.

A sprint stopped mid-building held its write rules and push denial over the checkout, so a
release could not be cut until it completed (observed-writes, 2026-11-16).
"""

from __future__ import annotations

import argparse
import subprocess

import pytest

from prusik import effort, fix_round, gate, ledger, main_writer, pause, phases

F = "feat"


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


@pytest.fixture
def proj(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".sprint").mkdir(parents=True)
    monkeypatch.chdir(root)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    (root / "a.txt").write_text("a\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-q", "-m", "init")
    _git(root, "branch", f"sprint/{F}-backend-builder")
    _git(root, "branch", f"sprint/{F}-test-writer")
    _git(root, "branch", "sprint/other-backend-builder")
    ledger.append("sprint_started", feature=F)
    ledger.append("phase_advance", feature=F, from_phase="planning", to_phase="building")
    phases.set_sprint_state({"feature": F, "phase": "building"}, root)
    return root


def _abandon(feature=F, reason="too expensive"):
    return gate.sprint_abandon(argparse.Namespace(feature=feature, reason=reason))


def test_abandon_clears_the_sprint_and_keeps_its_branches(proj, capsys):
    fix_round.start(F, proj)
    pause.pause("break")
    main_writer.check_and_touch(proj, "s-1", feature=F, phase="building")
    assert _abandon() == 0
    assert not phases.current_sprint_state(proj)
    assert fix_round.current_state(proj) is None and not pause.is_paused(proj)
    assert main_writer.load(proj) is None
    (ev,) = [r for r in ledger.read_all() if r["event"] == "sprint_abandoned"]
    assert ev["feature"] == F and ev["phase"] == "building" and ev["reason"] == "too expensive"
    assert ev["branches"] == [f"sprint/{F}-backend-builder", f"sprint/{F}-test-writer"]
    branches = _git(proj, "branch", "--list", "sprint/*")
    assert f"sprint/{F}-backend-builder" in branches and "sprint/other-backend-builder" in branches
    assert not [r for r in ledger.read_all() if r["event"] == "sprint_complete"], \
        "an abandoned sprint never counts as completed"
    # Writes the building phase refused are allowed again.
    config = {"phases": [{"name": "building", "writable": ["prusik/**"]}]}
    assert phases.is_path_writable("README.md", config, None, None, proj)[0]


def test_abandon_closes_the_feature_window(proj):
    assert _abandon() == 0
    windows = effort.phase_windows(ledger.read_all(), F)
    assert windows and all(w["end"] is not None for w in windows)


def test_abandon_refuses_another_feature_and_a_blank_reason(proj):
    assert _abandon(feature="other") == 1
    assert _abandon(reason="  ") == 2
    assert phases.current_sprint_state(proj)["feature"] == F


def test_abandon_refuses_while_a_live_drive_owns_the_sprint(proj):
    main_writer.check_and_touch(proj, "s-1", feature=F, phase="building")
    main_writer.set_driver(proj, "s-1", F)            # this test process is the live driver
    assert _abandon() == 2
    assert phases.current_sprint_state(proj)["feature"] == F
    assert not [r for r in ledger.read_all() if r["event"] == "sprint_abandoned"]
