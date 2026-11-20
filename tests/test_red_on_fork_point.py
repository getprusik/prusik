"""A RED baseline is the sprint's tests failing on the fork point (fb-1a5daf111f19).

prove-red used to run each verify at the project root, and drive ran it before any builder,
so a new test file was absent and pytest's exit 4 ("file not found") was recorded as a
load-bearing RED. Now, with feature worktrees, prove-red builds a temporary detached worktree
at their recorded fork point, overlays the sprint's changed test files, and runs the verify
there. pytest's exits 3/4/5 are no verdict, not RED.
moat-finding: fb-1a5daf111f19
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from prusik import absence, deferred_tier, gate, ledger, test_reach, worktrees
from prusik.testfiles import is_test_path

F = "feat"
PY = sys.executable
PYTEST = f"{PY} -m pytest -q -p no:cacheprovider"


def _git(d: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True,
                       check=True)
    return p.stdout


def _commit(d: Path, msg: str) -> None:
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", msg)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@e"),
                 ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@e")):
        monkeypatch.setenv(k, v)
    _git(root, "init", "-q", "-b", "main")
    (root / ".gitignore").write_text(".sprint/\nworktrees/\n__pycache__/\n")
    (root / "pkg.py").write_text("def f():\n    return 1\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_old.py").write_text("def test_old():\n    assert True\n")
    for d in ("briefs", ".sprint", ".claude", "worktrees"):
        (root / d).mkdir(exist_ok=True)
    (root / "briefs" / f"{F}.md").write_text("# b\n")
    _commit(root, "base")
    monkeypatch.chdir(root)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    return root


def _criteria(root: Path, verify: str) -> None:
    (root / "briefs" / f"{F}.criteria.yaml").write_text(yaml.safe_dump({
        "schema_version": "1.0", "criteria": [
            {"id": "c1", "description": "d", "verify_command": verify, "kind": "tests",
             "prove_red": True}]}))


def _solo(root: Path, role: str = "solo") -> Path:
    return worktrees.create(root, F, role)


def _prove_red() -> int:
    return gate.prove_red(argparse.Namespace(feature=F, id=None))


def _records(event: str) -> list[dict]:
    return [r for r in ledger.read_all() if r.get("event") == event and r.get("feature") == F]


def _new_behavior(wt: Path) -> None:
    """The sprint's change: pkg.f returns 2, and a new test asserts it."""
    (wt / "pkg.py").write_text("def f():\n    return 2\n")
    (wt / "tests" / "test_new.py").write_text(
        "from pkg import f\n\ndef test_new():\n    assert f() == 2\n")


# (a) ────────────────────────────────────────────────────────────────────────────────────────

def test_new_committed_test_captures_red_on_the_fork_point_and_leaves_main_untouched(repo):
    wt = _solo(repo)
    _new_behavior(wt)
    _commit(wt, "build")
    _criteria(repo, f"{PYTEST} tests/test_new.py")
    status_before = _git(repo, "status", "--porcelain")
    files_before = {p: p.read_bytes() for p in repo.rglob("*")
                    if p.is_file() and ".git" not in p.parts and ".sprint" not in p.parts
                    and "worktrees" not in p.parts}

    assert _prove_red() == 0
    [rec] = _records("criterion_red_baseline")
    assert rec["tree"] == "fork-point"
    assert rec["base"] == worktrees.recorded_base(wt)
    assert rec["test_files"] == ["tests/test_new.py"]
    assert rec["exit_code"] == 1, "the new test FAILS against the base's pkg.f (exit 1)"
    # The PRESERVED targets: no temp worktree is left registered, the root is unchanged.
    listed = _git(repo, "worktree", "list", "--porcelain")
    assert listed.count("worktree ") == 2, listed
    assert _git(repo, "status", "--porcelain") == status_before
    assert {p: p.read_bytes() for p in files_before} == files_before
    assert not (repo / "tests" / "test_new.py").exists()


