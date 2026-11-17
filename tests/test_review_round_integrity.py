"""A review round judges what the builder changed, fixes route for every lane, and a slow
suite is not a failing one.

- fb-109c7034f558: the change set diffed a worktree against its own HEAD, so committed
  builder work was invisible to the boundary gate and blast-radius checks.
- fb-e4f46ed05574: a solo sprint's fix round escalated spawn_failed; a reviewer's
  `find worktrees/` listed caches another reviewer's run wrote.
- fb-32d2fbb70859: a hardcoded 300s verify timeout read as a red run, and prove-red
  captured it as a RED baseline.

moat-finding: fb-109c7034f558
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from prusik import consistency, criterion, fix_round, gate, ledger, phases, schema
from prusik import worktrees as wtmod
from tests.test_sprint_drive import F, escalated_events, proj, rig, set_phase  # noqa: F401
from tests.test_sprint_drive import write_report

FEAT = "feat"
BUILDER = "backend-builder"


# ── real git fixtures ──────────────────────────────────────────────────────────────────────

def _git(d: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _commit(d: Path, rel: str, text: str | None = "x = 1\n", msg: str | None = None) -> str:
    """Commit `rel` in `d`, writing `text` first unless it is None (commit as it is)."""
    p = d / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if text is not None:
        p.write_text(text)
    _git(d, "add", rel)
    _git(d, "commit", "-q", "-m", msg or f"add {rel}")
    return _git(d, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    (root / ".gitignore").write_text("__pycache__/\n*.log\n.sprint/\nworktrees/\n")
    _commit(root, ".gitignore", None)
    _commit(root, "src/a.py")
    for d in (".sprint", ".claude", "design/feat", "worktrees"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "design/feat/scope.md").write_text("## Modules touched\n- `src/`\n")
    monkeypatch.chdir(root)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    return root


def _create(root: Path, role: str = BUILDER) -> Path:
    return wtmod.create(root, FEAT, role)


def _base(root: Path, role: str = BUILDER) -> str:
    return _git(root, "rev-parse", f"refs/prusik/base/sprint/{FEAT}-{role}")


def _changed(root: Path, role: str = BUILDER) -> list[str]:
    return consistency.builder_change_set(root / "worktrees" / role, root)


def _changed_files_cli(capsys) -> tuple[int, list[str], str]:
    rc = gate.changed_files(argparse.Namespace(feature=FEAT))
    out = capsys.readouterr()
    return rc, out.out.splitlines(), out.err


# ── one builder change set ─────────────────────────────────────────────────────────────────

def test_committed_out_of_scope_file_fails_the_boundary(repo):
    """Before: the diff against the worktree's own HEAD was empty after the builder's
    commit, so the boundary passed an out-of-scope file."""
    wt = _create(repo)
    _commit(wt, "src/b.py")                          # in scope
    _commit(wt, "rogue/y.py")                        # out of scope, committed
    v = "\n".join(consistency.builder_writes_within_plan(repo, FEAT))
    assert f"{BUILDER}/rogue/y.py" in v
    assert "src/b.py" not in v
    assert consistency.sprint_changed_files(repo) == {"src/b.py", "rogue/y.py"}


def test_change_set_has_committed_staged_unstaged_untracked_never_ignored(
        repo, capsys):
    wt = _create(repo)
    _commit(wt, "src/committed.py")
    (wt / "src/staged.py").write_text("s\n")
    _git(wt, "add", "src/staged.py")
    (wt / "src/a.py").write_text("changed, unstaged\n")
    (wt / "src/untracked.py").write_text("u\n")
    (wt / "src/__pycache__").mkdir()
    (wt / "src/__pycache__/a.cpython-313.pyc").write_bytes(b"\0")
    (wt / "run.log").write_text("noise\n")
    changed = _changed(repo)
    assert changed == ["src/a.py", "src/committed.py", "src/staged.py", "src/untracked.py"]
    rc, lines, _ = _changed_files_cli(capsys)
    assert rc == 0
    assert f"worktrees/{BUILDER}/src/committed.py" in lines
    assert f"worktrees/{BUILDER}/src/staged.py" in lines
    assert f"worktrees/{BUILDER}/src/untracked.py" in lines
    assert not [ln for ln in lines if "__pycache__" in ln or ln.endswith(".log")]


def test_a_deleted_file_is_in_the_change_set(repo):
    wt = _create(repo)
    _git(wt, "rm", "-q", "src/a.py")
    _git(wt, "commit", "-q", "-m", "drop a")
    assert _changed(repo) == ["src/a.py"]


def test_shared_tree_commit_before_the_fork_is_not_builder_work_and_merge_keeps_it(repo):
    """The sprint-start base would count this repo's mid-sprint `chore(findings)` commit as
    builder work; a merge-base with HEAD would empty after integration."""
    _commit(repo, "findings/fb-x.json", "{}\n", "chore(findings): file")   # after start
    wt = _create(repo)
    assert _base(repo) == _git(repo, "rev-parse", "HEAD")
    _commit(wt, "src/b.py")
    assert _changed(repo) == ["src/b.py"]
    _git(repo, "merge", "-q", "--no-ff", "-m", "integrate", f"sprint/{FEAT}-{BUILDER}")
    assert _changed(repo) == ["src/b.py"], "integration must not empty the change set"


def test_same_sprint_retry_refork_after_the_shared_tree_advanced(repo):
    _create(repo)
    old = _base(repo)
    _git(repo, "worktree", "remove", f"worktrees/{BUILDER}")
    head = _commit(repo, "src/later.py")             # the shared tree moves on
    wt = _create(repo)                               # branch has no commits: -B re-fork
    assert _base(repo) == head != old
    _commit(wt, "src/b.py")
    assert _changed(repo) == ["src/b.py"]


def test_later_sprint_reusing_the_feature_name_measures_from_the_new_fork(repo):
    wt = _create(repo)
    _commit(wt, "src/b.py")
    _git(repo, "merge", "-q", "--no-ff", "-m", "integrate", f"sprint/{FEAT}-{BUILDER}")
    _git(repo, "worktree", "remove", f"worktrees/{BUILDER}")
    head = _commit(repo, "src/next.py")
    wt = _create(repo)                               # merged branch: -B re-fork
    assert _base(repo) == head
    assert _changed(repo) == []
    _commit(wt, "src/c.py")
    assert _changed(repo) == ["src/c.py"], "the old sprint's src/b.py is not this one's"


def test_a_reused_branch_keeps_its_base_and_its_commits(repo):
    """What must survive a retry: the builder's committed work and the fork it began at."""
    wt = _create(repo)
    base = _base(repo)
    _commit(wt, "src/b.py")
    _git(repo, "worktree", "remove", f"worktrees/{BUILDER}")
    _commit(repo, "src/later.py")
    wt = _create(repo)
    assert _base(repo) == base
    assert (wt / "src/b.py").is_file()
    assert _changed(repo) == ["src/b.py"]


