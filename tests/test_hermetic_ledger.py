"""fb-f3f196654458 — the test suite never touches the real project ledger.

conftest dropped CLAUDE_PROJECT_DIR but left cwd at the repo root, so `ledger.project_root()`
walked up to the engine's own `.sprint/` and every full run appended ~29 synthetic events
(fake `prove_run`s, tiny dep-graphs, tmp briefs) to the real ledger — ~60% of it by 2026-10-26,
misread by the advance-time full-suite advisory as "the proven run executed 7 test(s)".

The fix is at the source (each test starts from its own empty cwd outside the repo, HOME and
tmp_path) plus an in-process detector (a ledger path inside the repo fails the test). The
end-to-end case runs the original offenders from a fake repo's cwd — a copy, so concurrent
real sprint activity can never touch what is compared — and requires its ledger byte-identical.

moat-finding: fb-f3f196654458
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import conftest
from prusik import ledger

REPO = Path(__file__).resolve().parent.parent

# The files that leaked into the real ledger before the fix (measured 2026-10-26).
OFFENDERS = [
    "tests/test_prove.py", "tests/test_fingerprint_auto_refresh.py", "tests/test_serve.py",
    "tests/test_prove_flaky.py", "tests/test_python_from_import_edges.py",
    "tests/test_python_src_layout.py", "tests/test_ci_comment.py",
    "tests/test_false_clean_diagnosis.py",
]
OFFENDERS_PASSING = 97  # their passing count, 2026-10-26: a crashed or empty run must not pass

_CACHES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def _inside(p: Path, root: Path) -> bool:
    return p.resolve().is_relative_to(root.resolve())


# ---- the source fix: a fresh, empty, hermetic cwd -------------------------------------------

def test_cwd_is_empty_and_outside_repo_home_and_tmp_path(tmp_path):
    cwd = Path.cwd()
    assert not _inside(cwd, REPO)
    assert not _inside(cwd, Path(os.environ["HOME"]))
    assert not _inside(cwd, tmp_path) and not _inside(tmp_path, cwd)
    assert list(cwd.iterdir()) == []
    assert ledger.project_root().resolve() == cwd.resolve()


@pytest.mark.parametrize("run", ["first", "second"])
def test_each_test_gets_its_own_fresh_cwd(run):
    """Each run starts empty and leaves a file behind — so a cwd shared between tests shows up
    as the second run's non-empty start. Order-independent: either run alone checks the same."""
    cwd = Path.cwd()
    assert not _inside(cwd, REPO), "never write into the repo — cwd isolation is not in place"
    assert cwd.parent == Path(conftest._CWD_BASE)
    assert list(cwd.iterdir()) == []
    (cwd / "left-behind").write_text(run)


def test_an_explicit_project_dir_is_unaffected(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    ledger.append("probe")
    assert (tmp_path / ".sprint" / "ledger.jsonl").read_text().count('"probe"') == 1


# ---- the detector: a ledger path inside the repo fails the test -----------------------------

def test_guard_covers_the_checkout_and_its_canonical_root():
    roots = {r.resolve() for r in conftest._FORBIDDEN_LEDGER_ROOTS}
    assert REPO.resolve() in roots
    assert ledger._canonical_worktree_root(REPO).resolve() in roots


@pytest.mark.parametrize("style", ["module", "direct-import"])
def test_guard_fires_for_any_import_style_and_writes_nothing(tmp_path, monkeypatch, style):
    fake = tmp_path / "repo"
    (fake / ".sprint").mkdir(parents=True)
    monkeypatch.setattr(conftest, "_FORBIDDEN_LEDGER_ROOTS", (fake,))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(fake))
    if style == "module":
        write = ledger.append
    else:
        from prusik.discovery import append as write  # the `from prusik.ledger import` style
    with pytest.raises(pytest.fail.Exception, match="real project ledger"):
        write("leak")
    with pytest.raises(pytest.fail.Exception, match="real project ledger"):
        ledger.read_all()
    assert not (fake / ".sprint" / "ledger.jsonl").exists()


# ---- end to end: the original offenders, run from a fake repo's cwd -------------------------

def _tree(root: Path) -> dict[str, bytes]:
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _CACHES]
        for fn in filenames:
            p = Path(dirpath) / fn
            out[str(p.relative_to(root))] = p.read_bytes()
    return out


def test_offenders_leave_a_fake_repo_untouched(tmp_path):
    files = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True, check=True).stdout.decode().split("\0")
    fake = tmp_path / "repo"
    for rel in filter(None, files):
        src = REPO / rel
        if src.is_file() and not rel.startswith(".sprint/"):
            (fake / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, fake / rel)
    (fake / ".sprint").mkdir(exist_ok=True)
    (fake / ".sprint" / "ledger.jsonl").write_text('{"event": "sentinel"}\n')
    (fake / ".claude").mkdir(exist_ok=True)
    before = _tree(fake)

    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    proc = subprocess.run([sys.executable, "-m", "pytest", *OFFENDERS, "-q", "-p",
                           "no:cacheprovider"], cwd=fake, env=env, capture_output=True,
                          text=True, timeout=600)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out[-3000:]
    passed = re.search(r"(\d+) passed", out)
    assert passed and int(passed.group(1)) >= OFFENDERS_PASSING, out[-3000:]

    after = _tree(fake)
    assert after[".sprint/ledger.jsonl"] == before[".sprint/ledger.jsonl"], \
        after[".sprint/ledger.jsonl"].decode()[-2000:]
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    assert changed == [], changed
