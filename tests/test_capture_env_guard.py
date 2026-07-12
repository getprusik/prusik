"""`prusik gate capture` runs the command through a NON-interactive `bash -c` with no
inherited login PATH, so a toolchain installed via nvm/volta/fnm (`npx`/`pnpm`) exited
127 — and that 127 was recorded as a usable evidence entry. Two fixes: (1) enrich the
capture PATH from the login shell so the tool resolves; (2) a command-not-found guard —
exit 127 means the tool never ran, so record NO entry and diagnose.

fb-53f161606abc.

moat-finding: fb-53f161606abc
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from prusik import gate, ledger


def test_capture_command_not_found_records_no_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "project_root", lambda: tmp_path)
    monkeypatch.setattr(gate, "_CAPTURE_PATH_CACHE", "/usr/bin")  # skip the login-PATH spawn
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=127, stdout="", stderr="bash: pnpm: command not found"))

    rc = gate.capture(SimpleNamespace(command=["pnpm", "test"], kind="tests",
                                      feature="f", phase="regression"))

    assert rc == 127                                  # transparent exit, not laundered
    # a tool that never ran is NOT evidence — no reports/ dir, no evidence file
    assert not (tmp_path / "reports").exists()


def test_capture_passes_enriched_path_to_subprocess(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "project_root", lambda: tmp_path)
    monkeypatch.setattr(gate, "_CAPTURE_PATH_CACHE", "/opt/toolchain/bin:/usr/bin")
    seen = {}

    def fake_run(argv, **kw):
        seen["path"] = (kw.get("env") or {}).get("PATH")
        return SimpleNamespace(returncode=0, stdout="3 passed", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    gate.capture(SimpleNamespace(command=["pytest"], kind="tests",
                                 feature="f", phase="regression"))
    assert seen["path"] == "/opt/toolchain/bin:/usr/bin"


def test_capture_env_path_merges_login_path_toolchain_first(monkeypatch):
    monkeypatch.setattr(gate, "_CAPTURE_PATH_CACHE", None)
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("SHELL", "/bin/bash")
    # profile banner noise around the NUL-delimited PATH must not corrupt extraction
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="welcome\n\x00/opt/homebrew/bin:/usr/bin\x00\n", stderr=""))

    parts = gate._capture_env_path().split(":")

    assert parts[0] == "/opt/homebrew/bin"            # toolchain dir first
    assert parts.count("/usr/bin") == 1               # de-duped against current PATH


def test_capture_env_path_falls_back_on_failure(monkeypatch):
    monkeypatch.setattr(gate, "_CAPTURE_PATH_CACHE", None)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    def boom(*a, **k):
        raise OSError("no shell")

    monkeypatch.setattr(subprocess, "run", boom)
    # a failed login-shell probe must not break capture — fall back to the current PATH
    assert gate._capture_env_path() == "/usr/bin:/bin"


def test_capture_env_path_rejects_garbage(monkeypatch):
    monkeypatch.setattr(gate, "_CAPTURE_PATH_CACHE", None)
    monkeypatch.setenv("PATH", "/usr/bin")
    # no NUL sentinels in output → can't trust it → fall back, don't inject garbage
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="some profile error text", stderr=""))
    assert gate._capture_env_path() == "/usr/bin"
