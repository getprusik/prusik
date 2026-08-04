"""Push-or-park: completed work must reach origin, mechanically.

moat-finding:fb-eef892a3e033 — a COMPLETED 25-commit sprint sat local-only
for a week; after a session collision the only copy of a migration lived in
reflog-recovered branches. Backup insurance must come from git's own state,
never from memory.
"""

import json
import subprocess

from prusik import push_guard, watchdog


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repo(tmp_path, remote=True, push=True):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.test")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("x\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "c1")
    if remote:
        origin = tmp_path / "origin.git"
        subprocess.run(["git", "init", "--bare", str(origin)],
                       check=True, capture_output=True)
        _git(repo, "remote", "add", "origin", str(origin))
        if push:
            _git(repo, "push", "-q", "-u", "origin", "main")
    return repo


def test_ahead_of_upstream_is_unpushed(tmp_path):
    repo = _repo(tmp_path)
    (repo / "b.txt").write_text("y\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-m", "c2")
    st = push_guard.state(repo)
    assert st["remote"] and st["upstream"] and st["ahead"] == 1
    ok, reason = push_guard.verdict(st)
    assert not ok and "1 commit" in reason and "git push" in reason


def test_no_upstream_is_unpushed(tmp_path):
    repo = _repo(tmp_path, push=False)
    ok, reason = push_guard.verdict(push_guard.state(repo))
    assert not ok and "no upstream" in reason


def test_clean_after_push_passes(tmp_path):
    repo = _repo(tmp_path)
    ok, _ = push_guard.verdict(push_guard.state(repo))
    assert ok


def test_no_remote_is_loudly_inapplicable(tmp_path):
    repo = _repo(tmp_path, remote=False)
    ok, reason = push_guard.verdict(push_guard.state(repo))
    assert ok and "inapplicable" in reason        # visible, never a block


def test_gate_check_advisory_records_event_and_require_blocks(
        tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    (repo / "b.txt").write_text("y\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-m", "c2")
    (repo / ".sprint").mkdir()
    monkeypatch.chdir(repo)
    ok = push_guard.gate_check(repo, {}, feature="f", phase="reviewing")
    assert ok                                     # advisory mode: never blocks
    out = capsys.readouterr().out
    assert "push-or-park" in out and "git push" in out
    events = [json.loads(line) for line in
              (repo / ".sprint" / "ledger.jsonl").read_text().splitlines()]
    assert any(e["event"] == "unpushed_sprint_work" for e in events)
    cfg = {"push_or_park": {"require": True}}
    assert not push_guard.gate_check(repo, cfg, feature="f", phase="reviewing")


def test_watchdog_files_incident_at_reviewing_when_unpushed(
        tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "b.txt").write_text("y\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-m", "c2")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "sprint-config.yaml").write_text("phases: {}\n")
    (repo / ".sprint").mkdir()
    (repo / ".sprint" / "state.json").write_text(
        json.dumps({"feature": "f", "phase": "reviewing"}))
    monkeypatch.chdir(repo)
    rc = watchdog.check(root=repo)
    assert rc == 1
    incidents = list((repo / ".sprint" / "incidents").glob("*.json"))
    kinds = [json.loads(f.read_text())["kind"] for f in incidents]
    assert "unpushed_sprint_work" in kinds