# (b) ────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("prefix", ["", "uv run "])
def test_a_test_path_that_exists_nowhere_is_no_verdict(repo, prefix):
    wt = _solo(repo)
    _new_behavior(wt)
    _commit(wt, "build")
    # A shell function stands in for uv, so the wrapped form runs without uv installed.
    shim = "uv() { shift; \"$@\"; }; " if prefix else ""
    _criteria(repo, f"{shim}{prefix}{PYTEST} tests/test_nowhere.py")
    assert _prove_red() == 1
    assert _records("criterion_red_baseline") == []
    [nv] = _records("criterion_prove_red_no_verdict")
    assert nv["exit_code"] == 4 and nv["tree"] == "fork-point"
    assert gate.missing_red_baselines(repo, F) == ["c1"]


@pytest.mark.parametrize("cmd, expected", [
    ("pytest -q", True),
    ("py.test tests", True),
    ("python -m pytest tests/x.py", True),
    ("python3.12 -m pytest", True),
    (".venv/bin/python -m pytest tests", True),
    ("uv run python -m pytest tests/x.py -q", True),
    ("uv run --with pytest-xdist pytest -q", True),
    ("poetry run pytest", True),
    ("cd sub && pdm run python -m pytest", True),
    ("env A=1 pytest", True),
    ("python -m unittest", False),
    ("npm test", False),
    ("uv pip install pytest", False),
    ("false", False),
])
def test_the_pytest_matcher_reads_wrapped_forms(cmd, expected):
    assert gate._runs_pytest(cmd) is expected


# (c) ────────────────────────────────────────────────────────────────────────────────────────

def test_a_test_that_passes_on_the_base_is_not_load_bearing(repo):
    wt = _solo(repo)
    (wt / "tests" / "test_new.py").write_text(
        "from pkg import f\n\ndef test_new():\n    assert f() == 1\n")
    _commit(wt, "build")
    _criteria(repo, f"{PYTEST} tests/test_new.py")
    assert _prove_red() == 1
    assert _records("criterion_red_baseline") == []


# (d) ────────────────────────────────────────────────────────────────────────────────────────

def test_worktrees_with_different_fork_points_fail_closed(repo, capsys):
    a = _solo(repo, "solo")
    (repo / "other.py").write_text("x = 1\n")
    _commit(repo, "main moves on")
    b = _solo(repo, "backend-builder")
    for wt in (a, b):
        (wt / "tests" / "test_new.py").write_text("def test_new():\n    assert False\n")
    _criteria(repo, f"{PYTEST} tests/test_new.py")
    assert _prove_red() == 2
    err = capsys.readouterr().err
    assert "different commits" in err
    assert "worktrees/solo" in err and "worktrees/backend-builder" in err
    assert _records("criterion_red_baseline") == []
    assert _records("criterion_prove_red_refused")


def test_a_worktree_with_no_recorded_fork_point_fails_closed(repo):
    wt = _solo(repo)
    _git(repo, "update-ref", "-d", worktrees.base_ref(worktrees.branch_name(F, "solo")))
    (wt / "tests" / "test_new.py").write_text("def test_new():\n    assert False\n")
    _criteria(repo, f"{PYTEST} tests/test_new.py")
    assert _prove_red() == 2
    assert _records("criterion_red_baseline") == []


# (e) ────────────────────────────────────────────────────────────────────────────────────────

PATHS = [
    ("tests/test_x.py", True), ("test/helpers.py", True), ("tests/conftest.py", True),
    ("conftest.py", True), ("test_root.py", True), ("pkg/x_test.py", True),
    ("pkg/sub/tests/data.py", True), ("web/__tests__/a.js", True), ("e2e/login.ts", True),
    ("src/a.test.ts", True), ("src/b.spec.jsx", True), ("src/c.e2e.js", True),
    ("prusik/gate.py", False), ("prusik/testfiles.py", False), ("docs/testing.md", False),
    ("src/contest.py", False), ("latest/x.py", False), ("README.md", False),
]


@pytest.mark.parametrize("rel, expected", PATHS)
def test_every_caller_agrees_with_the_one_rule(rel, expected, tmp_path):
    assert is_test_path(rel) is expected
    assert absence._is_test_path(rel) is expected
    assert test_reach._is_test_file(tmp_path / rel, tmp_path) is expected
    assert deferred_tier._is_test_file(rel) is (expected and rel.endswith(".py"))


