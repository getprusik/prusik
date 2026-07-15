"""fb-87c46ae9348a — shared-test-infra changes force full-tier execution-evidence.

A change to a tier-spanning conftest, an autouse/session-scoped fixture, or a
declared all-tier module has blast radius = every tier, but its production
reverse-dep set is empty so it reads as low-blast. A touched-set/subset green then
hides cross-tier breakage. These lock: (1) the detector fires on the shared-infra
tells and NOT on an ordinary change or a plain function-scoped fixture (the
adversarial no-false-positive case — over-firing would block every sprint); (2)
when it fires, the full-suite gate is MANDATORY even with the advisory config off;
(3) a change that DID prove the full suite still advances.

moat-finding: fb-87c46ae9348a
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from unittest import mock

import yaml

from prusik import gate, shared_infra, suite_baseline
from tests._common import _mktmp_project, _write_ledger


def _git(cwd: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


def _repo_with_worktree(tmp: Path) -> Path:
    _git(tmp, "init", "-q")
    _git(tmp, "config", "user.email", "t@t")
    _git(tmp, "config", "user.name", "t")
    (tmp / "src.py").write_text("x = 1\n")
    (tmp / "tests").mkdir()
    (tmp / "tests" / "conftest.py").write_text(
        "import pytest\n@pytest.fixture(autouse=True, scope='session')\ndef f():\n    ...\n")
    (tmp / "tests" / "unit").mkdir()
    (tmp / "tests" / "unit" / "conftest.py").write_text(
        "import pytest\n@pytest.fixture\ndef g():\n    ...\n")
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-qm", "base")
    (tmp / "worktrees").mkdir()
    _git(tmp, "worktree", "add", "-q", str(tmp / "worktrees" / "solo"), "-b", "solo")
    return tmp / "worktrees" / "solo"


# ── the detector ──────────────────────────────────────────────────────────────

def test_detector_ignores_ordinary_change():
    tmp = _mktmp_project()
    try:
        wt = _repo_with_worktree(tmp)
        (wt / "src.py").write_text("x = 2\n")
        assert shared_infra.touched_shared_test_infra(tmp) == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_detector_fires_on_tier_spanning_conftest():
    tmp = _mktmp_project()
    try:
        wt = _repo_with_worktree(tmp)
        (wt / "tests" / "conftest.py").write_text(
            "import pytest\n@pytest.fixture(autouse=True, scope='session')\n"
            "def f():\n    return 2\n")
        hits = shared_infra.touched_shared_test_infra(tmp)
        assert [h["file"] for h in hits] == ["tests/conftest.py"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_detector_ignores_plain_function_fixture_in_deep_conftest():
    # adversarial: a deep conftest with an ORDINARY function-scoped fixture is not
    # cross-tier shared state — over-firing here would block routine sprints.
    tmp = _mktmp_project()
    try:
        wt = _repo_with_worktree(tmp)
        (wt / "tests" / "unit" / "conftest.py").write_text(
            "import pytest\n@pytest.fixture\ndef g():\n    return 9\n")
        assert shared_infra.touched_shared_test_infra(tmp) == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_detector_fires_on_broad_scope_deep_conftest():
    tmp = _mktmp_project()
    try:
        wt = _repo_with_worktree(tmp)
        (wt / "tests" / "unit" / "conftest.py").write_text(
            "import pytest\n@pytest.fixture(scope='session')\ndef g():\n    return 9\n")
        hits = shared_infra.touched_shared_test_infra(tmp)
        assert [h["file"] for h in hits] == ["tests/unit/conftest.py"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_detector_fires_on_declared_glob():
    tmp = _mktmp_project()
    try:
        wt = _repo_with_worktree(tmp)
        (wt / "src.py").write_text("x = 2\n")
        hits = shared_infra.touched_shared_test_infra(
            tmp, {"shared_test_infra": ["src.py"]})
        assert [h["file"] for h in hits] == ["src.py"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── the gate escalation (detector mocked; git setup covered above) ─────────────

def _setup_gate(proj, *, require, prove_executed, baseline=906):
    os.environ["CLAUDE_PROJECT_DIR"] = str(proj)
    (proj / ".sprint").mkdir(exist_ok=True)
    suite_baseline.update(proj, baseline)
    (proj / ".claude").mkdir(exist_ok=True)
    (proj / ".claude" / "sprint-config.yaml").write_text(
        yaml.safe_dump({"require_full_suite_at_build": require}))
    evts = [{"ts": "2026-06-05T10:00:00+00:00", "event": "phase_advance",
             "to_phase": "building", "feature": "feat"}]
    if prove_executed is not None:
        evts.append({"ts": "2026-06-05T11:00:00+00:00", "event": "prove_run",
                     "kind": "tests", "proven": True, "executed": prove_executed})
    _write_ledger(proj, evts)


_SHARED = [{"file": "tests/conftest.py", "reason": "tier-spanning"}]


def test_shared_infra_forces_block_despite_advisory_off():
    proj = _mktmp_project()
    try:
        _setup_gate(proj, require=False, prove_executed=5)   # advisory OFF, subset prove
        with mock.patch.object(shared_infra, "touched_shared_test_infra",
                               return_value=_SHARED):
            assert gate._full_suite_gate("building", "reviewing", "feat") == 2
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def test_no_shared_infra_stays_advisory_when_off():
    proj = _mktmp_project()
    try:
        _setup_gate(proj, require=False, prove_executed=5)
        with mock.patch.object(shared_infra, "touched_shared_test_infra",
                               return_value=[]):
            assert gate._full_suite_gate("building", "reviewing", "feat") is None
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def test_shared_infra_with_full_suite_proven_advances():
    proj = _mktmp_project()
    try:
        _setup_gate(proj, require=False, prove_executed=906)  # full suite proven
        with mock.patch.object(shared_infra, "touched_shared_test_infra",
                               return_value=_SHARED):
            assert gate._full_suite_gate("building", "reviewing", "feat") is None
    finally:
        shutil.rmtree(proj, ignore_errors=True)
