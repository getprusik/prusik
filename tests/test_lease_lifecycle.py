"""The writer lease follows its holder's life and hands off to drive.

moat-finding: fb-ff93b3641749
moat-finding: fb-702c70a3971f
moat-finding: fb-361ec4acd0ee
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

import pytest

from prusik import gate, ledger, main_writer, phases

A, B = "session-aaaa", "session-bbbb"
_NOW = datetime.now(timezone.utc).isoformat()
F = "feat"


@pytest.fixture()
def proj(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".claude").mkdir(parents=True)
    (root / ".sprint").mkdir()
    import shutil
    from pathlib import Path
    shutil.copy(Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
                / "sprint-config.yaml", root / ".claude" / "sprint-config.yaml")
    monkeypatch.chdir(root)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.delenv("CLAUDE_PID", raising=False)
    return root


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _seed(root, session=A, host=None, feature=None, **extra):
    now = datetime.now(timezone.utc).isoformat()
    lease = {"session": session, "feature": feature, "phase": None,
             "acquired_at": now, "refreshed_at": now, **extra}
    if host is not None:
        lease["host"] = host
    main_writer._write(root, lease)


def _fire(hook, payload):
    old = sys.stdin
    sys.stdin = io.StringIO(json.dumps(payload))
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = hook()
    finally:
        sys.stdin = old
    return rc, buf.getvalue()


def _events(name):
    return [e for e in ledger.read_all() if e["event"] == name]


# ── liveness ──────────────────────────────────────────────────────────────────────────────

def test_dead_holder_is_taken_over_without_waiting_the_ttl(proj):
    _seed(proj, A, host={"pid": _dead_pid(), "since": _NOW})
    ok, prior, action = main_writer.check_and_touch(proj, B)
    assert (ok, action) == (True, "stolen_dead") and prior["session"] == A
    assert main_writer.load(proj)["session"] == B


def test_dead_holder_takeover_is_audited_by_the_hook(proj, monkeypatch):
    _seed(proj, A, host={"pid": _dead_pid(), "since": _NOW})
    _fire(gate.pre_tool, {"session_id": B, "tool_name": "Write",
                          "tool_input": {"file_path": "src/app.py"}})
    ev = _events("writer_lease_stolen_dead")
    assert ev and ev[-1]["prior_holder"] == A and ev[-1]["session"] == B


def test_live_host_keeps_the_lease(proj):
    _seed(proj, A, host={"pid": os.getpid(), "since": _NOW})
    ok, prior, action = main_writer.check_and_touch(proj, B)
    assert (ok, action) == (False, "denied") and prior["session"] == A


def test_a_reused_pid_does_not_make_a_dead_holder_look_alive(proj):
    _seed(proj, A, host={"pid": os.getpid(), "since": "2000-01-01T00:00:00+00:00"})
    assert main_writer.check_and_touch(proj, B)[2] == "stolen_dead"


def test_unreadable_start_time_fails_safe_toward_alive(proj, monkeypatch):
    _seed(proj, A, host={"pid": os.getpid(), "since": "2000-01-01T00:00:00+00:00"})
    monkeypatch.setattr(main_writer, "process_started_at", lambda pid: None)
    assert main_writer.check_and_touch(proj, B)[2] == "denied"


def test_permission_error_means_alive(proj, monkeypatch):
    def deny(pid, sig):
        raise PermissionError
    monkeypatch.setattr(os, "kill", deny)
    _seed(proj, A, host={"pid": 4242, "since": _NOW})
    assert main_writer.check_and_touch(proj, B)[2] == "denied"


def test_lease_without_a_process_keeps_the_ttl_rule(proj):
    _seed(proj, A)                                      # legacy lease: no host field
    assert main_writer.check_and_touch(proj, B)[2] == "denied"
    old = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    lease = main_writer.load(proj)
    lease["refreshed_at"] = old
    main_writer._write(proj, lease)
    assert main_writer.check_and_touch(proj, B)[2] == "stolen_stale"


def test_foreign_holder_and_live_owner_share_the_rule(proj):
    _seed(proj, A, host={"pid": _dead_pid(), "since": _NOW},
          driver={"pid": os.getpid(), "feature": F})
    assert main_writer.foreign_holder(proj, B) is None
    assert not main_writer.live_owner(proj, F, None)
    _seed(proj, A, host={"pid": os.getpid(), "since": _NOW},
          driver={"pid": os.getpid(), "feature": F})
    assert main_writer.foreign_holder(proj, B)["session"] == A
    assert main_writer.live_owner(proj, F, None)


def test_acquire_records_the_host_process(proj, monkeypatch):
    monkeypatch.setenv("CLAUDE_PID", str(os.getpid()))
    main_writer.check_and_touch(proj, A)
    host = main_writer.load(proj)["host"]
    assert host["pid"] == os.getpid() and host["since"]


def test_delegate_refresh_never_changes_the_host(proj, monkeypatch):
    monkeypatch.setenv("CLAUDE_PID", str(os.getpid()))
    main_writer.check_and_touch(proj, A)
    before = main_writer.load(proj)["host"]
    main_writer.delegate(proj, A, "role-1", role="r", feature=F)
    monkeypatch.setenv("CLAUDE_PID", "1")
    main_writer.check_and_touch(proj, "role-1")
    assert main_writer.load(proj)["host"] == before


# ── hand-off to drive ─────────────────────────────────────────────────────────────────────

def test_sprint_start_stamps_the_holders_lease(proj, monkeypatch):
    main_writer.check_and_touch(proj, A)                # the refresh before the command
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", A)
    assert main_writer.stamp_for_drive(proj, A, F, "scoping")
    lease = main_writer.load(proj)
    assert lease["feature"] == F and lease["drive_stamp"] == F
    assert not main_writer.stamp_for_drive(proj, B, F, "scoping")      # not B's lease


def test_hand_off_takes_a_live_lease_of_the_same_feature(proj):
    _seed(proj, A, host={"pid": os.getpid(), "since": _NOW}, feature=F,
          delegates=["r"], drive_stamp=F)
    prior = main_writer.hand_off_to_drive(proj, "drive-1", F)
    assert prior["session"] == A
    lease = main_writer.load(proj)
    assert lease["session"] == "drive-1" and "delegates" not in lease
    assert "drive_stamp" not in lease and lease["host"]["pid"] == os.getpid()
    assert lease["handed_off_from"] == A


@pytest.mark.parametrize("feature", [None, "other"])
def test_hand_off_refuses_another_or_no_feature(proj, feature):
    _seed(proj, A, host={"pid": os.getpid(), "since": _NOW}, feature=feature)
    assert main_writer.hand_off_to_drive(proj, "drive-1", F) is None
    assert main_writer.load(proj)["session"] == A


def test_hand_off_refuses_when_a_drive_already_runs(proj):
    _seed(proj, A, host={"pid": os.getpid(), "since": _NOW}, feature=F,
          driver={"pid": os.getpid(), "feature": F})
    assert main_writer.hand_off_to_drive(proj, "drive-1", F) is None


def test_former_holder_is_denied_with_a_message_naming_drive(proj):
    _seed(proj, A, feature=F)
    main_writer.hand_off_to_drive(proj, "drive-1", F)
    rc, out = _fire(gate.pre_tool, {"session_id": A, "tool_name": "Write",
                                    "tool_input": {"file_path": "src/app.py"}})
    assert '"deny"' in out and "prusik drive feat" in out
    blocked = [e for e in ledger.read_all() if e["event"] == "gate_blocked"][-1]
    assert blocked["gate_class"] == "writer_lease"


# ── Stop exemption ────────────────────────────────────────────────────────────────────────

def _scoping(root):
    phases.set_sprint_state({"phase": "scoping", "feature": F})


def _stop(session):
    return _fire(gate.stop, {"session_id": session})


def test_stop_does_not_block_while_the_lease_is_stamped_for_drive(proj):
    _scoping(proj)
    rc, out = _stop(A)
    assert '"block"' in out                              # control: artifacts are missing
    main_writer.check_and_touch(proj, A, feature=None, phase=None)
    main_writer.stamp_for_drive(proj, A, F, "scoping")
    assert _stop(A) == (0, "")


def test_a_later_write_ends_the_stop_exemption(proj):
    _scoping(proj)
    main_writer.check_and_touch(proj, A)
    main_writer.stamp_for_drive(proj, A, F, "scoping")
    _fire(gate.pre_tool, {"session_id": A, "tool_name": "Write",
                          "tool_input": {"file_path": "notes.txt"}})
    assert "drive_stamp" not in main_writer.load(proj)
    assert '"block"' in _stop(A)[1]


def test_the_stamp_exempts_only_its_own_session_and_feature(proj):
    _scoping(proj)
    main_writer.check_and_touch(proj, A)
    main_writer.stamp_for_drive(proj, A, F, "scoping")
    assert '"block"' in _stop(B)[1]
    phases.set_sprint_state({"phase": "scoping", "feature": "other"})
    assert '"block"' in _stop(A)[1]


# ── scope moat ────────────────────────────────────────────────────────────────────────────

def test_writes_outside_the_repo_and_worktrees_never_touch_the_lease(proj, tmp_path,
                                                                     monkeypatch):
    _seed(proj, B, host={"pid": os.getpid(), "since": _NOW}, feature=F)
    before = (proj / ".sprint" / "main-writer.json").read_bytes()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (proj / "worktrees" / "solo").mkdir(parents=True)
    for payload in (
            {"session_id": A, "tool_name": "Write",
             "tool_input": {"file_path": str(scratch / "n.txt")}},
            {"session_id": A, "tool_name": "Write",
             "tool_input": {"file_path": str(proj / "worktrees" / "solo" / "a.py")}}):
        assert '"deny"' not in _fire(gate.pre_tool, payload)[1]
    monkeypatch.chdir(scratch)
    out = _fire(gate.pre_tool, {"session_id": A, "tool_name": "Bash", "cwd": str(scratch),
                                "tool_input": {"command": "echo hi > rel.txt"}})[1]
    assert '"deny"' not in out
    assert (proj / ".sprint" / "main-writer.json").read_bytes() == before