def test_the_existing_dir_path_never_moves_the_base(repo):
    _create(repo)
    base = _base(repo)
    _commit(repo, "src/later.py")
    _create(repo)                                    # a drive re-run: dir exists
    assert _base(repo) == base


def test_detached_head_worktree_is_refused(repo, capsys):
    _git(repo, "worktree", "add", "-q", "--detach", "worktrees/loose", "HEAD")
    with pytest.raises(consistency.UnmeasuredChangeSet, match="gate worktree-add"):
        _changed(repo, "loose")
    v = "\n".join(consistency.builder_writes_within_plan(repo, FEAT))
    assert "unmeasured" in v and "detached HEAD" in v
    rc, lines, err = _changed_files_cli(capsys)
    assert rc == 2 and lines == [] and "gate worktree-add" in err


def test_hand_made_worktree_is_refused_not_diffed_against_head(repo, capsys):
    _git(repo, "worktree", "add", "-q", "-b", "sprint/feat-hand", "worktrees/hand", "HEAD")
    _commit(repo / "worktrees/hand", "rogue/y.py")
    with pytest.raises(consistency.UnmeasuredChangeSet) as e:
        _changed(repo, "hand")
    assert "gate worktree-add" in e.value.reason and "sprint/feat-hand" in e.value.reason
    v = consistency.builder_writes_within_plan(repo, FEAT)
    assert v and "gate worktree-add" in v[0], "a refusal blocks; it is never a pass"
    with pytest.raises(consistency.UnmeasuredChangeSet):
        consistency.sprint_changed_files(repo)
    # Recording it measures the builder's commit (merge-base before integration).
    assert gate.worktree_add(argparse.Namespace(feature=FEAT, role="hand")) == 0
    capsys.readouterr()
    assert _changed(repo, "hand") == ["rogue/y.py"]


