"""fb-1d621fced521 — `prusik update` syncs a pin the engine is ahead of, instead of being
refused by it.

In a project pinned behind the installed engine, the entrypoint's pin check refused `update`
and told the operator to run `prusik refresh` first, though update runs refresh itself: the
refusal blocked the one command whose job is to fix the lag. The read-only reports `metrics`
and `catches` were refused the same way. Now, when the engine is AHEAD (never BEHIND), update
moves the pin before it acts, re-checks, and refuses if the pin still mismatches. That is the
contract `gate sprint-start` has, through the one shared `refresh.sync_pin`. The two reports
write nothing, so they run while AHEAD.

Adversarial: BEHIND refuses all three and leaves the pin file byte-identical, a sync that leaves
the pin mismatched refuses before the closer runs, and `catch` (a ledger write) still refuses.

moat-finding: fb-1d621fced521
"""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import prusik
from prusik import __main__ as cli
from prusik import gate, pin, refresh, update_cmd

_RUNNING = prusik.__version__
_MAJ, _MIN, _PAT = pin.parse_version(_RUNNING)
_AHEAD_PIN = f"{_MAJ}.{_MIN - 1}.0"        # the engine is AHEAD of this pin
_BEHIND_PIN = f"{_MAJ}.{_MIN + 1}.0"       # the engine is BEHIND this pin
_READ = {"session_id": "t", "hook_event_name": "PreToolUse", "tool_name": "Read",
         "tool_input": {"file_path": "README.md"}}
# The release check must not depend on the network: every HTTP client (urllib, gh) goes
# through a proxy that refuses at once, so update sees "couldn't reach" deterministically.
_OFFLINE = {"HTTPS_PROXY": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9",
            "HTTP_PROXY": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9",
            "NO_PROXY": "", "no_proxy": ""}


def _run(root: Path, *args: str, payload: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root), **_OFFLINE}
    return subprocess.run([sys.executable, "-m", "prusik", *args],
                          input=json.dumps(payload) if payload is not None else "",
                          capture_output=True, text=True, cwd=str(root), env=env,
                          check=False, timeout=180)


def _manifest(root: Path) -> Path:
    return root / ".claude" / ".prusik-manifest.json"


def _set_pin(root: Path, version: str) -> None:
    m = json.loads(_manifest(root).read_text())
    m["template_surface_version"] = version
    _manifest(root).write_text(json.dumps(m))


def _initialised(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    p = _run(root, "init")
    assert p.returncode == 0, (p.stdout, p.stderr)
    assert pin.resolve(root)[0] == _RUNNING
    return root


def _ran(p: subprocess.CompletedProcess) -> None:
    assert "refusing" not in p.stderr, p.stderr
    assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)


def _refused(p: subprocess.CompletedProcess, *names: str) -> None:
    assert p.returncode == 2, (p.returncode, p.stdout, p.stderr)
    assert "[prusik] refusing:" in p.stderr, p.stderr
    for n in names:
        assert n in p.stderr, (n, p.stderr)


def _ledger_bytes(root: Path) -> bytes | None:
    lp = root / ".sprint" / "ledger.jsonl"
    return lp.read_bytes() if lp.exists() else None


# ── update moves a lagging pin, then acts ─────────────────────────────────────────────

def test_update_while_ahead_syncs_the_pin_and_the_next_hook_runs(tmp_path):
    root = _initialised(tmp_path)
    _set_pin(root, _AHEAD_PIN)
    _refused(_run(root, "gate", "pre-tool", payload=_READ), _AHEAD_PIN)
    p = _run(root, "update", "--timeout", "2")
    _ran(p)
    assert pin.resolve(root)[0] == _RUNNING
    # One sync, not a sync and then a second refresh in the package-current branch.
    assert (p.stdout + p.stderr).count("[prusik-refresh] prusik") == 1, p.stdout
    _ran(_run(root, "gate", "pre-tool", payload=_READ))


def _in_process(tmp_path: Path, monkeypatch, pin_version: str) -> tuple[Path, list[str]]:
    root = _initialised(tmp_path)
    _set_pin(root, pin_version)
    monkeypatch.chdir(root)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.setattr("prusik.ledger.project_root", lambda: root)
    monkeypatch.setattr("prusik.version_check.check", lambda timeout: (_RUNNING, None, False))
    calls: list[str] = []
    monkeypatch.setattr(update_cmd, "_close_shipped_findings",
                        lambda timeout: calls.append(f"close@{pin.resolve(root)[0]}"))
    return root, calls


def test_the_sync_runs_before_the_closer(tmp_path, monkeypatch):
    root, calls = _in_process(tmp_path, monkeypatch, _AHEAD_PIN)
    assert update_cmd.run(timeout=0.1) == 0
    assert calls == [f"close@{_RUNNING}"]          # the closer saw the synced pin


def test_a_sync_that_leaves_the_pin_mismatched_refuses_before_the_closer(
        tmp_path, monkeypatch, capsys):
    root, calls = _in_process(tmp_path, monkeypatch, _AHEAD_PIN)
    monkeypatch.setattr(refresh, "run", lambda **kw: 0)       # "synced", pin unmoved
    assert update_cmd.run(timeout=0.1) == 2
    assert calls == []
    err = capsys.readouterr().err
    assert "[prusik] refusing:" in err and _AHEAD_PIN in err
    assert pin.resolve(root)[0] == _AHEAD_PIN


def test_sprint_start_and_update_share_one_sync_rule():
    assert not hasattr(gate, "_pin_synced")
    assert "refresh.sync_pin(" in inspect.getsource(gate.sprint_start)
    assert "refresh.sync_pin(" in inspect.getsource(update_cmd.run)


# ── read-only reports run while AHEAD; BEHIND refuses everything ───────────────────────

def test_metrics_and_catches_run_while_ahead_and_write_nothing(tmp_path):
    root = _initialised(tmp_path)
    _set_pin(root, _AHEAD_PIN)
    pin_before, ledger_before = _manifest(root).read_bytes(), _ledger_bytes(root)
    for cmd in ("metrics", "catches"):
        _ran(_run(root, cmd))
    assert _manifest(root).read_bytes() == pin_before
    assert _ledger_bytes(root) == ledger_before
    # `catch` labels a catch by appending to the ledger: a write, so it still refuses.
    _refused(_run(root, "catch", "c-000000", "--true"), _AHEAD_PIN, "ahead of the pin")
    assert _ledger_bytes(root) == ledger_before


def test_behind_refuses_update_metrics_and_catches_and_leaves_the_pin(tmp_path):
    root = _initialised(tmp_path)
    _set_pin(root, _BEHIND_PIN)
    before = _manifest(root).read_bytes()
    for argv in (("update", "--timeout", "2"), ("metrics",), ("catches",)):
        _refused(_run(root, *argv), _BEHIND_PIN, "behind the pin")
    assert _manifest(root).read_bytes() == before


def test_the_ahead_allowances_are_declared_next_to_the_exempt_list():
    assert cli.PIN_SYNCS_WHEN_AHEAD == (("gate", "sprint-start"), ("update",))
    assert cli.PIN_READS_WHEN_AHEAD == ("metrics", "catches")
    assert "update" not in cli.PIN_EXEMPT and "metrics" not in cli.PIN_EXEMPT
