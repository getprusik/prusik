"""Smoke tests — exercise the engine without Claude Code in the loop.

Domain: bridge (opt-in live-collaboration channel; default OFF).

Run: uv run python -m pytest tests/test_bridge.py -v
Or run the whole suite: uv run python -m pytest tests/ -v

Shared helpers live in tests/_common.py (private; pytest does not
collect leading-underscore modules). v0.23.0 split tests/test_smoke.py
by domain to keep individual files navigable.
"""

# noqa: F401 — wildcard imports below intentionally re-export everything
# from _common (prusik modules, helpers, the tempfile/json/os toolbelt).
# F401 individual unused-name warnings would obscure the rest.
from tests._common import *  # noqa: F401,F403,E402
from tests._common import (  # noqa: F401,E402
    argparse, contextlib, io, json, os, re, shutil, subprocess, sys,
    tempfile, time, Path,
    schema, phases, triage, discovery, gate, watchdog, issues,
    kit_init, kit_uninstall, kit_toggle, consistency, agents_doctor,
    kit_refresh, kit_pause, kit_permissions, kit_brief_lint,
    kit_fix_round, kit_bridge, kit_detect, kit_doctor, ledger_digest,
    _mktmp_project, _copy_sprint_config, _wt_file, _write_ledger,
    _capture_stdout, _capture_stderr, _VALID_BRIEF,
)


# ---------- bridge (opt-in live-collaboration channel) ----------

def _clear_bridge_env():
    os.environ.pop("PRUSIK_BRIDGE_PATH", None)


def _mkbridge(tmp):
    """Point the bridge at a file via PRUSIK_BRIDGE_PATH."""
    bp = tmp / "bridge.md"
    bp.write_text("# header\n")
    os.environ["PRUSIK_BRIDGE_PATH"] = str(bp)
    return bp


def test_bridge_poll_injects_author_entries():
    tmp = _mktmp_project()
    try:
        bp = _mkbridge(tmp)
        bp.write_text(bp.read_text() + """
### [09:00] live-cc → prusik-author — QUESTION
Context: foo
Detail:  bar

### [09:02] prusik-author → live-cc — FIX
Verdict: bug confirmed
Action:  do X
""")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = kit_bridge.poll()
        assert rc == 0
        ctx = json.loads(buf.getvalue())["hookSpecificOutput"]["additionalContext"]
        assert "prusik-author → live-cc" in ctx
        assert "do X" in ctx
        assert "foo" not in ctx  # live-cc entries are not echoed back
    finally:
        _clear_bridge_env()
        shutil.rmtree(tmp)


def test_bridge_poll_silent_when_no_new_content():
    tmp = _mktmp_project()
    try:
        bp = _mkbridge(tmp)
        bp.write_text(bp.read_text() + """
### [09:02] prusik-author → live-cc — FIX
ok
""")
        kit_bridge.poll()  # first poll processes content
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = kit_bridge.poll()  # second poll: no new content → silent
        assert rc == 0
        assert buf.getvalue() == ""
    finally:
        _clear_bridge_env()
        shutil.rmtree(tmp)


def test_bridge_poll_picks_up_only_new_entries_on_second_call():
    tmp = _mktmp_project()
    try:
        bp = _mkbridge(tmp)
        bp.write_text(bp.read_text() + """
### [09:02] prusik-author → live-cc — FIX
first fix
""")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            kit_bridge.poll()
        assert "first fix" in buf.getvalue()
        with open(bp, "a") as f:
            f.write("\n### [09:10] prusik-author → live-cc — GUIDANCE\nsecond msg\n")
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            kit_bridge.poll()
        second = buf2.getvalue()
        assert "second msg" in second
        assert "first fix" not in second, "old entry should not be echoed again"
    finally:
        _clear_bridge_env()
        shutil.rmtree(tmp)


# ---------- v0.11.2: bridge append never silently loses a message ----------

def test_v0112_bridge_write_appends_and_verifies():
    tmp = _mktmp_project()
    try:
        bp = _mkbridge(tmp)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = kit_bridge.write_entry("live-cc", "BUG", "concrete defect body")
        assert rc == 0
        txt = bp.read_text()
        assert "### [" in txt and "live-cc → prusik-author — BUG" in txt
        assert "concrete defect body" in txt
    finally:
        _clear_bridge_env()
        shutil.rmtree(tmp)


