"""The headless spawn seam behind `prusik drive` (fb-13faaeb53496).

Every test runs a stub `claude` that is first on PATH and records its argv and env; none spawns
the real one. Each spawn test asserts the stub is the process that ran.

moat-finding: fb-13faaeb53496
"""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from prusik import main_writer, usage
from prusik import platform_adapter as pa

_STUB = """#!{python}
import json, os, sys, time
log = os.environ["STUB_LOG"]
with open(log, "a") as fh:
    fh.write(json.dumps({{"argv": sys.argv[1:], "env": dict(os.environ)}}) + "\\n")
if sys.argv[1:3] == ["auth", "status"]:
    print(json.dumps({{"loggedIn": True, "authMethod": os.environ.get("STUB_AUTH", "claude.ai"),
                       "apiKeySource": os.environ.get("STUB_SOURCE", "login")}}))
    sys.exit(0)
time.sleep(float(os.environ.get("STUB_SLEEP", "0")))
mode = os.environ.get("STUB_MODE", "ok")
if mode == "garbage":
    sys.stderr.write("boom: not json")
    print("not json")
elif mode == "denied":
    print(json.dumps({{"is_error": False, "result": "done", "permission_denials": [
        {{"tool_name": "Bash", "tool_input": {{"command": "git push"}}}}]}}))
elif mode == "error":
    print(json.dumps({{"is_error": True, "result": "max turns reached\\nmore", "permission_denials": []}}))
else:
    print(json.dumps({{"is_error": False, "result": "built it", "permission_denials": []}}))
"""