def test_recording_a_merged_hand_made_branch_is_refused(repo, capsys):
    _git(repo, "worktree", "add", "-q", "-b", "sprint/feat-old", "worktrees/old", "HEAD")
    _commit(repo, "src/later.py")                    # branch tip is now a strict ancestor
    assert gate.worktree_add(argparse.Namespace(feature=FEAT, role="old")) == 2
    err = capsys.readouterr().err
    assert "contained in HEAD" in err and "-B" in err
    with pytest.raises(consistency.UnmeasuredChangeSet):
        _changed(repo, "old")


def test_gate_worktree_add_creates_and_records(repo, capsys):
    assert gate.worktree_add(argparse.Namespace(feature=FEAT, role="solo")) == 0
    assert "fork point" in capsys.readouterr().out
    assert _git(repo / "worktrees/solo", "rev-parse", "--abbrev-ref", "HEAD") == \
        f"sprint/{FEAT}-solo"
    assert _base(repo, "solo") == _git(repo, "rev-parse", "HEAD")
    ev = [r for r in ledger.read_all() if r["event"] == "worktree_created"]
    assert ev and ev[-1]["role"] == "solo" and ev[-1]["base"] == _base(repo, "solo")


def test_partial_mirror_excludes_caches_and_gitignored_files(repo):
    mirror = repo / "worktrees" / "solo"                   # not a git worktree
    (mirror / "src/__pycache__").mkdir(parents=True)
    (mirror / "src/x.py").write_text("x = 1\n")
    (mirror / "src/__pycache__/x.cpython-313.pyc").write_bytes(b"\0")
    (mirror / ".ruff_cache").mkdir()
    (mirror / ".ruff_cache/CACHEDIR.TAG").write_text("t\n")
    (mirror / "debug.log").write_text("root .gitignore ignores *.log\n")
    assert not consistency.is_git_worktree_root(mirror)
    assert _changed(repo, "solo") == ["src/x.py"]


def test_assembly_root_test_never_resolves_a_base(repo):
    """A hand-made git worktree is skipped by assembly (the root test), not refused."""
    _git(repo, "worktree", "add", "-q", "--detach", "worktrees/loose", "HEAD")
    assert list(consistency._assemblable_worktree_files(repo)) == []


def test_base_refs_of_deleted_branches_are_pruned_and_live_ones_kept(repo):
    _create(repo)
    _create(repo, "test-writer")
    _git(repo, "worktree", "remove", f"worktrees/{BUILDER}")
    _git(repo, "branch", "-D", f"sprint/{FEAT}-{BUILDER}")
    assert wtmod.prune_base_refs(repo) == [f"sprint/{FEAT}-{BUILDER}"]
    assert _base(repo, "test-writer")                     # its branch still exists


def test_one_change_set_definition_and_no_head_diff_path():
    src = Path(consistency.__file__).read_text()
    assert len(re.findall(r"^def builder_change_set\(", src, re.M)) == 1
    pkg = Path(consistency.__file__).parent
    for py in pkg.rglob("*.py"):
        text = py.read_text()
        assert "_git_worktree_changed_files" not in text, py
        assert not re.search(r"diff --name-only HEAD\b", text), py
        assert not re.search(r"""["']--name-only["'],\s*["']HEAD["']""", text), py
    for name in ("sprint_changed_files", "builder_writes_within_plan"):
        body = src[src.index(f"def {name}("):]
        body = body[:body.index("\ndef ", 1)]
        assert "worktree_change_sets(" in body and "rglob(" not in body, name


def test_unmeasured_change_set_refuses_the_gating_callers(repo, capsys):
    _git(repo, "worktree", "add", "-q", "--detach", "worktrees/loose", "HEAD")
    from prusik import absence, blast_plan, ui_coverage
    assert absence.run(FEAT) == 2
    assert ui_coverage.run(FEAT) == 2
    assert blast_plan.verify_run(FEAT, repo) == 2
    err = capsys.readouterr().err
    assert err.count("gate worktree-add") >= 3