def test_v0112_bridge_concurrent_writers_no_loss():
    """The race regression: many simultaneous writers — every entry must
    land intact (flock-serialized + atomic O_APPEND), none lost/torn."""
    tmp = _mktmp_project()
    try:
        bp = _mkbridge(tmp)
        import threading
        n = 12
        barrier = threading.Barrier(n)

        def _w(i):
            barrier.wait()  # maximize contention
            with contextlib.redirect_stdout(io.StringIO()):
                kit_bridge.write_entry("live-cc", "OBSERVATION", f"datapoint-{i}")

        threads = [threading.Thread(target=_w, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        txt = bp.read_text()
        for i in range(n):
            assert txt.count(f"datapoint-{i}\n") == 1, \
                f"datapoint-{i} lost or duplicated under contention"
        assert txt.count("live-cc → prusik-author — OBSERVATION") == n
    finally:
        _clear_bridge_env()
        shutil.rmtree(tmp)


def test_v0112_bridge_unverifiable_append_is_loud_not_silent(monkeypatch):
    tmp = _mktmp_project()
    try:
        bp = _mkbridge(tmp)
        monkeypatch.setattr(kit_bridge, "_locked_append_verified",
                            lambda *a, **k: False)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = kit_bridge.write_entry("live-cc", "BUG", "must-not-vanish")
        assert rc == 1, "unverifiable append must NOT return success"
        assert "NOT silently lost" in err.getvalue()
        errlog = bp.parent / "errors.log"
        assert errlog.exists() and "must-not-vanish" in errlog.read_text()
    finally:
        _clear_bridge_env()
        shutil.rmtree(tmp)


# ---------- on/off toggle (default OFF) ----------

def test_bridge_default_off():
    tmp = _mktmp_project()
    try:
        kit_init.run()
        assert kit_bridge.is_enabled() is False, "bridge must be OFF until turned on"
    finally:
        _clear_bridge_env()
        shutil.rmtree(tmp)


def test_bridge_on_off_toggle():
    tmp = _mktmp_project()
    try:
        kit_init.run()
        with contextlib.redirect_stdout(io.StringIO()):
            assert kit_bridge.on(slug="unit-test") == 0
        assert kit_bridge.is_enabled() is True
        local = json.loads((tmp / ".claude" / "settings.local.json").read_text())
        assert "PRUSIK_BRIDGE_PATH" in local.get("env", {})
        assert "KIT_BRIDGE_PATH" not in local.get("env", {})
        with contextlib.redirect_stdout(io.StringIO()):
            assert kit_bridge.off() == 0
        assert kit_bridge.is_enabled() is False
    finally:
        _clear_bridge_env()
        shutil.rmtree(tmp)


def test_bridge_enable_disable_are_aliases_for_on_off():
    tmp = _mktmp_project()
    try:
        kit_init.run()
        with contextlib.redirect_stdout(io.StringIO()):
            kit_bridge.enable(slug="unit-test")
        assert kit_bridge.is_enabled() is True
        settings = json.loads((tmp / ".claude" / "settings.json").read_text())
        ups = settings.get("hooks", {}).get("UserPromptSubmit", [])
        assert any(h.get("command") == "prusik bridge poll"
                   for b in ups for h in b.get("hooks", []))
        with contextlib.redirect_stdout(io.StringIO()):
            kit_bridge.disable()
        assert kit_bridge.is_enabled() is False
    finally:
        _clear_bridge_env()
        shutil.rmtree(tmp)


# ---------- vocabulary ----------

def test_bridge_write_entry_uses_prusik_author():
    tmp = _mktmp_project()
    try:
        bp = _mkbridge(tmp)
        with contextlib.redirect_stdout(io.StringIO()):
            kit_bridge.write_entry("live-cc", "QUESTION", "why did it fail?")
        text = bp.read_text()
        assert "live-cc → prusik-author — QUESTION" in text
        assert "why did it fail?" in text
    finally:
        _clear_bridge_env()
        shutil.rmtree(tmp)


def test_bridge_write_entry_rejects_legacy_role():
    """Clean break: the legacy `kit-author` role is no longer accepted."""
    tmp = _mktmp_project()
    try:
        _mkbridge(tmp)
        with contextlib.redirect_stderr(io.StringIO()):
            rc = kit_bridge.write_entry("kit-author", "FIX", "x")
        assert rc == 1, "legacy role must be rejected after the clean break"
    finally:
        _clear_bridge_env()
        shutil.rmtree(tmp)
