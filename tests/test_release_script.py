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


# ── an uncommitted hand-bump is finished, never swallowed (fb-65f7eb5ea11b) ───────────────
# moat-finding: fb-65f7eb5ea11b

_BUMPED_INIT = '__version__ = "1.1.0"\n'
_BUMPED_README = "pinned to 1 findings\n- uses: getprusik/prusik@v1.1.0\n"


def _stop_at_network(env: dict) -> None:
    gh = Path(env["PATH"].split(os.pathsep)[0]) / "gh"
    gh.write_text("#!/usr/bin/env bash\nexit 7\n")
    gh.chmod(0o755)


def _assert_bump_committed(co: Path) -> None:
    assert _git(co, "log", "-1", "--format=%s") == "1.1.0: a thing"
    assert sorted(_git(co, "show", "--name-only", "--format=", "HEAD").split()) == \
        ["README.md", "prusik/__init__.py"]
    assert _git(co, "show", "HEAD:prusik/__init__.py") + "\n" == _BUMPED_INIT
    assert _git(co, "show", "HEAD:README.md") + "\n" == _BUMPED_README
    assert _git(co, "status", "--porcelain") == ""
    assert _git(co, "rev-parse", "origin/main") == _git(co, "rev-parse", "HEAD")


@pytest.mark.parametrize("staged", [False, True])
def test_an_uncommitted_bump_is_released_from_head(checkout, staged):
    co, env = checkout
    _suite(co, 0)
    _stop_at_network(env)
    (co / "prusik" / "__init__.py").write_text(_BUMPED_INIT)
    (co / "README.md").write_text(_BUMPED_README)
    if staged:
        _git(co, "add", "prusik/__init__.py", "README.md")
    p = _release(co, env)
    assert "is not the 1.1.0 bump" not in p.stderr, p.stderr
    _assert_bump_committed(co)


def test_a_partial_hand_bump_is_completed(checkout):
    co, env = checkout
    _suite(co, 0)
    _stop_at_network(env)
    (co / "prusik" / "__init__.py").write_text(_BUMPED_INIT)
    _git(co, "add", "prusik/__init__.py")
    _release(co, env)
    _assert_bump_committed(co)


@pytest.mark.parametrize("where", ["README.md", "CHANGELOG.md"])
def test_an_edit_beyond_the_bump_is_refused_and_preserved(checkout, where):
    co, env = checkout
    _suite(co, 0)
    head = _git(co, "rev-parse", "HEAD")
    (co / "prusik" / "__init__.py").write_text(_BUMPED_INIT)
    if where == "README.md":                            # the bump plus one more line
        (co / where).write_text(_BUMPED_README + "an operator's own line\n")
    else:                                               # another path dirty
        (co / where).write_text((co / where).read_text() + "an operator's own line\n")
    before = {f: (co / f).read_bytes() for f in ("prusik/__init__.py", "README.md", "CHANGELOG.md")}
    p = _release(co, env)
    assert p.returncode != 0 and "not clean" in p.stderr
    assert "bump " not in p.stderr                      # refused before any bump
    assert {f: (co / f).read_bytes() for f in before} == before
    assert _git(co, "rev-parse", "HEAD") == head


def test_a_red_suite_reverts_a_staged_bump_to_head(checkout):
    co, env = checkout
    _suite(co, 1)
    head = _git(co, "rev-parse", "HEAD")
    (co / "prusik" / "__init__.py").write_text(_BUMPED_INIT)
    (co / "README.md").write_text(_BUMPED_README)
    _git(co, "add", "prusik/__init__.py", "README.md")
    p = _release(co, env)
    assert p.returncode != 0 and "bump reverted" in p.stderr, p.stderr
    assert (co / "prusik" / "__init__.py").read_text() == '__version__ = "1.0.0"\n'
    assert _git(co, "show", ":prusik/__init__.py") + "\n" == '__version__ = "1.0.0"\n'
    assert _git(co, "rev-parse", "HEAD") == head and _git(co, "status", "--porcelain") == ""


# ── the publish watch reads the run's conclusion (fb-78e2394070e5) ──────────────────────
# moat-finding: fb-78e2394070e5
# `gh run watch --exit-status` exits non-zero both when the run fails and when gh cannot read
# it (a jobs-API HTTP 500 in the 0.270.0 release), so an unreadable run was reported FAILED.

_FAKE_GH = """#!/usr/bin/env bash
case "$1 $2" in
  "release view") exit 0 ;;
  "run list") echo 42 ;;
  "run view")
    n=$(( $(cat "$GH_READS" 2>/dev/null || echo 0) + 1 )); echo "$n" >"$GH_READS"
    case "$GH_MODE" in
      transient) case "$n" in
                   1|3) echo "failed to get run: HTTP 500" >&2; exit 1 ;;
                   2) echo "in_progress " ;;
                   *) echo "completed success" ;;
                 esac ;;
      failure) echo "completed failure" ;;
      *) echo "failed to get run: HTTP 500" >&2; exit 1 ;;
    esac ;;
  *) exit 9 ;;
esac
"""


def _watch(checkout, tmp_path, mode: str) -> subprocess.CompletedProcess:
    co, env = checkout
    _suite(co, 0)
    bindir = Path(env["PATH"].split(os.pathsep)[0])
    for name, body in (("gh", _FAKE_GH), ("curl", "#!/usr/bin/env bash\necho 200\n"),
                       ("prusik", f"#!{sys.executable}\nprint('prusik 1.1.0')\n")):
        (bindir / name).write_text(body)
        (bindir / name).chmod(0o755)
    env = {**env, "GH_MODE": mode, "GH_READS": str(tmp_path / "reads"),
           "RELEASE_POLL_SECONDS": "0"}
    return _release(co, env)


def test_watch_an_unreadable_read_then_success_passes(checkout, tmp_path):
    p = _watch(checkout, tmp_path, "transient")
    assert p.returncode == 0, p.stderr
    assert "FAILED" not in p.stderr and "released v1.1.0" in p.stderr


def test_watch_a_failed_conclusion_fails(checkout, tmp_path):
    p = _watch(checkout, tmp_path, "failure")
    assert p.returncode != 0
    assert "FAILED: publish run 42 failed (conclusion failure;" in p.stderr, p.stderr


def test_watch_a_run_that_stays_unreadable_is_unknown_not_failed(checkout, tmp_path):
    p = _watch(checkout, tmp_path, "unreadable")
    assert p.returncode != 0
    assert "publish run 42: state unknown" in p.stderr and "rerun" in p.stderr, p.stderr
    assert "failed (" not in p.stderr and "run 42 failed" not in p.stderr
    assert (tmp_path / "reads").read_text().strip() == "10"       # bounded: 10 reads, no more
