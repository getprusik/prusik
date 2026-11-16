"""Unit and in-process hook tests for observed writes (fb-aa7a6121fcee): the public functions
of `prusik.observed_writes`, `main_writer.foreign_holder`, the adapter's `call_id` and
`post_tool_context`, and the gate helpers that pair pre/post snapshots, refuse advance and
fold the fix-round assembly. The end-to-end CLI flows live in tests/test_observed_writes.py."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from prusik import consistency, gate, ledger, main_writer, observed_writes as ow, phases
from prusik import platform_adapter as pa

_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATE_CFG = _ROOT / "prusik" / "templates" / ".claude" / "sprint-config.yaml"
F = "feat"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def proj(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "p"
    root.mkdir()
    (root / ".claude").mkdir()
    shutil.copy(_TEMPLATE_CFG, root / ".claude" / "sprint-config.yaml")
    (root / ".gitignore").write_text("build/\n.sprint/\ndesign/\n")
    (root / "README.md").write_text("hello\n")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("x = 1\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    (root / ".sprint").mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.chdir(root)
    return root


def _enter(root: Path, phase: str, *, baseline: bool = True) -> None:
    """Enter `phase` the way sprint-start/advance do: state, entry event, baseline."""
    phases.set_sprint_state({"phase": phase, "feature": F}, root)
    ledger.append("phase_advance", from_phase="x", to_phase=phase, feature=F)
    if baseline:
        gate._take_phase_baseline(F, phase)


def _hook(fn, payload: dict, capsys) -> str:
    capsys.readouterr()
    os.environ.pop("PRUSIK_ADAPTER", None)
    import sys
    sys.stdin = io.StringIO(json.dumps(payload))
    try:
        fn()
    finally:
        sys.stdin = sys.__stdin__
    return capsys.readouterr().out


def _call(root: Path, capsys, write, *, cid: str = "toolu_1", session: str = "s1",
          command: str = "python3 -c 'x'") -> str:
    """One Bash call through the real hooks: pre-tool, the write, post-tool."""
    base = {"session_id": session, "tool_name": "Bash", "tool_input": {"command": command},
            "tool_use_id": cid, "cwd": str(root)}
    _hook(gate.pre_tool, {**base, "hook_event_name": "PreToolUse"}, capsys)
    write()
    out = _hook(gate.post_tool, {**base, "hook_event_name": "PostToolUse",
                                 "tool_response": {"stdout": ""}}, capsys)
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""


def _events(name: str) -> list[dict]:
    return [e for e in ledger.read_all() if e.get("event") == name]


# ---- observed_writes primitives ------------------------------------------------------------

def test_snapshot_prunes_always_writable_derived_and_nested_repos(proj):
    for rel in ("reports/r.txt", "findings/fb.json", "node_modules/m.js", "sub/.git",
                "sub/inner.py", "worktrees/b/x.py", "deep/a/__pycache__/c.pyc"):
        (proj / rel).parent.mkdir(parents=True, exist_ok=True)
        (proj / rel).write_text("x")
    os.symlink("README.md", proj / "link")
    snap = ow.snapshot(proj, None, deadline_s=None)
    assert {"README.md", "src/app.py", ".gitignore", "link"} <= snap.keys()
    assert not [p for p in snap if p.startswith(("reports/", "findings/", "node_modules/",
                                                 "sub/", "worktrees/", "deep/", ".sprint/",
                                                 ".git/"))]
    assert snap["link"][2] == __import__("hashlib").sha256(b"README.md").hexdigest()


def test_snapshot_reuses_cached_sha_only_when_stat_matches(proj):
    snap = ow.snapshot(proj, None, deadline_s=None)
    fake = {k: [v[0], v[1], "cached"] for k, v in snap.items()}
    again = ow.snapshot(proj, fake, deadline_s=None)
    assert again["README.md"][2] == "cached"
    (proj / "README.md").write_text("changed!\n")
    assert ow.snapshot(proj, fake, deadline_s=None)["README.md"][2] != "cached"


def test_snapshot_deadline_saves_progress_and_raises(proj):
    with pytest.raises(ow.TreeUnreadable, match="exceeded 0"):
        ow.snapshot(proj, None, deadline_s=0)
    assert ow.load_cache(proj)                       # progress kept


def test_snapshot_unscannable_root_raises(tmp_path):
    with pytest.raises(ow.TreeUnreadable):
        ow.snapshot(tmp_path / "missing", None, deadline_s=None, config={}, feature=None)


def test_candidates_and_content_confirmed_changed(proj):
    before = ow.snapshot(proj, None, deadline_s=None)
    st = (proj / "README.md").stat()
    os.utime(proj / "README.md", ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))  # touch
    (proj / "src" / "app.py").write_text("x = 2\n")
    (proj / "new.txt").write_text("n")
    (proj / "build").mkdir()
    (proj / "build" / "out.o").write_text("o")
    after = ow.snapshot(proj, before, deadline_s=None)
    assert ow.candidates(before, after) == ["README.md", "build/out.o", "new.txt",
                                            "src/app.py"]
    cfg = phases.load_sprint_config(proj)
    assert ow.changed(before, after, proj, cfg, F) == ["new.txt", "src/app.py"]


def test_none_sha_counts_as_changed(proj):
    before = ow.snapshot(proj, None, deadline_s=None)
    after = {**before, "README.md": [before["README.md"][0] + 1, before["README.md"][1], None]}
    assert ow.changed(before, after, proj, phases.load_sprint_config(proj), F) == ["README.md"]


def test_members_gated_dir_counts_even_when_gitignored(proj):
    cfg = phases.load_sprint_config(proj)
    assert "design" in ow.gated_dirs(cfg)
    got = ow.members(proj, ["design/feat/scope.md", "build/x", "src/app.py", "new.py"], cfg, F)
    assert got == {"design/feat/scope.md", "src/app.py", "new.py"}


def test_members_no_spawn_for_gated_only_or_empty(proj, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("spawned")
    monkeypatch.setattr(subprocess, "run", boom)
    cfg = phases.load_sprint_config(proj)
    assert ow.members(proj, [], cfg, F) == set()
    assert ow.members(proj, ["design/x.md"], cfg, F) == {"design/x.md"}


def test_members_broken_git_raises_never_passes(proj, tmp_path, monkeypatch):
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "git").write_text("#!/bin/sh\nexit 128\n")
    (fake / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}:{os.environ['PATH']}")
    with pytest.raises(ow.TreeUnreadable, match="check-ignore failed"):
        ow.members(proj, ["new.py"], phases.load_sprint_config(proj), F)


def test_members_non_git_project_counts_everything(tmp_path):
    assert ow.members(tmp_path, ["a", "b/c"], {}, None) == {"a", "b/c"}


def test_gated_dirs_drops_globs_worktrees_and_sprint():
    cfg = {"phases": [{"writable": ["worktrees/*/**", "src/**", "{x}/a", "*.md", ".sprint/s",
                                    "/tmp/**", "~/x"],
                       "exit_artifacts": [{"path": "decisions/{feature}.json"}]}]}
    assert ow.gated_dirs(cfg) == frozenset({"src", "decisions"})


def test_matches_baseline():
    now = {"a": [1, 1, "x"], "b": [1, 1, "y"], "c": [1, 1, None]}
    base = {"a": [0, 0, "x"], "b": [0, 0, "z"], "c": [0, 0, None]}
    assert ow.matches_baseline(["a", "b", "c", "gone"], now, base) == {"a", "gone"}


def test_pending_roundtrip_corrupt_and_key_sanitized(proj):
    key = ow.pending_key("s/1", "toolu ../x")
    assert key == "s_1-toolu____x"
    assert ow.pending_key(None, "c") == "nosession-c"
    ow.write_pending(proj, key, {"a": 1})
    assert ow.pop_pending(proj, key) == {"a": 1}
    assert ow.pop_pending(proj, key) is None
    ow.write_pending(proj, "k", {})
    (proj / ".sprint/observed-writes/pending/k.json").write_text("{nope")
    assert ow.pop_pending(proj, "k") == {"error": "pending snapshot unreadable"}


def test_pop_orphans_takes_only_old_records(proj):
    old = (datetime.now(timezone.utc) - timedelta(seconds=ow.ORPHAN_AFTER_S + 1)).isoformat()
    ow.write_pending(proj, "old", {"ts": old})
    ow.write_pending(proj, "young", {"ts": datetime.now(timezone.utc).isoformat()})
    got = ow.pop_orphans(proj, ow.ORPHAN_AFTER_S)
    assert [k for k, _ in got] == ["old"]
    assert (proj / ".sprint/observed-writes/pending/young.json").exists()
    assert not hasattr(ow, "sweep_pending")


def test_baseline_write_load_mismatch_and_clear(proj):
    ow.write_baseline(proj, F, "building", "T1", {"a": [1, 1, "x"]})
    assert ow.load_baseline(proj, F, "building", "T1") == ({"a": [1, 1, "x"]}, None)
    assert ow.load_baseline(proj, F, "building", None)[1] is None
    assert ow.load_baseline(proj, F, "building", "T2") == (None, "mismatch")
    assert ow.load_baseline(proj, F, "reviewing", None) == (None, "mismatch")
    (proj / ".sprint/observed-writes/baseline.json").write_text("[]")
    assert ow.load_baseline(proj, F, "building", None) == (None, "unreadable")
    ow.clear_baseline(proj)
    assert ow.load_baseline(proj, F, "building", None) == (None, "missing")


def test_fold_baseline_noop_without_matching_sprint_and_folds_otherwise(proj):
    ow.fold_baseline(proj, ["README.md"])                # no baseline: no-op
    assert not (proj / ".sprint/observed-writes/baseline.json").exists()
    phases.set_sprint_state({"phase": "reviewing", "feature": F}, proj)
    ow.write_baseline(proj, F, "building", "T", {})      # other phase: no-op
    ow.fold_baseline(proj, ["README.md"])
    assert ow.load_baseline(proj, F, "building", None)[0] == {}
    ow.write_baseline(proj, F, "reviewing", "T", {"gone.txt": [1, 1, "x"]})
    ow.fold_baseline(proj, ["README.md", "gone.txt"])
    snap = ow.load_baseline(proj, F, "reviewing", "T")[0]
    assert set(snap) == {"README.md"}
    assert snap["README.md"][2] == ow.snapshot(proj, None, deadline_s=None)["README.md"][2]


# ---- lease, adapter --------------------------------------------------------------------------

def test_foreign_holder(proj):
    assert main_writer.foreign_holder(proj, "b") is None
    main_writer.check_and_touch(proj, "a")
    assert main_writer.foreign_holder(proj, "b")["session"] == "a"
    assert main_writer.foreign_holder(proj, None)["session"] == "a"
    assert main_writer.foreign_holder(proj, "a") is None
    main_writer.delegate(proj, "a", "d", role="r", feature=F)
    assert main_writer.foreign_holder(proj, "d") is None
    lease = main_writer.load(proj)
    lease["refreshed_at"] = "2000-01-01T00:00:00+00:00"
    (proj / ".sprint" / "main-writer.json").write_text(json.dumps(lease))
    assert main_writer.foreign_holder(proj, "b") is None
    assert main_writer.load(proj) == lease                # read-only


def test_adapter_call_id_and_post_tool_context(capsys):
    a = pa.ClaudeCodeAdapter()
    for tool, ti in (("Bash", {"command": "ls"}), ("Edit", {"file_path": "a"}), ("Read", {})):
        assert a.parse_event({"tool_name": tool, "tool_input": ti,
                              "tool_use_id": "toolu_9"}).call_id == "toolu_9"
        assert a.parse_event({"tool_name": tool, "tool_input": ti}).call_id is None
    assert pa.ToolEvent(tool="Bash", command="x", session="s", cwd="/").call_id is None
    assert a.post_tool_context([]) == 0 and capsys.readouterr().out == ""
    assert a.post_tool_context(["a", "b"]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert json.loads(out)["hookSpecificOutput"] == {"hookEventName": "PostToolUse",
                                                     "additionalContext": "a\n\nb"}


def test_findings_always_writable(proj):
    cfg = phases.load_sprint_config(proj)
    assert phases.is_path_writable("findings/fb-1.json", cfg, "building", F) == (True, None)


# ---- hooks, in process -----------------------------------------------------------------------

def test_hook_reports_unwritable_write_then_revert_is_not_damage(proj, capsys):
    # moat-finding: fb-aa7a6121fcee
    _enter(proj, "building")
    msg = _call(proj, capsys, lambda: (proj / "README.md").write_text("bad\n"))
    assert "[prusik-observed-write]" in msg and "README.md" in msg and "'building'" in msg
    ev = _events("observed_write_reported")[-1]
    assert ev["unwritable"] == ["README.md"] and ev["orphan"] is False
    # The revert is itself a Bash write to an unwritable path; it matches the baseline.
    assert _call(proj, capsys, lambda: (proj / "README.md").write_text("hello\n"),
                 cid="toolu_2", command="python3 -c 'revert'") == ""
    assert not list((proj / ".sprint/observed-writes/pending").glob("*.json"))


def test_hook_ignores_writable_ignored_and_touched_paths(proj, capsys):
    _enter(proj, "building")
    (proj / "README.md").write_text("dirty at entry\n")    # dirty, then untouched

    def writes():
        (proj / "worktrees" / "b").mkdir(parents=True)
        (proj / "worktrees" / "b" / "x.py").write_text("ok")
        (proj / "build").mkdir()
        (proj / "build" / "x").write_text("ignored")
        st = (proj / "src" / "app.py").stat()
        os.utime(proj / "src" / "app.py", ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000))
    assert _call(proj, capsys, writes) == ""


def test_hook_reports_gitignored_gated_dir_and_deletion(proj, capsys):
    # moat-finding: fb-c1d2d8dcabac
    _enter(proj, "building")

    def writes():
        (proj / "design" / F).mkdir(parents=True)
        (proj / "design" / F / "scope.md").write_text("rewritten")
        (proj / "src" / "app.py").unlink()
    msg = _call(proj, capsys, writes)
    assert "design/feat/scope.md" in msg and "src/app.py" in msg


def test_hook_without_pre_snapshot_reports_could_not_run(proj, capsys):
    _enter(proj, "building", baseline=False)
    out = _hook(gate.post_tool, {"session_id": "s", "tool_name": "Bash", "tool_use_id": "t9",
                                 "tool_input": {"command": "ls"}}, capsys)
    assert "could not run" in out and "no pre-call snapshot" in out
    assert _events("observed_write_check_failed")[-1]["reason"] == "no_pre_snapshot"


def test_hook_without_tool_use_id_reports_only_when_pre_marked_it(proj, capsys):
    _enter(proj, "building", baseline=False)
    payload = {"session_id": "s", "tool_name": "Bash", "tool_input": {"command": "ls"}}
    assert _hook(gate.post_tool, payload, capsys) == ""   # no pre ran: nothing claimed
    _hook(gate.pre_tool, payload, capsys)
    out = _hook(gate.post_tool, payload, capsys)
    assert "could not run" in out and "no tool-use id" in out


def test_hook_corrupt_pending_reports_could_not_run(proj, capsys):
    _enter(proj, "building", baseline=False)
    ow.write_pending(proj, ow.pending_key("s", "t1"), {})
    (proj / ".sprint/observed-writes/pending/s-t1.json").write_text("{bad")
    out = _hook(gate.post_tool, {"session_id": "s", "tool_name": "Bash", "tool_use_id": "t1",
                                 "tool_input": {"command": "ls"}}, capsys)
    assert "pending snapshot unreadable" in out


def test_hook_deadline_reports_could_not_run(proj, capsys, monkeypatch):
    _enter(proj, "building", baseline=False)
    monkeypatch.setattr(ow, "HOOK_SNAPSHOT_DEADLINE_S", 0)
    msg = _call(proj, capsys, lambda: None)
    assert "could not run" in msg and "exceeded" in msg
    assert ow.load_cache(proj)


def test_no_sprint_no_foreign_lease_takes_no_snapshot(proj, capsys):
    msg = _call(proj, capsys, lambda: (proj / "README.md").write_text("x"))
    assert msg == "" and not (proj / ".sprint" / "observed-writes").exists()


def test_foreign_lease_reports_writes_even_to_writable_paths(proj, capsys):
    # moat-finding: fb-16c859a406b9
    main_writer.check_and_touch(proj, "holder-session-aaaa")
    msg = _call(proj, capsys, lambda: (proj / "src" / "app.py").write_text("y"), session="b")
    assert "holds the single-writer lease" in msg and "holder-sessi" in msg
    assert "src/app.py" in msg
    assert _call(proj, capsys, lambda: None, session="b", cid="t2") == ""


def test_lease_accepted_miss_always_writable_dirs(proj, capsys):
    main_writer.check_and_touch(proj, "holder")

    def writes():
        (proj / "findings").mkdir()
        (proj / "findings" / "x.json").write_text("{}")
    assert _call(proj, capsys, writes, session="b") == ""


def test_orphan_is_reported_on_a_later_call_and_young_one_is_kept(proj, capsys):
    _enter(proj, "building")
    base = {"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": "x"}}
    _hook(gate.pre_tool, {**base, "tool_use_id": "orphan1"}, capsys)
    (proj / "README.md").write_text("written by a call whose post never ran\n")
    _hook(gate.pre_tool, {**base, "tool_use_id": "young"}, capsys)
    path = proj / ".sprint/observed-writes/pending/s1-orphan1.json"
    rec = json.loads(path.read_text())
    rec["ts"] = (datetime.now(timezone.utc)
                 - timedelta(seconds=ow.ORPHAN_AFTER_S + 1)).isoformat()
    path.write_text(json.dumps(rec))
    msg = _call(proj, capsys, lambda: None, cid="later")
    assert "orphan1" in msg and "README.md" in msg and "'building'" in msg
    assert _events("observed_write_check_failed")[-1]["stage"] == "orphan"
    assert _events("observed_write_reported")[-1]["orphan"] is True
    assert not path.exists()
    assert (proj / ".sprint/observed-writes/pending/s1-young.json").exists()


# ---- advance and baselines -------------------------------------------------------------------

def test_baseline_stamped_with_the_entry_event(proj):
    _enter(proj, "building")
    data = json.loads((proj / ".sprint/observed-writes/baseline.json").read_text())
    assert data["entered_at"] == _events("phase_advance")[-1]["ts"]
    assert data["phase"] == "building" and data["feature"] == F


def test_advance_refuses_unwritable_change_then_passes_after_revert(proj, capsys):
    _enter(proj, "building")
    cfg = phases.load_sprint_config(proj)
    (proj / "README.md").write_text("bad\n")
    assert gate._observed_advance_block(cfg, "building", "reviewing", F) == 2
    assert "README.md" in capsys.readouterr().err
    ev = _events("advance_blocked")[-1]
    assert ev["gate_class"] == "observed_write" and ev["paths"] == ["README.md"]
    assert not _events("phase_baseline_retaken")
    _git(proj, "checkout", "--", "README.md")
    assert gate._observed_advance_block(cfg, "building", "reviewing", F) is None


def test_advance_dirty_at_entry_untouched_passes_rewritten_refuses(proj):
    (proj / "README.md").write_text("dirty before entry\n")
    _enter(proj, "building")
    cfg = phases.load_sprint_config(proj)
    assert gate._observed_advance_block(cfg, "building", "reviewing", F) is None
    (proj / "README.md").write_text("rewritten in phase\n")
    assert gate._observed_advance_block(cfg, "building", "reviewing", F) == 2


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_advance_missing_or_corrupt_baseline_refuses_once(proj, damage):
    _enter(proj, "building")
    p = proj / ".sprint/observed-writes/baseline.json"
    if damage == "missing":
        p.unlink()
    else:
        p.write_text("{oops")
    cfg = phases.load_sprint_config(proj)
    assert gate._observed_advance_block(cfg, "building", "reviewing", F) == 2
    assert _events("phase_baseline_retaken")[-1]["reason"] in ("missing", "unreadable")
    assert gate._observed_advance_block(cfg, "building", "reviewing", F) is None


def test_failed_baseline_write_leaves_no_stale_baseline(proj, monkeypatch):
    _enter(proj, "building")

    def boom(*a, **k):
        raise OSError("disk full")
    with monkeypatch.context() as m:
        m.setattr(ow, "write_baseline", boom)
        _enter(proj, "reviewing")
    assert not (proj / ".sprint/observed-writes/baseline.json").exists()
    assert _events("observed_write_check_failed")[-1]["stage"] == "baseline"
    cfg = phases.load_sprint_config(proj)
    assert gate._observed_advance_block(cfg, "reviewing", "integrating", F) == 2
    assert _events("phase_baseline_retaken")[-1]["reason"] == "missing"


def test_hand_set_phase_is_not_checked_but_recorded(proj):
    _enter(proj, "building")
    phases.set_sprint_state({"phase": "reviewing", "feature": F}, proj)   # no entry event
    (proj / "README.md").write_text("bad\n")
    cfg = phases.load_sprint_config(proj)
    assert gate._observed_advance_block(cfg, "reviewing", "integrating", F) is None
    assert "no phase-entry event" in _events("observed_write_check_failed")[-1]["reason"]


# ---- fix-round fold --------------------------------------------------------------------------

def test_assembly_folds_only_copied_paths(proj, capsys):
    (proj / "src" / "app.py").write_text("x = 'root, modified'\n")       # not pristine
    _enter(proj, "reviewing")
    wt = proj / "worktrees" / "backend-builder" / "src"
    wt.mkdir(parents=True)
    (wt / "app.py").write_text("x = 'fixed'\n")
    (proj / "README.md").write_text("agent's own root write\n")
    staged = consistency.assemble_worktrees_to_root(proj)
    assert staged == ["src/app.py"]
    cfg = phases.load_sprint_config(proj)
    assert gate._observed_advance_block(cfg, "reviewing", "integrating", F) == 2
    assert _events("advance_blocked")[-1]["paths"] == ["README.md"]


def test_assembly_without_baseline_is_unchanged(proj):
    (proj / "src" / "app.py").write_text("x = 'root, modified'\n")
    wt = proj / "worktrees" / "backend-builder" / "src"
    wt.mkdir(parents=True)
    (wt / "app.py").write_text("x = 'fixed'\n")
    assert consistency.assemble_worktrees_to_root(proj) == ["src/app.py"]
    assert (proj / "src" / "app.py").read_text() == "x = 'fixed'\n"
    assert not (proj / ".sprint/observed-writes/baseline.json").exists()


def test_fold_failure_never_breaks_the_assembly(proj, monkeypatch, capsys):
    _enter(proj, "reviewing")
    (proj / "src" / "app.py").write_text("x = 'root, modified'\n")
    wt = proj / "worktrees" / "backend-builder" / "src"
    wt.mkdir(parents=True)
    (wt / "app.py").write_text("x = 'fixed'\n")

    def boom(*a, **k):
        raise OSError("nope")
    monkeypatch.setattr(ow, "fold_baseline", boom)
    assert consistency.assemble_worktrees_to_root(proj) == ["src/app.py"]
    assert _events("observed_write_check_failed")[-1]["stage"] == "fold"
    assert "not folded" in capsys.readouterr().err
