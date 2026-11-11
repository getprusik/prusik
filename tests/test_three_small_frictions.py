"""Three field frictions, each a wrong or unhelpful message.

- The cross-builder CSS advisory reported `-mr-2` as a naming variant of `mr-2`.
- The writer-lease refusal for `prusik gate release-writer && git push` did not say the
  release has to run alone.
- `prusik gate fix-round status` rejected the `--feature` that `start` and `end` require.

moat-finding: fb-cba134e72aef
moat-finding: fb-3e80f782a066
moat-finding: fb-427305c05d6b
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
from contextlib import redirect_stdout

import pytest

from prusik import __main__ as cli
from prusik import cross_builder, gate, main_writer, phases
from tests._common import _copy_sprint_config, _mktmp_project

A, B = "session-aaaa-1111", "session-bbbb-2222"


def _worktree(tmp, name, files):
    for rel, text in files.items():
        p = tmp / "worktrees" / name / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


@pytest.fixture
def project():
    tmp = _mktmp_project()
    yield tmp
    os.chdir("/")
    shutil.rmtree(tmp, ignore_errors=True)


# ---- a leading hyphen is not a naming convention ------------------------------------

def test_negative_utilities_are_not_reported_as_variants(project):
    _worktree(project, "css", {"s.css": ".mr-2 {}\n.rotate-90 {}\n.translate-x-1 {}\n"})
    _worktree(project, "solo", {"Tips.tsx":
              '<div className="-mr-2 -rotate-90 -translate-x-1 mr-2"></div>\n'})
    assert cross_builder.css_drift(project) == []
    assert cross_builder.advisory(project, "feat") is None


def test_the_bem_versus_flat_seam_is_still_reported(project):
    _worktree(project, "css", {"s.css": ".client__table {}\n.client-row {}\n.mr-2 {}\n"})
    _worktree(project, "tpl", {"x.html":
              '<table class="client-table client_row -mr-2"></table>\n'})
    drift = {d["used_class"]: d["defined_as"] for d in cross_builder.css_drift(project)}
    assert drift == {"client-table": ["client__table"], "client_row": ["client-row"]}


# ---- the lease refusal says how to hand off -----------------------------------------

def _fire(payload):
    stdin_backup = sys.stdin
    sys.stdin = io.StringIO(json.dumps(payload))
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            gate.pre_tool()
    finally:
        sys.stdin = stdin_backup
    return buf.getvalue()


def _bash(session, cmd):
    return {"session_id": session, "tool_name": "Bash", "tool_input": {"command": cmd}}


def _held_by_a(project):
    _copy_sprint_config(project)
    phases.set_sprint_state({"phase": "integrating", "feature": "beta"})
    out = _fire({"session_id": A, "tool_name": "Write",
                 "tool_input": {"file_path": "src/app.py"}})
    assert '"deny"' not in out
    phases.clear_sprint_state()


@pytest.mark.parametrize("cmd", [
    "prusik gate release-writer && git push",
    "prusik gate release-writer; git push origin main",
    "prusik  gate   release-writer && git commit -m x",
])
def test_release_chained_with_a_git_write_is_still_refused_and_says_why(project, cmd):
    _held_by_a(project)
    out = _fire(_bash(B, cmd))
    assert '"deny"' in out                                   # the decision is unchanged
    assert main_writer.load(project)["session"] == A         # and the lease was not taken
    assert "run as its own command" in out
    assert "judged whole before any of it runs" in out
    assert "alone first, then the rest" in out


def test_a_refusal_without_release_in_it_gets_no_chaining_line(project):
    _held_by_a(project)
    out = _fire(_bash(B, "git push"))
    assert '"deny"' in out
    assert "run as its own command" in out                   # every refusal says this
    assert "judged whole" not in out


def test_release_alone_then_the_push_goes_through(project):
    """The path the message points to has to work."""
    _held_by_a(project)
    assert '"deny"' not in _fire(_bash(B, "prusik gate release-writer"))
    assert main_writer.release(project) is not None          # what the command itself does
    assert '"deny"' not in _fire(_bash(B, "git push"))
    assert main_writer.load(project)["session"] == B


def test_deny_message_note_is_keyed_to_the_command():
    holder = {"session": A}
    assert "judged whole" not in main_writer.deny_message(holder)
    assert "judged whole" not in main_writer.deny_message(holder, "echo release-writer")
    assert "judged whole" in main_writer.deny_message(
        holder, "prusik gate release-writer && git push")


# ---- fix-round status takes --feature ------------------------------------------------

def _run_cli(argv, capsys):
    old = sys.argv
    sys.argv = ["prusik", *argv]
    try:
        try:
            rc = cli.main()
        except SystemExit as e:
            rc = e.code
    finally:
        sys.argv = old
    return rc, capsys.readouterr()


def test_fix_round_status_accepts_feature_and_prints_the_same(project, capsys):
    _copy_sprint_config(project)
    rc_bare, bare = _run_cli(["gate", "fix-round", "status"], capsys)
    rc_feat, feat = _run_cli(["gate", "fix-round", "status", "--feature", "feat"], capsys)
    assert rc_bare == rc_feat == 0
    assert bare.out == feat.out and "no active fix-round" in feat.out
    assert "unrecognized arguments" not in feat.err