def test_map_staleness_reports_an_unmeasured_change_set(repo, monkeypatch, capsys):
    _git(repo, "worktree", "add", "-q", "--detach", "worktrees/loose", "HEAD")
    monkeypatch.setattr(gate, "_close_gate", lambda f, p: None)
    monkeypatch.setattr(gate, "_run_success_criteria", lambda f, r: (True, []))
    phases.set_sprint_state({"phase": "integrating", "feature": FEAT})
    ledger.append("sprint_started", feature=FEAT)
    gate.sprint_complete(argparse.Namespace(feature=FEAT, duration_min=1, tokens=None,
                                            escalated=False))
    assert "map re-verify UNMEASURED" in capsys.readouterr().out
    ev = [r for r in ledger.read_all() if r["event"] == "map_staleness_unmeasured"]
    assert ev and "detached HEAD" in ev[-1]["reason"]


# ── a solo fix round hands back ────────────────────────────────────────────────────────────

def _solo(root: Path) -> None:
    ledger.append("phase_advance", from_phase="scoping", to_phase="solo_execute", feature=F)


def test_solo_fail_hands_back_and_the_rerun_ends_the_round(rig, proj):
    """plan.md still names a builder: its presence is not the solo signal."""
    _solo(proj)
    set_phase(proj, "reviewing")
    verdicts = iter(["FAIL\n- bug\n", "PASS\n"])
    rig.adapter.scripts["regression-sentinel"] = lambda req: (
        proj / "reports/feat/regression.txt").write_text(next(verdicts))
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "solo_handback" and esc["phase"] == "reviewing"
    assert "worktrees/solo/" in esc["reason"] and f"prusik drive {F}" in esc["reason"]
    assert esc["evidence"][0].endswith("reports/feat/regression.txt")
    assert (fix_round.current_state(proj) or {}).get("feature") == F, "round is open"
    assert ("spawn", "backend-builder") not in rig.events
    rig.events.clear()
    assert rig.run() == 0
    gates = [e[1:3] for e in rig.events if e[:2] == ("gate", "fix-round")]
    assert gates == [("fix-round", "end")], "ends the open round, starts none"
    assert ("spawn", "backend-builder") not in rig.events
    assert ("spawn", "regression-sentinel") in rig.events
    assert [e["kind"] for e in escalated_events()] == ["solo_handback"], "no second hand-back"


def test_a_solo_advance_from_an_earlier_sprint_does_not_make_this_one_solo(rig, proj):
    _solo(proj)
    ledger.append("sprint_started", feature=F)         # a new sprint since
    (proj / "design/feat/plan.md").write_text(
        (proj / "design/feat/plan.md").read_text().replace("**backend-builder**",
                                                           "**scribe**"))
    set_phase(proj, "reviewing")
    rig.adapter.scripts["regression-sentinel"] = write_report(proj, "regression.txt",
                                                              "FAIL\n")
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "spawn_failed" and "no builder role" in esc["reason"]


def test_triage_solo_creates_the_recorded_solo_worktree(rig, proj, monkeypatch):
    from tests.test_drive_design_phases import _fake_triage
    _git(proj, "init", "-q", "-b", "main")
    _git(proj, "config", "user.email", "t@example.invalid")
    _git(proj, "config", "user.name", "t")
    (proj / ".gitignore").write_text(".sprint/\nworktrees/\nreports/\n")
    _commit(proj, ".gitignore", None)
    set_phase(proj, "triage")
    _fake_triage(monkeypatch, proj, {"mode": "solo", "reason": "r"})
    assert rig.run() == 3
    assert rig.escalation()["kind"] == "solo_handback"
    assert _git(proj / "worktrees/solo", "rev-parse", "--abbrev-ref", "HEAD") == \
        f"sprint/{F}-solo"
    assert _git(proj, "rev-parse", f"refs/prusik/base/sprint/{F}-solo") == \
        _git(proj, "rev-parse", "HEAD")


# ── a per-criterion timeout, validated ─────────────────────────────────────────────────────

def _criteria(root: Path, entries: list[dict]) -> Path:
    (root / "briefs").mkdir(exist_ok=True)
    (root / "briefs" / f"{FEAT}.md").write_text("## Goal\nx\n")
    path = root / "briefs" / f"{FEAT}.criteria.yaml"
    path.write_text(yaml.safe_dump({"schema_version": "1.0", "criteria": entries}))
    return path


@pytest.fixture
def plain(tmp_path, monkeypatch):
    for d in (".sprint", ".claude"):
        (tmp_path / d).mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize("bad", [0, -1, "600", True, 7201])
