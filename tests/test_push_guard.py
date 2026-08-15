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


# ---- remote-truth push confirmation (fb-60d5c11b2f99) --------------------------
# moat-finding:fb-60d5c11b2f99 — the integrator backgrounded `git push`, returned,
# and the process died with its turn: origin never moved, retro unwritten, sprint on
# one disk. The local `@{upstream}` check trusts the tracking ref; confirmation must
# come from the REMOTE itself (`git ls-remote`), which an orphaned/forged ref can't fake.

def test_remote_confirm_confirms_a_real_push(tmp_path):
    repo = _repo(tmp_path)                          # pushed -u origin main
    status, sha = push_guard.remote_confirm(repo, push_guard.state(repo))
    assert status == "confirmed"
    assert sha == push_guard._git(repo, "rev-parse", "HEAD")


def test_remote_confirm_catches_orphaned_push_the_local_ref_hides(tmp_path):
    # THE ADVERSARIAL CASE: a commit whose tracking ref was advanced (as a real push
    # would) but origin never received it — the orphaned-background-push shape. The
    # LOCAL verdict reads `parked` (ahead == 0); only the remote catches the lie.
    repo = _repo(tmp_path)
    (repo / "b.txt").write_text("y\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-m", "c2")
    # advance the local remote-tracking ref to HEAD WITHOUT pushing to origin
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    st = push_guard.state(repo)
    assert st["ahead"] == 0                         # the trap: local says parked …
    assert push_guard.verdict(st)[0] is True        # … and the local verdict agrees
    status, sha = push_guard.remote_confirm(repo, st)
    assert status == "diverged"                     # … but origin never moved
    assert sha != push_guard._git(repo, "rev-parse", "HEAD")


def test_remote_confirm_unreachable_when_origin_is_gone(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "remote", "set-url", "origin", str(tmp_path / "does-not-exist.git"))
    status, sha = push_guard.remote_confirm(repo, push_guard.state(repo))
    assert status == "unreachable" and sha is None   # degrade, never a false 'confirmed'


def test_remote_confirm_inapplicable_without_a_remote(tmp_path):
    repo = _repo(tmp_path, remote=False)
    status, _ = push_guard.remote_confirm(repo, push_guard.state(repo))
    assert status == "inapplicable"


def test_sprint_complete_blocks_an_unconfirmed_push_under_require(
        tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "b.txt").write_text("y\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-m", "c2")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")   # forge parked
    (repo / ".sprint").mkdir()
    monkeypatch.chdir(repo)
    cfg = {"push_or_park": {"require": True}}
    # under require, an unconfirmable push at the terminal HARD-BLOCKS …
    assert not push_guard.gate_check(repo, cfg, feature="f", phase="sprint-complete")
    events = [json.loads(line) for line in
              (repo / ".sprint" / "ledger.jsonl").read_text().splitlines()]
    assert any(e["event"] == "push_unconfirmed" for e in events)
    # … while advisory (no require) surfaces it but does not block.
    assert push_guard.gate_check(repo, {}, feature="f", phase="sprint-complete")


def test_pre_push_advance_checks_skip_the_network_confirmation(tmp_path, monkeypatch):
    # remote-confirm is terminal-only: reviewing/integrating ENTRY is PRE-push, so a
    # forged-but-locally-parked state must NOT be probed against origin there (no
    # network cost on the frequent advance path, and nothing to confirm yet).
    repo = _repo(tmp_path)
    (repo / "b.txt").write_text("y\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-m", "c2")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    (repo / ".sprint").mkdir()
    monkeypatch.chdir(repo)
    cfg = {"push_or_park": {"require": True}}
    assert push_guard.gate_check(repo, cfg, feature="f", phase="integrating")
    assert not (repo / ".sprint" / "ledger.jsonl").exists() or not any(
        json.loads(line)["event"] == "push_unconfirmed"
        for line in (repo / ".sprint" / "ledger.jsonl").read_text().splitlines())


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
