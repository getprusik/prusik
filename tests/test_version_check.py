"""Update-availability check + `prusik update` (v0.84.0) — multi-host distribution.
Read-only, no phone-home; the network call is mocked so tests stay offline."""

from __future__ import annotations

import io
from contextlib import redirect_stdout

from prusik import version_check, update_cmd


def test_parse_and_is_newer():
    assert version_check._parse("v0.83.0") == (0, 83, 0)
    assert version_check._parse("0.83.0") == (0, 83, 0)
    assert version_check._parse("nightly") is None
    assert version_check.is_newer("0.84.0", installed="0.83.0")
    assert version_check.is_newer("0.83.1", installed="0.83.0")
    assert not version_check.is_newer("0.83.0", installed="0.83.0")
    assert not version_check.is_newer("0.82.0", installed="0.83.0")   # older


def test_check_offline_returns_none(monkeypatch):
    monkeypatch.setattr(version_check, "latest_release", lambda timeout=3.0: None)
    installed, latest, newer = version_check.check()
    assert latest is None and newer is False and installed


def test_update_when_newer_instructs_upgrade_and_does_not_refresh(monkeypatch):
    from prusik import version_check as _vc
    monkeypatch.setattr(_vc, "check", lambda timeout=3.0: ("0.83.0", "0.84.0", True))
    called = {"refresh": False}
    import prusik.refresh as _r
    monkeypatch.setattr(_r, "run", lambda *a, **k: called.__setitem__("refresh", True) or 0)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = update_cmd.run()
    out = buf.getvalue()
    assert rc == 0
    assert "newer release is available: 0.84.0" in out
    assert "upgrade the package" in out
    assert called["refresh"] is False          # don't sync stale templates


def test_update_when_current_refreshes_and_reminds_restart(monkeypatch):
    from prusik import version_check as _vc
    monkeypatch.setattr(_vc, "check", lambda timeout=3.0: ("0.84.0", "0.84.0", False))
    called = {"refresh": False}
    import prusik.refresh as _r
    monkeypatch.setattr(_r, "run", lambda *a, **k: called.__setitem__("refresh", True) or 0)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = update_cmd.run()
    out = buf.getvalue()
    assert rc == 0
    assert "is current" in out
    assert called["refresh"] is True
    assert "restart your Claude Code session" in out


def test_nudge_throttled_and_cached(tmp_path, monkeypatch):
    (tmp_path / ".sprint").mkdir()
    calls = {"n": 0}
    def fake_latest(timeout=2.0):
        calls["n"] += 1
        return "v9.9.9"                       # always "newer" than installed
    monkeypatch.setattr(version_check, "latest_release", fake_latest)
    # first call hits the network + caches
    n1 = version_check.nudge_if_stale(tmp_path)
    assert n1 and "9.9.9" in n1 and calls["n"] == 1
    # second call within throttle window → cached, NO new network call
    n2 = version_check.nudge_if_stale(tmp_path)
    assert n2 and calls["n"] == 1
    # current version → no nudge
    monkeypatch.setattr(version_check, "latest_release", lambda timeout=2.0: "0.0.1")
    (tmp_path / ".sprint" / ".update-check.json").unlink()   # bust cache
    assert version_check.nudge_if_stale(tmp_path) is None
