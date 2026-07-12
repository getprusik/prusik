"""Known-failure baselines (v0.73.0, field finding #4) — tolerate a git-stash-PROVEN
pre-existing flake, never launder a new failure. The prove tests use a real
git repo because the laundering-prevention IS the feature."""

from __future__ import annotations

import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path
import sys
import tempfile

from prusik import baseline


# ---------- store / aging / deselect (pure) ----------

def _tmp():
    d = Path(tempfile.mkdtemp(prefix="kit-baseline-"))
    (d / ".sprint").mkdir()
    return d


def test_active_excludes_expired():
    d = _tmp()
    try:
        today = date(2026, 6, 4)
        baseline.add_entry(d, "tests/x.py::a", proven_sha="abc", note="n",
                           days=30, today=today)
        baseline.add_entry(d, "tests/x.py::b", proven_sha="def", note="n",
                           days=30, today=today - timedelta(days=40))  # expired
        act = baseline.active(baseline.load(d), today)
        ids = {e["test"] for e in act}
        assert "tests/x.py::a" in ids
        assert "tests/x.py::b" not in ids        # aged out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_deselect_args_only_active():
    d = _tmp()
    try:
        today = date(2026, 6, 4)
        baseline.add_entry(d, "tests/x.py::a", proven_sha="abc", note="n",
                           days=30, today=today)
        baseline.add_entry(d, "tests/x.py::old", proven_sha="def", note="n",
                           days=1, today=today - timedelta(days=10))
        args = baseline.deselect_args(d, today)
        assert args == ["--deselect", "tests/x.py::a"]   # expired one excluded
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_prune_drops_expired():
    d = _tmp()
    try:
        today = date(2026, 6, 4)
        baseline.add_entry(d, "a", proven_sha="x", note="n", days=30, today=today)
        baseline.add_entry(d, "b", proven_sha="y", note="n", days=1,
                           today=today - timedelta(days=10))
        assert baseline.prune(d, today) == 1
        assert {e["test"] for e in baseline.load(d)} == {"a"}
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------- the integrity core: git-stash proof ----------

def _git(d, *a):
    return subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True)


def _git_repo_with_failing_test_on_head():
    """A repo whose committed test FAILS, plus an uncommitted change. Stashing
    the change → the test still fails on HEAD → pre-existing."""
    d = Path(tempfile.mkdtemp(prefix="kit-bl-git-"))
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t.t")
    _git(d, "config", "user.name", "t")
    (d / "test_flaky.py").write_text("def test_flaky():\n    assert False\n")
    (d / "feature.py").write_text("x = 1\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "init")
    (d / "feature.py").write_text("x = 2\n")       # the sprint's uncommitted change
    (d / ".sprint").mkdir()
    return d


def test_prove_baselines_a_genuinely_preexisting_failure():
    d = _git_repo_with_failing_test_on_head()
    try:
        ok, msg = baseline.prove(
            d, "test_flaky.py::test_flaky",
            f"{sys.executable} -m pytest test_flaky.py -q", today=date(2026, 6, 4))
        assert ok, msg
        assert baseline.load(d)[0]["test"] == "test_flaky.py::test_flaky"
        # the sprint's working change survived the stash/pop
        assert (d / "feature.py").read_text() == "x = 2\n"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_prove_REFUSES_to_launder_a_sprint_introduced_failure():
    """The test passes on HEAD but the sprint's change makes it fail — prove must
    REFUSE to baseline it (this is the whole point of the feature)."""
    d = Path(tempfile.mkdtemp(prefix="kit-bl-git2-"))
    try:
        _git(d, "init", "-q")
        _git(d, "config", "user.email", "t@t.t")
        _git(d, "config", "user.name", "t")
        # committed test PASSES; the working change is unrelated
        (d / "test_ok.py").write_text("def test_ok():\n    assert True\n")
        (d / "feature.py").write_text("x = 1\n")
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "init")
        (d / "feature.py").write_text("x = 2\n")
        (d / ".sprint").mkdir()
        ok, msg = baseline.prove(
            d, "test_ok.py::test_ok",
            f"{sys.executable} -m pytest test_ok.py -q", today=date(2026, 6, 4))
        assert ok is False
        assert "SPRINT" in msg or "not pre-existing" in msg
        assert baseline.load(d) == []          # nothing recorded
        assert (d / "feature.py").read_text() == "x = 2\n"   # change restored
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_prove_refuses_on_clean_tree():
    d = Path(tempfile.mkdtemp(prefix="kit-bl-clean-"))
    try:
        _git(d, "init", "-q")
        _git(d, "config", "user.email", "t@t.t")
        _git(d, "config", "user.name", "t")
        (d / "a.py").write_text("x = 1\n")
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "init")
        (d / ".sprint").mkdir()
        ok, msg = baseline.prove(d, "a::b", "true", today=date(2026, 6, 4))
        assert ok is False and "clean" in msg.lower()
    finally:
        shutil.rmtree(d, ignore_errors=True)
