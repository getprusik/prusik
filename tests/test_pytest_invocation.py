"""fb-c515bcaf0476 — every pytest invocation collects the same suite.

`pytest tests/test_effort.py` failed with `No module named 'tests'`, as did 59 other modules alone,
and `pytest tests` failed 28. Bare `pytest` worked by accident: conftest left the repo while it was
imported, before pytest globbed `testpaths`, so the glob came back empty, pytest walked the whole
repo, and reaching `prusik/` first put the root on `sys.path`.

`-I` keeps cwd off `sys.path` on every supported Python, which is what the console script does.
Plain `python -m pytest` adds cwd, and that hid this bug in CI.

moat-finding: fb-c515bcaf0476
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

_CWD_AT_IMPORT = Path.cwd()
_EMPTY_TESTPATHS = "No files were found in testpaths"


def _collect(*args, cwd=REPO, isolated=True):
    python = [sys.executable, "-I"] if isolated else [sys.executable]
    return subprocess.run(
        [*python, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *args],
        cwd=cwd, capture_output=True, text=True, timeout=120)


def _output(run):
    return run.stdout + run.stderr


def _collected(run):
    return [line for line in run.stdout.splitlines() if "::" in line]


@pytest.mark.parametrize("args", [(), ("tests",), ("tests/test_effort.py",)],
                         ids=["bare", "tests-dir", "one-file"])
def test_each_entry_point_collects_without_cwd_on_sys_path(args):
    run = _collect(*args)
    assert run.returncode == 0, _output(run)
    assert _collected(run)


def test_python_dash_m_still_collects():
    run = _collect("tests/test_effort.py", isolated=False)
    assert run.returncode == 0, _output(run)
    assert _collected(run)


def test_absolute_path_collects_from_outside_the_repo(tmp_path):
    run = _collect(str(REPO / "tests" / "test_effort.py"), cwd=tmp_path)
    assert run.returncode == 0, _output(run)
    assert _collected(run)


def test_bare_run_honours_testpaths():
    run = _collect()
    assert _EMPTY_TESTPATHS not in _output(run)
    outside = [node for node in _collected(run) if not node.startswith("tests/")]
    assert outside == []


def test_decoy_outside_testpaths_is_not_collected(tmp_path):
    """The real pyproject and conftest, in a tree with a sprint worktree in it: the case the
    fallback walk gets wrong, since nothing excludes `worktrees/`."""
    shutil.copy(REPO / "pyproject.toml", tmp_path)
    (tmp_path / "tests").mkdir()
    shutil.copy(REPO / "tests" / "conftest.py", tmp_path / "tests")
    (tmp_path / "tests" / "test_kept.py").write_text("def test_kept():\n    pass\n")
    decoy = tmp_path / "worktrees" / "solo" / "tests"
    decoy.mkdir(parents=True)
    (decoy / "test_decoy.py").write_text("def test_decoy():\n    pass\n")

    run = _collect(cwd=tmp_path)

    assert run.returncode == 0, _output(run)
    assert [node.rsplit("::", 1)[-1] for node in _collected(run)] == ["test_kept"]


def test_modules_are_imported_outside_the_repo():
    """The chdir moved from conftest import to `pytest_configure`: it must still precede
    collection, or a module-level ledger call reaches the real project (fb-f3f196654458)."""
    assert not _CWD_AT_IMPORT.resolve().is_relative_to(REPO)


def test_common_helpers_load_under_one_name():
    bare = re.compile(r"^\s*(from|import) _common\b", re.MULTILINE)
    assert [p.name for p in (REPO / "tests").glob("*.py") if bare.search(p.read_text())] == []
