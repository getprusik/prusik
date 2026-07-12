"""A git-stash "pre-existing" determination is a POINT-IN-TIME run: a test that reads
the wall clock can fail on base at one hour and pass at another, so `prove` can mislabel
a time-of-day flake as pre-existing — and a same-time re-run can't tell them apart
(fb-72ad02292a10). prove now detects clock-dependence and baselines with a loud
TIME-OF-DAY caveat (recorded in the note), steering to a frozen clock / prove-flaky,
rather than a silent clean "pre-existing" claim. Non-clock failures are unaffected.

moat-finding: fb-72ad02292a10
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date

import pytest

from prusik import baseline


def _git(d, *a):
    return subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True)


def _has_git():
    try:
        return subprocess.run(["git", "--version"], capture_output=True).returncode == 0
    except OSError:
        return False


# ---------- unit: the clock detector (cross-language, best-effort) ----------

def test_detects_python_datetime_now(tmp_path):
    (tmp_path / "test_t.py").write_text(
        "import datetime\ndef test_x():\n    assert datetime.datetime.now()\n")
    assert baseline._reads_wall_clock("test_t.py::test_x", tmp_path) is True


def test_detects_date_today(tmp_path):
    (tmp_path / "test_t.py").write_text("from datetime import date\n"
                                        "def t():\n    return date.today()\n")
    assert baseline._reads_wall_clock("test_t.py:2", tmp_path) is True


def test_detects_ts_new_date(tmp_path):
    (tmp_path / "a.test.ts").write_text("it('x', () => { const d = new Date(); })\n")
    assert baseline._reads_wall_clock("a.test.ts", tmp_path) is True


def test_no_clock_is_false(tmp_path):
    (tmp_path / "test_t.py").write_text("def test_x():\n    assert 1 + 1 == 2\n")
    assert baseline._reads_wall_clock("test_t.py::test_x", tmp_path) is False


def test_bare_slug_not_inspectable(tmp_path):
    assert baseline._reads_wall_clock("auth-service-reset", tmp_path) is False


def test_missing_file_is_false(tmp_path):
    assert baseline._reads_wall_clock("tests/nope.py::test_x", tmp_path) is False


# ---------- integration: prove caveats a clock-dependent pre-existing failure --------

pytestmark = pytest.mark.skipif(not _has_git(), reason="git not available")


def _repo(tmp, test_src):
    _git(tmp, "init", "-q")
    _git(tmp, "config", "user.email", "t@t")
    _git(tmp, "config", "user.name", "t")
    (tmp / "test_flaky.py").write_text(test_src)
    (tmp / "feature.py").write_text("x = 1\n")
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-qm", "i")
    (tmp / "feature.py").write_text("x = 2\n")      # the sprint's uncommitted change
    (tmp / ".sprint").mkdir()


def test_clock_dependent_preexisting_gets_caveat(tmp_path):
    _repo(tmp_path, "import datetime\ndef test_flaky():\n"
                    "    assert datetime.datetime.now().year < 2000\n")
    ok, msg = baseline.prove(tmp_path, "test_flaky.py::test_flaky",
                             f"{sys.executable} -m pytest test_flaky.py -q",
                             today=date(2026, 6, 4))
    assert ok, msg
    assert "TIME-OF-DAY" in msg and "prove-flaky" in msg
    assert "CLOCK-DEPENDENT" in baseline.load(tmp_path)[0]["note"]


def test_non_clock_preexisting_has_no_caveat(tmp_path):
    _repo(tmp_path, "def test_flaky():\n    assert False\n")
    ok, msg = baseline.prove(tmp_path, "test_flaky.py::test_flaky",
                             f"{sys.executable} -m pytest test_flaky.py -q",
                             today=date(2026, 6, 4))
    assert ok, msg
    assert "TIME-OF-DAY" not in msg
    assert "CLOCK-DEPENDENT" not in baseline.load(tmp_path)[0]["note"]
