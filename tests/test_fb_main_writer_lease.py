"""Two concurrent sessions with write access to the same checkout collided in
the field: a sprint-less ad-hoc responder — which the gate never saw, because
pre_tool returned before any check when no sprint was active — hard-reset main
and silently discarded 25 unpushed commits while an 'integrating'-phase sprint
(writable '**') was working the same tree. The fix: a TTL single-writer lease
on the SHARED tree, checked for EVERY session before the no-sprint early
return; worktrees stay fully concurrent.

fb-0b9d0a6d1ce6.

moat-finding: fb-0b9d0a6d1ce6
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

from _common import _copy_sprint_config, _mktmp_project

from prusik import gate, main_writer, phases

A, B = "session-aaaa-1111", "session-bbbb-2222"


def _fire(payload):
    stdin_backup = sys.stdin
    sys.stdin = io.StringIO(json.dumps(payload))
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = gate.pre_tool()
    finally:
        sys.stdin = stdin_backup
    return rc, buf.getvalue()


def _write(session, path):
    return {"session_id": session, "tool_name": "Write",
            "tool_input": {"file_path": path}}


def _bash(session, cmd):
    return {"session_id": session, "tool_name": "Bash",
            "tool_input": {"command": cmd}}


def _denied(out: str) -> bool:
    return '"deny"' in out


def test_sprintless_git_reset_blocked_while_another_session_holds(tmp_path):
    # THE field scenario: A (integrating sprint) writes main and holds the
    # lease; B — NO active sprint, the exact shape the gate used to wave
    # through — tries the hard reset. Must be denied, loudly.
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        phases.set_sprint_state({"phase": "integrating", "feature": "beta"})
        rc, out = _fire(_write(A, "src/app.py"))
        assert not _denied(out)

        phases.clear_sprint_state()          # B is sprint-less, like the field
        rc, out = _fire(_bash(B, "git reset --hard b70c064"))
        assert _denied(out) and "fb-0b9d0a6d1ce6" in out
    finally:
        import os
        import shutil
        os.chdir("/")
        shutil.rmtree(tmp)


def test_holder_keeps_writing_and_worktrees_stay_concurrent(tmp_path):
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        phases.set_sprint_state({"phase": "integrating", "feature": "beta"})
        _fire(_write(A, "src/app.py"))
        # holder refreshes freely
        rc, out = _fire(_write(A, "src/other.py"))
        assert not _denied(out)
        # a second session in an isolated worktree is NOT serialized
        rc, out = _fire(_write(B, "worktrees/security/pkg.json"))
        assert not _denied(out)
        rc, out = _fire(_bash(B, "git -C worktrees/security commit -m x"))
        assert not _denied(out)
        rc, out = _fire(_bash(B, "cd worktrees/security && git reset --hard"))
        assert not _denied(out)
        # read-only bash is never serialized
        rc, out = _fire(_bash(B, "git status && git log --oneline"))
        assert not _denied(out)
        # ...but the same session's shared-tree mutation is
        rc, out = _fire(_bash(B, "git checkout -- package.json"))
        assert _denied(out)
    finally:
        import os
        import shutil
        os.chdir("/")
        shutil.rmtree(tmp)


def test_serialization_is_symmetric_adhoc_acquires_first(tmp_path):
    # reversed roles: the sprint-less session mutates first and acquires; the
    # SPRINT session is then the one denied — protection has no favorites
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        phases.clear_sprint_state()
        rc, out = _fire(_bash(B, "git commit -am wip"))
        assert not _denied(out)              # B acquired (no sprint to gate it)

        phases.set_sprint_state({"phase": "integrating", "feature": "beta"})
        rc, out = _fire(_write(A, "src/app.py"))
        assert _denied(out)
    finally:
        import os
        import shutil
        os.chdir("/")
        shutil.rmtree(tmp)


def test_stale_lease_is_taken_over_not_a_deadlock(tmp_path):
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        phases.set_sprint_state({"phase": "integrating", "feature": "beta"})
        _fire(_write(A, "src/app.py"))
        # backdate: holder went silent past the TTL (crashed session)
        lease = main_writer.load(tmp)
        stale = (datetime.now(timezone.utc)
                 - timedelta(minutes=main_writer.LEASE_TTL_MIN + 1)).isoformat()
        lease["refreshed_at"] = stale
        (tmp / ".sprint" / "main-writer.json").write_text(json.dumps(lease))

        rc, out = _fire(_write(B, "src/app.py"))
        assert not _denied(out)
        assert main_writer.load(tmp)["session"] == B
    finally:
        import os
        import shutil
        os.chdir("/")
        shutil.rmtree(tmp)


def test_release_writer_hands_off_audited(tmp_path):
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        phases.set_sprint_state({"phase": "integrating", "feature": "beta"})
        _fire(_write(A, "src/app.py"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = gate.release_writer(None)
        assert rc == 0 and main_writer.load(tmp) is None
        # next writer acquires cleanly
        rc, out = _fire(_write(B, "src/app.py"))
        assert not _denied(out)
        assert main_writer.load(tmp)["session"] == B
    finally:
        import os
        import shutil
        os.chdir("/")
        shutil.rmtree(tmp)


def test_no_session_identity_no_serialization(tmp_path):
    # a host that supplies no session_id can't be serialized — the check is
    # inapplicable, and must not crash or false-block
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        phases.set_sprint_state({"phase": "integrating", "feature": "beta"})
        payload = {"tool_name": "Write", "tool_input": {"file_path": "src/a.py"}}
        rc, out = _fire(payload)
        assert not _denied(out)
        assert main_writer.load(tmp) is None
    finally:
        import os
        import shutil
        os.chdir("/")
        shutil.rmtree(tmp)