def test_invalid_timeout_sec_is_rejected(plain, bad, capsys):
    path = _criteria(plain, [{"id": "slow", "description": "d", "verify_command": "true",
                              "timeout_sec": bad, "prove_red": True}])
    ok, errors = schema.validate_criteria_file(path, plain)
    assert not ok and any("slow" in e and "timeout_sec" in e for e in errors)
    assert gate.prove_red(argparse.Namespace(feature=FEAT, id=None)) == 2
    assert "slow" in capsys.readouterr().err
    with pytest.raises(gate.CriterionTimeoutInvalid):
        gate.criterion_timeout({"id": "slow", "timeout_sec": bad})


@pytest.mark.parametrize("good", [1, 7200])
def test_valid_timeout_sec_bounds_are_accepted(plain, good):
    path = _criteria(plain, [{"id": "c", "description": "d", "verify_command": "true",
                              "timeout_sec": good}])
    assert schema.validate_criteria_file(path, plain) == (True, [])
    assert gate.criterion_timeout({"id": "c", "timeout_sec": good}) == good
    assert gate.criterion_timeout({"id": "c"}) == 300


SLEEPER = {"id": "slow", "description": "d", "verify_command": "sleep 5",
           "timeout_sec": 1, "prove_red": True}


def test_a_timeout_is_recorded_timed_out_and_shown_as_timeout(plain, monkeypatch, capsys):
    _criteria(plain, [SLEEPER, {"id": "red", "description": "d", "verify_command": "false"}])
    monkeypatch.setattr(gate, "_close_gate", lambda f, p: None)
    phases.set_sprint_state({"phase": "integrating", "feature": FEAT})
    ledger.append("sprint_started", feature=FEAT)
    rc = gate.sprint_complete(argparse.Namespace(feature=FEAT, duration_min=1, tokens=None,
                                                 escalated=False))
    assert rc == 1, "a timeout still fails closed"
    err = capsys.readouterr().err
    slow = next(ln for ln in err.splitlines() if "- slow:" in ln)
    red = next(ln for ln in err.splitlines() if "- red:" in ln)
    assert "TIMEOUT after 1s" in slow and "FAIL" not in slow
    assert "FAIL exit=1" in red and "TIMEOUT" not in red
    rec = [r for r in ledger.read_all() if r["event"] == "success_criterion_verified"
           and r["id"] == "slow"][-1]
    assert rec["timed_out"] is True and rec["timeout_sec"] == 1 and rec["passed"] is False
    out = (plain / "reports" / FEAT / "verify-slow.txt").read_text()
    assert "1s" in out and "timeout_sec" in out
    red_rec = [r for r in ledger.read_all() if r["event"] == "success_criterion_verified"
               and r["id"] == "red"][-1]
    assert "timed_out" not in red_rec


def test_prove_red_never_records_a_timeout_as_a_red_baseline(plain, capsys):
    _criteria(plain, [SLEEPER])
    assert gate.prove_red(argparse.Namespace(feature=FEAT, id=None)) != 0
    err = capsys.readouterr().err
    assert "TIMEOUT" in err and "timeout_sec" in err
    assert not [r for r in ledger.read_all() if r["event"] == "criterion_red_baseline"]
    assert gate.missing_red_baselines(plain, FEAT) == ["slow"]


def test_prove_red_still_captures_a_real_red(plain):
    """What must survive: a verify that genuinely fails is still a RED baseline."""
    _criteria(plain, [{**SLEEPER, "verify_command": "false"}])
    assert gate.prove_red(argparse.Namespace(feature=FEAT, id=None)) == 0
    assert gate.missing_red_baselines(plain, FEAT) == []


def test_criterion_resolve_uses_the_criterion_timeout(plain, capsys):
    _criteria(plain, [{"id": "ext", "description": "d", "verify_command": "sleep 5",
                       "timeout_sec": 1, "blocked_external": True,
                       "blocked_reason": "needs a sandbox"}])
    assert criterion.resolve(FEAT, "ext", plain, strict=True) == 1
    assert "TIMEOUT" in capsys.readouterr().out
    rec = [r for r in ledger.read_all() if r["event"] == "success_criterion_verified"][-1]
    assert rec["timed_out"] is True and rec["timeout_sec"] == 1


def test_every_runner_resolves_the_timeout_through_one_helper():
    pkg = Path(gate.__file__).parent
    for name in ("gate.py", "criterion.py"):
        text = (pkg / name).read_text()
        assert "timeout=_VERIFY_DEFAULT_TIMEOUT_SEC" not in text, name
        assert "_TIMEOUT_SEC = 600" not in text, name
        assert "criterion_timeout(" in text, name

