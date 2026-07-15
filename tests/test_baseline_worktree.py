"""fb-f02412bdfd4d — `gate baseline prove` gave a false PROVEN-pre-existing for a
worktree-scoped `--command`.

The operator ran `prusik gate baseline prove --command "cd worktrees/solo && ..."`
from the MAIN project root. `prove` stashed the MAIN worktree, but the sprint's
dirty changes live in a SEPARATE linked git worktree — stashing main does NOT
touch the solo worktree, so the "baseline" run still carried every change and a
REAL regression was falsely PROVEN pre-existing (2 of 9 in the field). The A/B
stash+run must happen in the tree the command actually runs against.

These lock the integrity core: a real regression in the worktree is REFUSED
(the adversarial case — a false proof would launder a regression into a tolerated
baseline), while a GENUINELY pre-existing worktree failure is still PROVEN (so
the fix doesn't over-correct into refusing legitimate baselines). Both the
auto-detected `cd <worktree>` form and the explicit `worktree=` param are covered.

moat-finding: fb-f02412bdfd4d
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from prusik import baseline

# No .pyc: prove does `git stash push -u`, so an untracked artifact the command
# regenerates would collide on `stash pop` — orthogonal to this finding, avoided
# here so the A/B *logic* is what's under test.
_BARE = 'PYTHONDONTWRITEBYTECODE=1 python3 -c "from calc import add; assert add(2,2)==4"'
_CD = 'cd worktrees/solo && ' + _BARE


def _git(cwd: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


def _repo_with_worktree(tmp_path: Path, base_add: str) -> Path:
    """A sprint root (`.sprint/`, `worktrees/solo` linked worktree) whose base
    `calc.add` body is `base_add`. `.sprint` is committed so the MAIN tree is
    clean — the only dirty changes will be the ones we plant in the worktree."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "calc.py").write_text(f"def add(a, b):\n    return {base_add}\n")
    (root / ".sprint").mkdir()
    (root / ".sprint" / "state.json").write_text("{}")
    _git(root, "add", "calc.py", ".sprint")
    _git(root, "commit", "-qm", "base")
    _git(root, "worktree", "add", "-q", str(root / "worktrees" / "solo"), "-b", "solo")
    return root


def _plant(root: Path, add_body: str) -> None:
    (root / "worktrees" / "solo" / "calc.py").write_text(
        f"def add(a, b):\n    return {add_body}\n")


# ── the adversarial case: a real regression must NOT be baselined ──────────────

def test_real_regression_in_worktree_is_refused_via_cd_detection(tmp_path):
    # add() is correct on base; the worktree introduces a regression → the test
    # fails ONLY because of the sprint's change. A correct prove refuses.
    root = _repo_with_worktree(tmp_path, "a + b")
    _plant(root, "a + b - 1")
    ok, msg = baseline.prove(root, "test_add", _CD)
    assert ok is False, f"real regression falsely PROVEN pre-existing: {msg}"
    assert "PASSED on HEAD" in msg
    assert not baseline.load(root), "a refused proof must not write a baseline entry"


def test_real_regression_in_worktree_is_refused_via_explicit_param(tmp_path):
    root = _repo_with_worktree(tmp_path, "a + b")
    _plant(root, "a + b - 1")
    ok, msg = baseline.prove(root, "test_add", _BARE,
                             worktree=Path("worktrees/solo"))
    assert ok is False, f"real regression falsely PROVEN pre-existing: {msg}"
    assert "PASSED on HEAD" in msg
    assert not baseline.load(root)


# ── the preserved case: a genuine pre-existing failure must still be tolerated ─

def test_genuine_pre_existing_worktree_failure_is_proven_via_cd(tmp_path):
    # add() is ALREADY broken on base; the worktree only adds an unrelated edit →
    # the failure is genuinely pre-existing and must be baselined.
    root = _repo_with_worktree(tmp_path, "a + b - 1")
    _plant(root, "a + b - 1  # unrelated sprint edit")
    ok, msg = baseline.prove(root, "test_add", _CD)
    assert ok is True, f"genuine pre-existing failure wrongly refused: {msg}"
    assert baseline.load(root), "a proven baseline must be recorded"


def test_genuine_pre_existing_worktree_failure_is_proven_via_explicit_param(tmp_path):
    root = _repo_with_worktree(tmp_path, "a + b - 1")
    _plant(root, "a + b - 1  # unrelated sprint edit")
    ok, msg = baseline.prove(root, "test_add", _BARE,
                             worktree=Path("worktrees/solo"))
    assert ok is True, f"genuine pre-existing failure wrongly refused: {msg}"
    assert baseline.load(root)


# ── the resolver in isolation ─────────────────────────────────────────────────

def test_resolver_targets_linked_worktree_from_cd(tmp_path):
    root = _repo_with_worktree(tmp_path, "a + b")
    target, note = baseline._resolve_target_tree(root, _CD, None)
    assert target.resolve() == (root / "worktrees" / "solo").resolve()
    assert note and "worktree" in note