@pytest.mark.parametrize("rel, expected", [
    ("prusik/test_reach.py", False),       # a module of a package: source, not a test
    ("prusik/tests/test_x.py", True),      # a test directory wins over the package
    ("prusik/conftest.py", True),
    ("test_root.py", True),                # the root is no package
    ("scripts/test_tool.py", True),        # no __init__.py: a test by name
])
def test_a_name_matched_module_of_a_package_is_not_a_test(rel, expected, tmp_path):
    (tmp_path / "prusik" / "tests").mkdir(parents=True)
    (tmp_path / "prusik" / "__init__.py").write_text("")
    assert is_test_path(rel, tmp_path) is expected
    assert absence._is_test_path(rel, tmp_path) is expected
    assert test_reach._is_test_file(tmp_path / rel, tmp_path) is expected
    assert deferred_tier._is_test_file(rel, tmp_path) is expected


def test_a_source_module_named_like_a_test_is_not_overlaid(repo):
    (repo / "lib").mkdir()
    (repo / "lib" / "__init__.py").write_text("")
    (repo / "lib" / "test_helpers.py").write_text("V = 1\n")
    _commit(repo, "lib")
    wt = _solo(repo)
    (wt / "lib" / "test_helpers.py").write_text("V = 2\n")
    (wt / "tests" / "test_new.py").write_text(
        "from lib.test_helpers import V\n\ndef test_v():\n    assert V == 2\n")
    _criteria(repo, f"{PYTEST} tests/test_new.py")
    assert _prove_red() == 0
    [rec] = _records("criterion_red_baseline")
    assert rec["test_files"] == ["tests/test_new.py"]
    assert rec["exit_code"] == 1, "the base's lib.test_helpers (V = 1) ran, not the sprint's"


# (f) ────────────────────────────────────────────────────────────────────────────────────────

def test_a_test_deleted_on_the_sprint_branch_is_absent_from_the_fork_point_tree(repo):
    wt = _solo(repo)
    (wt / "tests" / "test_old.py").unlink()
    (wt / "tests" / "test_new.py").write_text("def test_new():\n    assert False\n")
    _commit(wt, "build")
    # Passes (not RED) if test_old.py is still there; RED only once it is gone.
    _criteria(repo, f"test -e tests/test_old.py || {PYTEST} tests/test_new.py")
    assert _prove_red() == 0
    [rec] = _records("criterion_red_baseline")
    assert rec["test_files"] == ["tests/test_new.py", "tests/test_old.py"]
    assert (repo / "tests" / "test_old.py").exists(), "the root keeps its copy"


# (g) ────────────────────────────────────────────────────────────────────────────────────────

def test_no_changed_test_file_is_no_verdict_and_the_verify_never_runs(repo, tmp_path):
    wt = _solo(repo)
    (wt / "pkg.py").write_text("def f():\n    return 2\n")
    _commit(wt, "code only")
    marker = tmp_path / "ran"
    _criteria(repo, f"touch {marker}; false")
    assert _prove_red() == 1
    assert not marker.exists()
    [nv] = _records("criterion_prove_red_no_verdict")
    assert nv["reason"] == "no changed test file" and nv["test_files"] == []
    assert _records("criterion_red_baseline") == []


# (h) ────────────────────────────────────────────────────────────────────────────────────────

def test_no_tests_collected_is_no_verdict(repo):
    wt = _solo(repo)
    _new_behavior(wt)
    _commit(wt, "build")
    _criteria(repo, f"{PYTEST} tests/test_new.py -k nothing_matches_this")
    assert _prove_red() == 1
    [nv] = _records("criterion_prove_red_no_verdict")
    assert nv["exit_code"] == 5


# (i) ────────────────────────────────────────────────────────────────────────────────────────

