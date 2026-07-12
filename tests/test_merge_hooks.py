"""init --merge-hooks (v0.53.3, finding #5) — a repo with its OWN hooks block
keeps it, and prusik's gate hooks are NOT wired by default (the inert-harness
case). --merge-hooks APPENDS prusik's alongside, non-destructive + reversible."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from tests._common import _mktmp_project  # noqa: F401,E402
from prusik import refresh_merge
from prusik import init as kit_init
from prusik import uninstall as kit_uninstall

_SCOPE_GUARD = json.dumps({
    "hooks": {"PreToolUse": [{
        "matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit",
        "hooks": [{"type": "command", "command": "./scripts/scope-guard.sh"}],
    }]},
}, indent=2) + "\n"


def _git(d, *a):
    subprocess.run(["git", "-C", str(d), *a], capture_output=True, check=True)


def _repo_with_hooks() -> Path:
    d = Path(tempfile.mkdtemp(prefix="kit-mh-"))
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t.t")
    _git(d, "config", "user.name", "t")
    (d / ".claude").mkdir()
    (d / ".claude" / "settings.json").write_text(_SCOPE_GUARD)
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "seed")
    return d


def _in(d, fn):
    cwd = os.getcwd()
    try:
        os.chdir(d)
        os.environ["CLAUDE_PROJECT_DIR"] = str(d)
        return fn()
    finally:
        os.chdir(cwd)


def _hooks(d) -> dict:
    return json.loads((d / ".claude" / "settings.json").read_text())["hooks"]


def _cmds(hooks, event):
    return [h.get("command") for g in hooks.get(event, []) for h in g["hooks"]]


# ---------- the surgical union ----------

def test_merge_hooks_appends_alongside_user():
    tmpl = {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "prusik gate pre-tool"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "prusik gate stop"}]}]}
    proj = {"PreToolUse": [{"matcher": "X", "hooks": [
        {"type": "command", "command": "./scope-guard.sh"}]}]}
    merged, added = refresh_merge._merge_hooks(tmpl, proj)
    assert _cmds(merged, "PreToolUse") == ["./scope-guard.sh", "prusik gate pre-tool"]
    assert _cmds(merged, "Stop") == ["prusik gate stop"]
    assert set(added) == {"PreToolUse", "Stop"}


def test_merge_hooks_idempotent():
    tmpl = {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "prusik gate pre-tool"}]}]}
    proj = {"PreToolUse": [
        {"matcher": "X", "hooks": [{"type": "command", "command": "x.sh"}]},
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "prusik gate pre-tool"}]}]}
    _, added = refresh_merge._merge_hooks(tmpl, proj)
    assert added == []   # already present → no duplicate


# ---------- init honesty: inert by default ----------

def test_init_without_merge_hooks_leaves_prusik_hooks_unwired():
    d = _repo_with_hooks()
    assert _in(d, lambda: kit_init.run()) == 0
    h = _hooks(d)
    # user's scope-guard preserved; prusik's gate NOT wired
    assert "./scripts/scope-guard.sh" in _cmds(h, "PreToolUse")
    assert not any("prusik gate" in c for c in _cmds(h, "PreToolUse"))
    assert kit_init._prusik_hooks_wired(d / ".claude" / "settings.json") is False


def test_init_with_merge_hooks_wires_alongside_scope_guard():
    d = _repo_with_hooks()
    assert _in(d, lambda: kit_init.run(merge_hooks=True)) == 0
    h = _hooks(d)
    pre = _cmds(h, "PreToolUse")
    assert "./scripts/scope-guard.sh" in pre and "prusik gate pre-tool" in pre
    assert "prusik gate stop" in _cmds(h, "Stop")
    assert kit_init._prusik_hooks_wired(d / ".claude" / "settings.json") is True


# ---------- reversibility: uninstall restores scope-guard byte-for-byte ----------

def test_uninstall_after_merge_hooks_restores_original():
    d = _repo_with_hooks()
    _in(d, lambda: kit_init.run(merge_hooks=True))
    assert (d / ".claude" / "settings.json").read_text() != _SCOPE_GUARD  # changed
    _in(d, lambda: kit_uninstall.run())
    # back to the exact original — scope-guard intact, no prusik hooks/perms
    assert (d / ".claude" / "settings.json").read_text() == _SCOPE_GUARD
    out = subprocess.run(["git", "-C", str(d), "status", "--porcelain"],
                         capture_output=True, text=True).stdout
    assert out.strip() == "", f"residue: {out!r}"


# ---------- --minimal-perms: only the load-bearing entry ----------

def _allow(d):
    return json.loads(
        (d / ".claude" / "settings.json").read_text()).get("permissions", {}).get("allow", [])


def test_minimal_perms_adds_only_harness_required():
    d = _repo_with_hooks()                       # has scope-guard, NO permissions block
    assert _in(d, lambda: kit_init.run(merge_hooks=True, minimal_perms=True)) == 0
    assert _allow(d) == ["Bash(prusik *)"]       # ONLY the load-bearing entry
    h = _hooks(d)                                 # hooks still wired alongside scope-guard
    assert "prusik gate pre-tool" in _cmds(h, "PreToolUse")
    assert "./scripts/scope-guard.sh" in _cmds(h, "PreToolUse")


def test_default_perms_merge_full_allowlist():
    d = _repo_with_hooks()
    _in(d, lambda: kit_init.run(merge_hooks=True))   # no --minimal-perms
    allow = _allow(d)
    assert "Bash(prusik *)" in allow and len(allow) > 10   # full convenience set
    assert "Write(**)" in allow                            # broad — what minimal avoids


def test_minimal_perms_plus_merge_hooks_is_reversible():
    d = _repo_with_hooks()
    _in(d, lambda: kit_init.run(merge_hooks=True, minimal_perms=True))
    _in(d, lambda: kit_uninstall.run())
    assert (d / ".claude" / "settings.json").read_text() == _SCOPE_GUARD   # byte-for-byte


# ---------- doctor catches the inert-harness state ----------

def test_doctor_flags_inert_harness():
    from prusik import doctor
    d = _repo_with_hooks()
    _in(d, lambda: kit_init.run())                       # init WITHOUT --merge-hooks
    _, ev = doctor._score_session_lifecycle(d, d / ".claude")
    assert any("INERT HARNESS" in e for e in ev)


def test_doctor_no_inert_flag_when_hooks_wired():
    from prusik import doctor
    d = _repo_with_hooks()
    _in(d, lambda: kit_init.run(merge_hooks=True))        # wired
    _, ev = doctor._score_session_lifecycle(d, d / ".claude")
    assert not any("INERT HARNESS" in e for e in ev)


# ---------- the MultiEdit matcher hole ----------

def test_template_matcher_includes_multiedit():
    from prusik.init import TEMPLATE_ROOT
    s = json.loads((TEMPLATE_ROOT / ".claude" / "settings.json").read_text())
    matcher = s["hooks"]["PreToolUse"][0]["matcher"]
    assert "MultiEdit" in matcher   # else a gate is bypassable via MultiEdit
