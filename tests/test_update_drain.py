"""`prusik update` — the shipped-fix drain must run regardless of a pending newer
release, and must never fail silently (v0.197.32)."""

from __future__ import annotations

import shutil

from tests._common import _mktmp_project  # noqa: F401
from prusik import update_cmd, version_check, feedback_store


def test_update_drains_even_when_a_newer_release_is_available(monkeypatch):
    """moat-finding: fb-6dbf6ccc167f — a fix already present in THIS engine must drain
    on `prusik update` even when a newer release exists. The old `newer` branch
    early-returned BEFORE the closer, so already-shipped fixes rotted as stale-open
    findings until a second update run adopters rarely make (an adopter carried 10
    findings whose fixes shipped in 0.144–0.190 stuck open on 0.197.x for this reason)."""
    tmp = _mktmp_project()
    try:
        called = []
        monkeypatch.setattr(version_check, "check", lambda t: ("0.1.0", "9.9.9", True))
        monkeypatch.setattr(version_check, "changelog_text", lambda t: None)
        monkeypatch.setattr(update_cmd, "_close_shipped_findings",
                            lambda t: called.append(t))
        rc = update_cmd.run(timeout=0)
        assert called, "the drain must run even when a newer release is available"
        assert rc == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_closer_surfaces_errors_instead_of_silently_swallowing(monkeypatch, capsys):
    """The closer is best-effort (never BREAKS update) but must not HIDE a failure — a
    bare `except: pass` would let the drain no-op invisibly (fail-closed principle)."""
    tmp = _mktmp_project()
    try:
        def boom(root):
            raise RuntimeError("drain-boom")
        monkeypatch.setattr(feedback_store, "load_all", boom)
        update_cmd._close_shipped_findings(0.0)      # must NOT raise
        out = capsys.readouterr().out
        assert "drain-boom" in out or "couldn't close the loop" in out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