def test_a_collection_error_on_the_new_symbol_is_red(repo):
    wt = _solo(repo)
    (wt / "pkg.py").write_text("def f():\n    return 1\n\ndef g():\n    return 3\n")
    (wt / "tests" / "test_new.py").write_text(
        "from pkg import g\n\ndef test_g():\n    assert g() == 3\n")
    # Uncommitted and untracked changes count as the sprint's too.
    _criteria(repo, f"{PYTEST} tests/test_new.py")
    assert _prove_red() == 0
    [rec] = _records("criterion_red_baseline")
    assert rec["exit_code"] == 2 and rec["tree"] == "fork-point"


# (j) ────────────────────────────────────────────────────────────────────────────────────────

def test_with_no_feature_worktree_red_runs_at_the_root(repo, capsys):
    _criteria(repo, "false")
    assert _prove_red() == 0
    [rec] = _records("criterion_red_baseline")
    assert rec["tree"] == "root" and "base" not in rec
    assert "at the project root" in capsys.readouterr().out


def test_another_features_worktree_is_not_this_features(repo):
    worktrees.create(repo, "other", "solo")
    _criteria(repo, "false")
    assert _prove_red() == 0
    assert _records("criterion_red_baseline")[0]["tree"] == "root"


# (k) ────────────────────────────────────────────────────────────────────────────────────────

def test_drive_captures_red_after_the_builders_and_before_the_suite(monkeypatch):
    from prusik import drive
    calls: list[str] = []

    class Stub(drive._Drive):
        def __init__(self):
            self.f, self.cfg, self.steps, self.root = F, {"suite_command": "true"}, [], Path(".")

        def _worktree(self, role):
            calls.append(f"worktree {role}")

        def _builder_done(self, role, wt):
            return False

        def _spawn(self, role, task):
            calls.append(f"spawn {role}")

        def _built_tip(self, role, wt):
            return None

        def _capture_red(self, phase):
            calls.append("prove-red")

        def _prove_suite(self, builds, suite):
            calls.append("suite")

        def _advance(self, to):
            calls.append(f"advance {to}")

        def _builders(self):
            return ["backend-builder", "frontend-builder"]

    Stub()._solo_execute()
    assert calls == ["worktree solo", "spawn solo-builder", "prove-red", "suite",
                     "advance reviewing"]
    calls.clear()
    Stub()._building()
    assert calls == ["worktree backend-builder", "spawn backend-builder",
                     "worktree frontend-builder", "spawn frontend-builder",
                     "prove-red", "suite", "advance reviewing"]


# (l) ────────────────────────────────────────────────────────────────────────────────────────

def test_two_worktrees_with_differing_copies_of_one_test_fail_closed(repo, capsys):
    a = _solo(repo, "solo")
    b = _solo(repo, "backend-builder")
    (a / "tests" / "test_new.py").write_text("def test_new():\n    assert False\n")
    (b / "tests" / "test_new.py").write_text("def test_new():\n    assert 0\n")
    _criteria(repo, f"{PYTEST} tests/test_new.py")
    assert _prove_red() == 2
    assert "tests/test_new.py differs between" in capsys.readouterr().err
    assert _records("criterion_red_baseline") == []


def test_two_worktrees_with_identical_copies_are_fine(repo):
    a = _solo(repo, "solo")
    b = _solo(repo, "backend-builder")
    for wt in (a, b):
        (wt / "tests" / "test_new.py").write_text("def test_new():\n    assert False\n")
    _criteria(repo, f"{PYTEST} tests/test_new.py")
    assert _prove_red() == 0


# (m) ────────────────────────────────────────────────────────────────────────────────────────

def test_an_internal_error_exit_is_no_verdict(repo):
    wt = _solo(repo)
    _new_behavior(wt)
    _criteria(repo, f"{PYTEST} tests/test_new.py; exit 3")
    assert _prove_red() == 1
    [nv] = _records("criterion_prove_red_no_verdict")
    assert nv["exit_code"] == 3


def test_a_command_not_found_in_the_fork_point_tree_is_no_verdict(repo):
    wt = _solo(repo)
    _new_behavior(wt)
    _criteria(repo, "./no-such-tool tests/test_new.py")
    assert _prove_red() == 1
    [nv] = _records("criterion_prove_red_no_verdict")
    assert nv["exit_code"] == 127
