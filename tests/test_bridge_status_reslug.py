"""`prusik bridge status` showed the OLD slug right after a successful
`bridge on --slug NEW`, because it read the env-first path (the session
`PRUSIK_BRIDGE_PATH`, loaded at start and stale until restart) instead of the
freshly-written settings.local.json. status must report the CONFIGURED path and
flag the session divergence.

moat-finding: fb-1e1badf0b6ef
"""

from __future__ import annotations

import json

from prusik import bridge


def _write_local(root, path):
    d = root / ".claude"
    d.mkdir(exist_ok=True)
    (d / "settings.local.json").write_text(
        json.dumps({"env": {bridge.ENV_VAR: str(path)}}) + "\n")


def test_configured_path_reads_settings_local(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(bridge.ENV_VAR, raising=False)
    _write_local(tmp_path, "/x/new-slug/bridge.md")
    assert str(bridge._configured_bridge_path()) == "/x/new-slug/bridge.md"


def test_status_reports_configured_not_stale_env(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write_local(tmp_path, "/x/2026-06-06-entity-type-support/bridge.md")  # NEW (on --slug)
    monkeypatch.setenv(bridge.ENV_VAR, "/x/2026-06-05-audit-log-ui/bridge.md")  # OLD session
    bridge.status()
    out = capsys.readouterr().out
    assert "2026-06-06-entity-type-support" in out          # reports the CONFIGURED path
    assert "session-active path differs" in out             # flags the stale env
    assert "restart" in out.lower()                          # tells operator why


def test_status_no_divergence_note_when_aligned(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write_local(tmp_path, "/x/same/bridge.md")
    monkeypatch.setenv(bridge.ENV_VAR, "/x/same/bridge.md")
    bridge.status()
    out = capsys.readouterr().out
    assert "session-active path differs" not in out          # aligned → no noise
