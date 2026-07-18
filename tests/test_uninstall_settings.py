"""uninstall restores a pre-merge settings.json — provably clean exit for repos
already using Claude Code (v0.52.1). Evidence-pulled: a real verification showed
init-merge→uninstall left settings.json modified on the adopter shape."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from tests._common import _mktmp_project  # noqa: F401,E402
from prusik import init as kit_init
from prusik import uninstall as kit_uninstall

_REAL_SETTINGS_SHAPE = (
    '{\n  "hooks": {"PreToolUse": [{"matcher": "*", "hooks": ['
    '{"type": "command", "command": "echo hi"}]}]},\n'
    '  "permissions": {"allow": ["Read"]}\n}\n'
)


def _git(d, *a):
    subprocess.run(["git", "-C", str(d), *a], capture_output=True, check=True)


def _repo(settings: str | None) -> Path:
    d = Path(tempfile.mkdtemp(prefix="kit-us-"))
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t.t")
    _git(d, "config", "user.name", "t")
    (d / "README.md").write_text("seed\n")
    if settings is not None:
        (d / ".claude").mkdir()
        (d / ".claude" / "settings.json").write_text(settings)
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


def _manifest(d) -> dict:
    return json.loads((d / ".claude" / ".prusik-manifest.json").read_text())


# ---------- the headline: pre-existing settings.json is reverted ----------

def test_preexisting_settings_restored_byte_identical():
    d = _repo(_REAL_SETTINGS_SHAPE)
    assert _in(d, lambda: kit_init.run()) == 0
    # init merged → settings.json changed, and the manifest recorded the restore
    assert (d / ".claude" / "settings.json").read_text() != _REAL_SETTINGS_SHAPE
    assert "settings_restore" in _manifest(d)
    assert _in(d, lambda: kit_uninstall.run()) == 0
    # uninstall reverted it to the exact original
    assert (d / ".claude" / "settings.json").read_text() == _REAL_SETTINGS_SHAPE
    # and the tree is clean (only the committed README + original settings)
    out = subprocess.run(["git", "-C", str(d), "status", "--porcelain"],
                         capture_output=True, text=True).stdout
    assert out.strip() == "", f"residue: {out!r}"


# ---------- drift safety: a hand-edit since merge is NOT clobbered ----------

def test_hand_edited_settings_not_clobbered():
    d = _repo(_REAL_SETTINGS_SHAPE)
    _in(d, lambda: kit_init.run())
    # operator edits settings.json AFTER prusik merged
    sp = d / ".claude" / "settings.json"
    sp.write_text(sp.read_text().replace('"echo hi"', '"echo EDITED"'))
    _in(d, lambda: kit_uninstall.run())
    txt = sp.read_text()
    assert "EDITED" in txt, "must not clobber a post-merge hand-edit"
    # manifest kept so the operator can retry
    assert (d / ".claude" / ".prusik-manifest.json").exists()


def test_force_reverts_even_after_hand_edit():
    d = _repo(_REAL_SETTINGS_SHAPE)
    _in(d, lambda: kit_init.run())
    sp = d / ".claude" / "settings.json"
    sp.write_text(sp.read_text().replace('"echo hi"', '"echo EDITED"'))
    _in(d, lambda: kit_uninstall.run(force=True))
    assert sp.read_text() == _REAL_SETTINGS_SHAPE


# ---------- fresh repo: still clean, no settings_restore ----------

def test_fresh_repo_no_settings_restore_and_file_removed():
    d = _repo(settings=None)
    _in(d, lambda: kit_init.run())
    # init CREATED settings.json (tracked in files[]), so no restore recorded
    assert "settings_restore" not in _manifest(d)
    _in(d, lambda: kit_uninstall.run())
    assert not (d / ".claude" / "settings.json").exists()
    out = subprocess.run(["git", "-C", str(d), "status", "--porcelain"],
                         capture_output=True, text=True).stdout
    assert out.strip() == ""