def test_resolver_defaults_to_root_without_cd_or_param(tmp_path):
    root = _repo_with_worktree(tmp_path, "a + b")
    target, note = baseline._resolve_target_tree(root, _BARE, None)
    assert target == root
    assert note is None


# ── fb-99ca849e2189: a proof that writes files must never strand the sprint ────
#
# prove does `git stash push -u` then runs the command on HEAD. A command that
# rewrites a file the sprint owns used to collide on `stash pop` and leave the
# sprint's real changes STRANDED in the stash. The adversarial assertion is that
# BOTH a tracked change and an untracked file the sprint owns are PRESERVED after
# a proof whose command mutates both.
#
# moat-finding: fb-99ca849e2189

# ── fb-80d0a26be528: the environment-gap category (machine-verified) ──────────
#
# A failure caused by a gitignored fixture/asset absent from the worktree fails on
# the worktree's base at EVERY commit (the fixture is never committed), so a
# worktree-only A/B mislabels it "pre-existing" and tolerates it for 30 days. The
# machine discriminator is: the same check PASSES at the canonical project root,
# where the fixture lives on disk. prove must tag it ENVIRONMENT-GAP, not
# pre-existing — the adversarial assertion is that a real env issue is NOT laundered
# into a tolerated pre-existing baseline (which would mask the fixable cause and
# hide the lost coverage).
#
# moat-finding: fb-80d0a26be528

def _repo_with_gitignored_fixture(tmp_path: Path) -> Path:
    """`check.py` reads a gitignored `fixture.txt` present at the root on disk but
    never committed — so a linked worktree checkout lacks it."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / ".gitignore").write_text("fixture.txt\n")
    (root / "check.py").write_text(
        'def ok():\n    return open("fixture.txt").read().strip() == "ok"\n')
    (root / ".sprint").mkdir()
    (root / ".sprint" / "state.json").write_text("{}")
    _git(root, "add", ".gitignore", "check.py", ".sprint")
    _git(root, "commit", "-qm", "base")
    (root / "fixture.txt").write_text("ok\n")            # gitignored, root-only
    _git(root, "worktree", "add", "-q", str(root / "worktrees" / "solo"), "-b", "solo")
    (root / "worktrees" / "solo" / "check.py").write_text(  # a dirty sprint edit
        'def ok():\n    return open("fixture.txt").read().strip() == "ok"\n# edit\n')
    return root


_CHECK = 'python3 -c "from check import ok; assert ok()"'


def test_gitignored_fixture_absent_in_worktree_is_env_gap_not_pre_existing(tmp_path):
    root = _repo_with_gitignored_fixture(tmp_path)
    ok, msg = baseline.prove(root, "test_ok", _CHECK,
                             worktree=Path("worktrees/solo"))
    assert ok is True, msg
    assert "ENVIRONMENT-GAP" in msg, msg
    ent = next((e for e in baseline.load(root) if e["test"] == "test_ok"), None)
    assert ent and ent["kind"] == "env-gap", ent
    # adversarial: it must NOT be recorded as a plain pre-existing baseline
    assert ent["kind"] != "pre-existing"


def test_env_gap_detection_via_cd_form(tmp_path):
    root = _repo_with_gitignored_fixture(tmp_path)
    cmd = "cd worktrees/solo && " + _CHECK
    ok, msg = baseline.prove(root, "test_ok", cmd)
    assert ok is True and "ENVIRONMENT-GAP" in msg, msg


def test_genuine_pre_existing_is_not_misclassified_as_env_gap(tmp_path):
    # The failure reproduces at the ROOT too → genuinely pre-existing, NOT env-gap.
    root = _repo_with_worktree(tmp_path, "a + b - 1")          # broken at base
    _plant(root, "a + b - 1  # unrelated edit")
    ok, msg = baseline.prove(root, "test_add", _BARE,
                             worktree=Path("worktrees/solo"))
    assert ok is True, msg
    assert "ENVIRONMENT-GAP" not in msg, msg
    ent = next((e for e in baseline.load(root) if e["test"] == "test_add"), None)
    assert ent and ent["kind"] == "pre-existing", ent


def test_prove_preserves_sprint_changes_when_command_writes_files(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "tracked.py").write_text("x = 1\n")
    _git(root, "add", "tracked.py")
    _git(root, "commit", "-qm", "base")
    # the sprint owns a tracked edit AND an untracked file
    (root / "tracked.py").write_text("x = 2\n")
    (root / "out.txt").write_text("sprint-version\n")
    # a failing command that clobbers BOTH paths (a real test byproduct pattern)
    cmd = "echo command-version > out.txt; echo x=999 > tracked.py; exit 1"
    ok, msg = baseline.prove(root, "t", cmd)
    assert ok is True, msg                       # exit 1 fails on HEAD → pre-existing
    # the sprint's work survived intact, not the command's throwaway output
    assert (root / "out.txt").read_text().strip() == "sprint-version"
    assert (root / "tracked.py").read_text().strip() == "x = 2"
    # and nothing is left stranded in the stash
    stash = subprocess.run(["git", "-C", str(root), "stash", "list"],
                           capture_output=True, text=True).stdout.strip()
    assert stash == "", f"sprint changes stranded in stash: {stash}"