@pytest.fixture
def stub(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "claude"
    script.write_text(_STUB.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "stub.jsonl"
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
           "STUB_LOG": str(log)}
    root = tmp_path / "proj"
    root.mkdir()

    class S:
        pass
    s = S()
    s.env, s.log, s.root = env, log, root
    s.calls = lambda: [json.loads(x) for x in log.read_text().splitlines()]
    return s


def _req(stub, **kw):
    spec = pa.AgentSpec(name="backend-builder", description="builds",
                        tools=("Read", "Write", "Bash"), prompt="You are the builder.")
    base = dict(role="backend-builder", prompt="do the thing", model="sonnet",
                cwd=str(stub.root), root=str(stub.root), session_id="sess-1",
                agent_spec=spec, timeout_s=30, env=stub.env)
    base.update(kw)
    return pa.SpawnRequest(**base)


def test_spawn_runs_claude_with_the_headless_flags(stub):
    adapter = pa.ClaudeCodeAdapter()
    res = adapter.spawn(_req(stub))
    call = stub.calls()[-1]                    # the stub ran: the log is its only writer
    argv = call["argv"]
    assert argv[0:2] == ["-p", "do the thing"]
    for flag, value in (("--model", "sonnet"), ("--permission-mode", "auto"),
                        ("--permission-prompts", "none"), ("--output-format", "json"),
                        ("--session-id", "sess-1"), ("--agent", "backend-builder")):
        assert argv[argv.index(flag) + 1] == value, flag
    agents_path = Path(argv[argv.index("--agents") + 1])
    assert agents_path.parent == stub.root / ".sprint" / "drive-agents"
    assert not agents_path.exists(), "the agents file is deleted afterwards"
    assert res.rc == 0 and not res.is_error and res.text == "built it" and not res.timed_out


def test_agents_file_holds_the_spec_while_the_child_runs(stub, tmp_path):
    # Capture the file's content from inside the stub run: wrap the stub to copy it.
    script = Path(stub.env["PATH"].split(os.pathsep)[0]) / "claude"
    body = script.read_text().replace(
        "mode = os.environ",
        "import shutil\nif '--agents' in sys.argv:\n"
        "    shutil.copy(sys.argv[sys.argv.index('--agents') + 1], os.environ['STUB_LOG'] + '.agents')\n"
        "mode = os.environ")
    script.write_text(body)
    pa.ClaudeCodeAdapter().spawn(_req(stub))
    data = json.loads(Path(str(stub.log) + ".agents").read_text())
    assert data == {"backend-builder": {"description": "builds",
                                        "prompt": "You are the builder.",
                                        "tools": ["Read", "Write", "Bash"]}}


def test_no_model_flag_when_the_tier_is_off(stub):
    pa.ClaudeCodeAdapter().spawn(_req(stub, model=None))
    assert "--model" not in stub.calls()[-1]["argv"]


def test_result_parsing_denials_errors_garbage_and_timeout(stub):
    a = pa.ClaudeCodeAdapter()
    stub.env["STUB_MODE"] = "denied"
    r = a.spawn(_req(stub))
    assert r.denials and r.denials[0]["tool_name"] == "Bash" and not r.is_error
    stub.env["STUB_MODE"] = "error"
    r = a.spawn(_req(stub))
    assert r.is_error and r.text.startswith("max turns reached")
    stub.env["STUB_MODE"] = "garbage"
    r = a.spawn(_req(stub))
    assert r.is_error and "boom" in r.text and not r.timed_out
    stub.env.update(STUB_MODE="ok", STUB_SLEEP="30")
    t0 = time.monotonic()
    r = a.spawn(_req(stub, timeout_s=1, tick_s=1))
    assert r.timed_out and r.is_error and r.rc == -1
    assert time.monotonic() - t0 < 15, "the child is killed, not waited out"
    assert len(stub.calls()) == 4


def test_spawn_env_by_auth(stub):
    a = pa.ClaudeCodeAdapter()
    base = {"PATH": "/x", "ANTHROPIC_API_KEY": "k", "CLAUDE_CODE_SESSION_ID": "parent"}
    sub = a.spawn_env("claude.ai", base)
    assert "ANTHROPIC_API_KEY" not in sub and "CLAUDE_CODE_SESSION_ID" not in sub
    assert sub["PATH"] == "/x" and "ANTHROPIC_API_KEY" in base, "the caller's env is untouched"
    key = a.spawn_env("api_key", base)
    assert key["ANTHROPIC_API_KEY"] == "k" and "CLAUDE_CODE_SESSION_ID" not in key
    with pytest.raises(ValueError):
        a.spawn_env("oauth", base)


def test_auth_method_parses_the_status_json(stub):
    a = pa.ClaudeCodeAdapter()
    stub.env.update(STUB_AUTH="api_key", STUB_SOURCE="ANTHROPIC_API_KEY")
    assert a.auth_method(stub.env) == ("api_key", "ANTHROPIC_API_KEY")
    stub.env.pop("STUB_SOURCE")
    stub.env["STUB_AUTH"] = "claude.ai"
    assert a.auth_method(stub.env) == ("claude.ai", "login")
    assert stub.calls()[-1]["argv"] == ["auth", "status"]


def test_auth_method_fails_closed_on_unreadable_status(stub, tmp_path):
    bad = tmp_path / "badbin"
    bad.mkdir()
    s = bad / "claude"
    s.write_text("#!/bin/sh\necho nope\n")
    s.chmod(0o755)
    env = {**stub.env, "PATH": f"{bad}{os.pathsep}{stub.env['PATH']}"}
    with pytest.raises(pa.SpawnError):
        pa.ClaudeCodeAdapter().auth_method(env)


def test_on_tick_runs_each_slice_and_a_timeout_kills(stub):
    stub.env["STUB_SLEEP"] = "3.5"
    ticks = []
    r = pa.ClaudeCodeAdapter().spawn(_req(stub, tick_s=1, on_tick=lambda: ticks.append(1)))
    assert not r.timed_out and len(ticks) >= 2
    assert len(stub.calls()) == 1


# ── usage attribution ──────────────────────────────────────────────────────────────────────

def _row(mid, ts, out=10):
    return json.dumps({"type": "assistant", "timestamp": ts, "message": {
        "id": mid, "model": "claude-sonnet-4", "usage": {
            "input_tokens": 1, "output_tokens": out, "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0}}})


def _seed_window(root):
    from tests._common import _write_ledger
    t0 = datetime.now(timezone.utc) - timedelta(minutes=10)
    _write_ledger(root, [
        {"ts": t0.isoformat(), "event": "sprint_started", "feature": "f"},
        {"ts": (t0 + timedelta(minutes=1)).isoformat(), "event": "phase_advance",
         "feature": "f", "to_phase": "building"}])


def test_tagged_session_main_rows_are_attributed_to_its_role(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    _seed_window(tmp_path)
    ts = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
    for name in ("tagged", "plain"):
        (tmp_path / f"{name}.jsonl").write_text(_row(f"m-{name}", ts) + "\n")
    usage.tag_session("sid-tagged", role="backend-builder", feature="f")
    entry = json.loads(usage.registry_path().read_text())["sid-tagged"]
    assert entry["role"] == "backend-builder" and entry["feature"] == "f"
    assert "transcript_path" not in entry
    # the hook later registers the transcript and keeps the role
    usage.register(pa.SessionRef(session="sid-tagged",
                                 transcript=str(tmp_path / "tagged.jsonl")))
    usage.register(pa.SessionRef(session="sid-plain",
                                 transcript=str(tmp_path / "plain.jsonl")))
    entry = json.loads(usage.registry_path().read_text())["sid-tagged"]
    assert entry["role"] == "backend-builder" and entry["transcript_path"]
    u = usage.measure("f")
    assert u.measured, u.reason
    assert u.by_role.get("backend-builder") == 11 and u.by_role.get("main") == 11


def test_tag_only_entry_without_a_transcript_is_skipped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    _seed_window(tmp_path)
    usage.tag_session("sid-x", role="integrator", feature="f")
    u = usage.measure("f")
    assert not u.measured and "no registered session was active" in (u.reason or "")


# ── single-writer lease delegation ─────────────────────────────────────────────────────────

def _age(root, minutes):
    lease = main_writer.load(root)
    lease["refreshed_at"] = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    main_writer._write(root, lease)


def test_delegate_acts_as_the_holder_and_a_stranger_is_denied(tmp_path):
    H, D, X = "holder", "delegate", "stranger"
    assert main_writer.check_and_touch(tmp_path, H, feature="f", phase="building")[2] == "acquired"
    assert main_writer.delegate(tmp_path, H, D, role="backend-builder", feature="f")
    _age(tmp_path, 10)
    before = main_writer.load(tmp_path)["refreshed_at"]
    ok, prior, action = main_writer.check_and_touch(tmp_path, D)
    lease = main_writer.load(tmp_path)
    assert (ok, prior, action) == (True, None, "refreshed")
    assert lease["session"] == H and lease["delegates"] == [D]
    assert lease["refreshed_at"] > before and lease["feature"] == "f"
    assert main_writer.check_and_touch(tmp_path, D)[0], "a second write by the delegate"
    main_writer.check_and_touch(tmp_path, H, feature="f", phase="reviewing")
    lease = main_writer.load(tmp_path)
    assert lease["delegates"] == [D] and lease["phase"] == "reviewing", \
        "a holder refresh keeps the delegates"
    ok, holder, action = main_writer.check_and_touch(tmp_path, X)
    assert (ok, action) == (False, "denied") and holder["session"] == H


def test_delegate_is_refused_for_a_non_holder_or_stale_lease(tmp_path):
    assert not main_writer.delegate(tmp_path, "h", "d", role="r", feature="f"), "no lease"
    main_writer.check_and_touch(tmp_path, "h")
    assert not main_writer.delegate(tmp_path, "other", "d", role="r", feature="f")
    _age(tmp_path, 31)
    assert not main_writer.delegate(tmp_path, "h", "d", role="r", feature="f")
    assert not main_writer.heartbeat(tmp_path, "h"), "a stale lease is not revived"


def test_delegates_do_not_outlive_a_taken_over_holder(tmp_path):
    main_writer.check_and_touch(tmp_path, "h")
    main_writer.delegate(tmp_path, "h", "d", role="r", feature="f")
    main_writer.delegate(tmp_path, "h", "d", role="r", feature="f")
    assert main_writer.load(tmp_path)["delegates"] == ["d"], "deduplicated"
    _age(tmp_path, 31)
    ok, prior, action = main_writer.check_and_touch(tmp_path, "new")
    assert (ok, action) == (True, "stolen_stale") and prior["session"] == "h"
    assert "delegates" not in main_writer.load(tmp_path)
    assert not main_writer.check_and_touch(tmp_path, "d")[0], "the old delegate is a stranger"


def test_heartbeat_refreshes_only_for_the_live_holder(tmp_path):
    main_writer.check_and_touch(tmp_path, "h")
    _age(tmp_path, 20)
    old = main_writer.load(tmp_path)["refreshed_at"]
    assert not main_writer.heartbeat(tmp_path, "someone-else")
    assert main_writer.load(tmp_path)["refreshed_at"] == old
    assert main_writer.heartbeat(tmp_path, "h")
    assert main_writer.load(tmp_path)["refreshed_at"] > old
