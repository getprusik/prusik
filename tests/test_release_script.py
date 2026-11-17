"""The engine release is one command whose version bump is the last local edit.

fb-c713dc6530a9: committing the bump makes the installed engine BEHIND the source pin, so
every hook refused and push/release/publish/install had to be relayed to the operator.
scripts/release.sh bumps only after everything else is committed, keeps the bump only on a
green suite, and the refusal for a bumped checkout names the resumable script, never an
editable install of unreleased code.

moat-finding: fb-c713dc6530a9
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from prusik import changelog

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "release.sh"

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="release.sh uses BSD sed -i ''")


def _git(d: Path, *a: str) -> str:
    return subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True,
                          check=True).stdout.strip()


@pytest.fixture
def checkout(tmp_path):
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    co = tmp_path / "co"
    co.mkdir()
    _git(co, "init", "-q", "-b", "main")
    _git(co, "config", "user.email", "t@t"); _git(co, "config", "user.name", "t")
    (co / "prusik").mkdir()
    (co / "prusik" / "__init__.py").write_text('__version__ = "1.0.0"\n')
    (co / "README.md").write_text("pinned to 1 findings\n- uses: getprusik/prusik@v1.0.0\n")
    (co / "prusik" / "_closures.json").write_text('{"fb-a": "1.0.0"}\n')
    (co / "CHANGELOG.md").write_text("# Changelog\n\n## [1.1.0] — a thing\n\n- it\n\n"
                                     "## [1.0.0] — before\n")
    (co / "scripts").mkdir()
    shutil.copy(SCRIPT, co / "scripts" / "release.sh")
    _git(co, "add", "."); _git(co, "commit", "-q", "-m", "init")
    _git(co, "remote", "add", "origin", str(origin)); _git(co, "push", "-q", "origin", "main")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "prusik"                     # bare prusik: a python-shebang console script
    fake.write_text(f"#!{sys.executable}\nprint('prusik 1.0.0')\n")
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}
    return co, env


def _suite(co: Path, rc: int) -> None:
    s = co / "scripts" / "suite.sh"
    s.write_text(f"#!/usr/bin/env bash\nexit {rc}\n")
    s.chmod(0o755)
    _git(co, "add", "scripts/suite.sh"); _git(co, "commit", "-q", "-m", "suite")
    _git(co, "push", "-q", "origin", "main")


def _release(co: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([str(co / "scripts" / "release.sh")], cwd=co, env=env,
                          capture_output=True, text=True, timeout=60)


def test_a_red_suite_reverts_the_bump_and_commits_nothing(checkout):
    co, env = checkout
    _suite(co, 1)
    head = _git(co, "rev-parse", "HEAD")
    p = _release(co, env)
    assert p.returncode != 0 and "bump reverted" in p.stderr
    assert (co / "prusik" / "__init__.py").read_text() == '__version__ = "1.0.0"\n'
    assert (co / "README.md").read_text().startswith("pinned to 1 findings\n- uses: getprusik/prusik@v1.0.0")
    assert _git(co, "rev-parse", "HEAD") == head and _git(co, "status", "--porcelain") == ""


def test_a_dirty_tree_is_refused_before_any_bump(checkout):
    co, env = checkout
    _suite(co, 0)
    (co / "CHANGELOG.md").write_text((co / "CHANGELOG.md").read_text() + "\nuncommitted\n")
    p = _release(co, env)
    assert p.returncode != 0 and "not clean" in p.stderr
    assert (co / "prusik" / "__init__.py").read_text() == '__version__ = "1.0.0"\n'


def test_a_green_suite_commits_exactly_the_bump(checkout, tmp_path):
    co, env = checkout
    _suite(co, 0)
    gh = Path(env["PATH"].split(os.pathsep)[0]) / "gh"    # stop at the first network step
    gh.write_text("#!/usr/bin/env bash\nexit 7\n")
    gh.chmod(0o755)
    _release(co, env)
    assert _git(co, "log", "-1", "--format=%s") == "1.1.0: a thing"
    assert sorted(_git(co, "show", "--name-only", "--format=", "HEAD").split()) == \
        ["README.md", "prusik/__init__.py"]
    assert (co / "README.md").read_text() == "pinned to 1 findings\n- uses: getprusik/prusik@v1.1.0\n"
    assert _git(co, "rev-parse", "origin/main") == _git(co, "rev-parse", "HEAD")  # pushed


def test_closures_stamp_the_pending_changelog_version_before_the_bump(tmp_path):
    """Moat tags and closure rows are release content committed BEFORE the bump, so the
    version they stamp is the top CHANGELOG heading when it is ahead of __init__."""
    (tmp_path / "prusik").mkdir()
    (tmp_path / "prusik" / "__init__.py").write_text('__version__ = "1.0.0"\n')
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [1.0.0] — shipped\n")
    assert changelog.development_version(tmp_path) == "1.0.0"
    (tmp_path / "CHANGELOG.md").write_text("## [1.1.0] — next\n\n## [1.0.0] — shipped\n")
    assert changelog.development_version(tmp_path) == "1.1.0"
    (tmp_path / "prusik" / "__init__.py").write_text('__version__ = "1.1.0"\n')   # bumped
    assert changelog.development_version(tmp_path) == "1.1.0"
    (tmp_path / "CHANGELOG.md").write_text("## [0.9.0] — older\n")              # never lower
    assert changelog.development_version(tmp_path) == "1.1.0"
