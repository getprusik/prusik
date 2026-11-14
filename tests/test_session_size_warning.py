"""A sprint in a long session pays its context every turn: sprint-start and every advance say
when the current host session has grown past 120K tokens above its first turn (measured from
its registered transcript, main thread only), and each completed sprint records the largest
context it re-read.

moat-finding: fb-164e3509dce5
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from prusik import effort, gate, ledger, phases, platform_adapter as pa, report, usage
from tests._common import (
    _copy_sprint_config,
    _mktmp_project,
    _write_retro,
    discovery,
)

_SID = "sess-current"


# ---------- fixtures (the small helpers of tests/test_measured_token_usage.py) ----------

def _row(mid, ts, *, out=10, inp=1, cw=100, cr=1000, model="claude-opus-5-5"):
    return {"type": "assistant", "timestamp": ts,
            "message": {"id": mid, "model": model,
                        "usage": {"input_tokens": inp, "output_tokens": out,
                                  "cache_creation_input_tokens": cw,
                                  "cache_read_input_tokens": cr}}}


def _ctx(mid, ts, context):
    """A main-thread row whose turn context is exactly `context` (output is excluded)."""
    return _row(mid, ts, out=999, inp=0, cw=0, cr=context)


def _write_jsonl(path: Path, rows, extra_lines=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
        for line in extra_lines:
            f.write(line + "\n")


def _register(sid, transcript):
    path = usage.registry_path()
    reg = json.loads(path.read_text()) if path.exists() else {}
    reg[sid] = {"transcript_path": str(transcript), "first_seen": "2026-10-26T09:00:00+00:00",
                "last_seen": "2026-10-26T10:59:00+00:00"}
    path.write_text(json.dumps(reg))


def _ledger(root: Path, events):
    with open(root / ".sprint" / "ledger.jsonl", "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


@pytest.fixture
def proj(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".claude").mkdir(parents=True)
    (root / ".sprint").mkdir()
    (root / ".claude" / "sprint-config.yaml").write_text(
        (Path(__file__).resolve().parent.parent / "prusik" / "templates" / ".claude"
         / "sprint-config.yaml").read_text())
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    return root


@pytest.fixture
def session(proj, tmp_path, monkeypatch):
    """The current session, registered, whose transcript the test fills."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", _SID)
    t = tmp_path / "cc" / f"{_SID}.jsonl"
    _register(_SID, t)
    return t


def _grown(t, growth):
    _write_jsonl(t, [_ctx("a", "2026-10-26T09:00:01Z", 50_000),
                     _ctx("b", "2026-10-26T09:30:00Z", 50_000 + growth)])


def _events(name):
    return [e for e in ledger.read_all() if e["event"] == name]


# ---------- the adapter ----------

def test_adapter_names_the_session_from_the_environment(monkeypatch):
    a = pa.ClaudeCodeAdapter()
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc")
    assert a.current_session_id() == "abc"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "")
    assert a.current_session_id() is None
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    assert a.current_session_id() is None


def test_the_suite_is_hermetic_about_the_host_session():
    import os
    assert "CLAUDE_CODE_SESSION_ID" not in os.environ


# ---------- session_context ----------

def test_turn_context_is_the_one_definition():
    assert usage.turn_context(1, 100, 1000) == 1101


def test_no_host_session_means_the_check_does_not_apply(proj):
    c = usage.session_context()
    assert c.applies is False and c.measured is False


def test_growth_is_latest_minus_first_main_turn(session):
    _write_jsonl(session, [_ctx("a", "2026-10-26T09:00:01Z", 50_000),
                           _ctx("b", "2026-10-26T09:30:00Z", 200_000)])
    c = usage.session_context()
    assert (c.applies, c.measured) == (True, True)
    assert (c.first, c.current, c.growth) == (50_000, 200_000, 150_000)


def test_partial_rows_merge_by_message_id_with_per_field_max(session):
    _write_jsonl(session, [
        _row("a", "2026-10-26T09:00:01Z", out=1, inp=1, cw=100, cr=0),
        _row("a", "2026-10-26T09:00:01Z", out=9, inp=0, cw=0, cr=900),
        _ctx("b", "2026-10-26T09:30:00Z", 5_000)])
    c = usage.session_context()
    assert c.first == 1 + 100 + 900 and c.current == 5_000


def test_a_subagent_at_400k_does_not_set_the_current_context(session):
    _write_jsonl(session, [_ctx("a", "2026-10-26T09:00:01Z", 50_000),
                           _ctx("b", "2026-10-26T09:30:00Z", 60_000)])
    sub = session.with_suffix("") / "subagents" / "agent-1.jsonl"
    _write_jsonl(sub, [_ctx("s1", "2026-10-26T09:40:00Z", 400_000)])
    sub.with_suffix(".meta.json").write_text(json.dumps({"agentType": "backend-builder"}))
    c = usage.session_context()
    assert c.measured and c.current == 60_000 and c.growth == 10_000


def test_unregistered_session_is_unmeasured_with_its_reason(proj, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "nobody")
    c = usage.session_context()
    assert c.applies and not c.measured
    assert "session nobody is not registered" in c.reason and "SessionStart" in c.reason


def test_missing_transcript_is_unmeasured(session):
    c = usage.session_context()                       # registered, file never written
    assert c.applies and not c.measured and "transcript unreadable" in c.reason


def test_corrupt_registry_is_unmeasured(session):
    usage.registry_path().write_text("{not json")
    c = usage.session_context()
    assert c.applies and not c.measured and "session registry unreadable" in c.reason


def test_a_transcript_with_no_main_usage_is_unmeasured(session):
    _write_jsonl(session, [{"type": "user", "timestamp": "2026-10-26T09:00:00Z"}])
    c = usage.session_context()
    assert c.applies and not c.measured and "no main-thread usage" in c.reason


def test_a_raising_adapter_is_a_reason_not_an_exception(session, monkeypatch):
    class Boom(pa.ClaudeCodeAdapter):
        def read_usage(self, transcript, role_names):
            raise RuntimeError("boom")

    monkeypatch.setattr(pa, "get_adapter", lambda: Boom())
    c = usage.session_context()
    assert c.applies and not c.measured
    assert c.reason == "session context read failed: RuntimeError: boom"


# ---------- _session_size_line ----------

def test_growth_at_the_threshold_prints_and_records_nothing(session):
    _grown(session, usage.SESSION_GROWTH_WARN)
    assert gate._session_size_line("feat", "building") is None
    assert _events("session_size_warned") == []


def test_growth_above_the_threshold_warns_and_records(session):
    _grown(session, usage.SESSION_GROWTH_WARN + 1)
    line = gate._session_size_line("feat", "building")
    assert "170k tokens" in line and "120k above its first turn" in line
    assert "every turn re-reads it" in line and "briefs/feat.md" in line and "ledger" in line
    (ev,) = _events("session_size_warned")
    assert ev["feature"] == "feat" and ev["phase"] == "building"
    assert ev["context"] == 50_000 + 120_001 and ev["growth"] == 120_001
    assert "session" not in ev


def test_no_host_session_prints_and_records_nothing(proj):
    assert gate._session_size_line("feat", "building") is None
    assert _events("session_size_warned") == []


def test_unmeasured_is_said_once_and_records_nothing(proj, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "nobody")
    line = gate._session_size_line("feat", "building")
    assert line.startswith("[prusik-gate] session context UNMEASURED: session nobody is not")
    assert _events("session_size_warned") == []


# ---------- sprint-start and advance, end to end ----------

@pytest.fixture
def project(monkeypatch):
    tmp = _mktmp_project()
    _copy_sprint_config(tmp)
    yield tmp
    shutil.rmtree(tmp)


def _ready_brief(tmp: Path, feature: str) -> None:
    (tmp / "briefs").mkdir(exist_ok=True)
    (tmp / "briefs" / f"{feature}.md").write_text(
        "## Goal\nFix the typo in the retry log message.\n\n"
        "## Success criteria\nLog reads correctly within 1 line changed.\n\n"
        "## Type\nbug_fix\n")
    report_ = tmp / "reports" / feature / "brief-critique.txt"
    report_.parent.mkdir(parents=True, exist_ok=True)
    report_.write_text("PASS\n")
    (tmp / ".sprint").mkdir(exist_ok=True)
    (tmp / ".sprint" / "dep-graph.json").write_text(json.dumps(
        {"forward": {}, "reverse": {}, "stats": {"by_language": {}}}))
    (tmp / "design").mkdir(exist_ok=True)
    (tmp / "design" / "map.md").write_text("map placeholder")
    discovery.fingerprint_map()


def _start_and_advance(tmp: Path, capsys) -> tuple[int, str, int, str]:
    _ready_brief(tmp, "f")
    capsys.readouterr()
    rc1 = gate.sprint_start(argparse.Namespace(feature="f", trivial=True))
    out1 = capsys.readouterr().out
    design = tmp / "design" / "f"
    design.mkdir(parents=True)
    (design / "trivial.md").write_text("## Change\nx\n\n## How verified\ny\n")
    (design / "scope.md").write_text("## Size\nS\n")
    rc2 = gate.advance(argparse.Namespace(phase="solo_execute", feature="f",
                                          allow_rewind=False))
    return rc1, out1, rc2, capsys.readouterr().out


def _register_in(tmp: Path, growth: int, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", _SID)
    t = tmp / "cc" / f"{_SID}.jsonl"
    _grown(t, growth)
    usage.registry_path().parent.mkdir(parents=True, exist_ok=True)
    _register(_SID, t)


def test_start_and_advance_warn_after_their_output_and_keep_their_rc(project, capsys,
                                                                    monkeypatch):
    _register_in(project, 150_000, monkeypatch)
    rc1, out1, rc2, out2 = _start_and_advance(project, capsys)
    assert (rc1, rc2) == (0, 0)
    assert out1.rstrip().splitlines()[-1].startswith("[prusik-gate] ⚠ this session's context")
    assert "Next: run scoping role" in out1 or "Trivial lane" in out1
    assert out2.rstrip().splitlines()[-1].startswith("[prusik-gate] ⚠ this session's context")
    assert "Advanced: scoping → solo_execute" in out2
    warned = _events("session_size_warned")
    assert [(e["phase"]) for e in warned] == ["scoping", "solo_execute"]


def test_below_the_threshold_output_is_identical_to_no_session(project, capsys, monkeypatch):
    _register_in(project, 10_000, monkeypatch)
    with_session = _start_and_advance(project, capsys)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    shutil.rmtree(project / "design" / "f")
    (project / ".sprint" / "state.json").unlink()
    without = _start_and_advance(project, capsys)
    strip = lambda s: "\n".join(  # noqa: E731 — drop the usage line's token counts
        ln for ln in s.splitlines() if "token usage" not in ln and " used " not in ln)
    assert with_session[0] == without[0] == 0 and with_session[2] == without[2] == 0
    assert strip(with_session[1]) == strip(without[1])
    assert strip(with_session[3]) == strip(without[3])
    assert _events("session_size_warned") == []


# ---------- peak_context: measure, snapshot, rollup, effort, export ----------

def _journey(feature="feat"):
    return [
        {"ts": "2026-10-26T10:00:00+00:00", "event": "sprint_started", "feature": feature},
        {"ts": "2026-10-26T10:10:00+00:00", "event": "phase_advance", "from_phase": "scoping",
         "to_phase": "solo_execute", "feature": feature},
        {"ts": "2026-10-26T10:20:00+00:00", "event": "phase_advance",
         "from_phase": "solo_execute", "to_phase": "reviewing", "feature": feature},
    ]


_NOW = datetime(2026, 10, 26, 11, 0, tzinfo=timezone.utc)


def test_peak_is_the_largest_main_context_inside_the_windows(proj, tmp_path):
    _ledger(proj, _journey())
    t = tmp_path / "cc" / "s1.jsonl"
    _write_jsonl(t, [_ctx("early", "2026-10-26T09:30:00Z", 900_000),    # before every window
                     _ctx("in1", "2026-10-26T10:15:00Z", 70_000),
                     _ctx("in2", "2026-10-26T10:25:00Z", 90_000),
                     _ctx("in3", "2026-10-26T10:26:00Z", 80_000)])
    sub = t.with_suffix("") / "subagents" / "agent-1.jsonl"
    _write_jsonl(sub, [_ctx("sub", "2026-10-26T10:15:00Z", 400_000)])    # subagent, inside
    sub.with_suffix(".meta.json").write_text(json.dumps({"agentType": "backend-builder"}))
    _register("s1", t)
    u = usage.measure("feat", now=_NOW)
    assert u.measured and u.peak_context == 90_000
    assert u.breakdown()["peak_context"] == 90_000


def test_no_main_row_in_the_windows_is_none_and_serialises_as_null(proj, tmp_path):
    _ledger(proj, _journey())
    t = tmp_path / "cc" / "s1.jsonl"
    _write_jsonl(t, [_ctx("early", "2026-10-26T09:30:00Z", 900_000)])
    sub = t.with_suffix("") / "subagents" / "agent-1.jsonl"
    _write_jsonl(sub, [_ctx("sub", "2026-10-26T10:15:00Z", 400_000)])
    sub.with_suffix(".meta.json").write_text(json.dumps({"agentType": "backend-builder"}))
    _register("s1", t)
    u = usage.measure("feat", now=_NOW)
    assert u.measured and u.peak_context is None
    assert json.loads(json.dumps(u.breakdown()))["peak_context"] is None


def test_sprint_complete_snapshot_carries_the_peak(proj, tmp_path, monkeypatch):
    _ledger(proj, _journey())
    phases.set_sprint_state({"phase": "integrating", "feature": "feat"})
    t = tmp_path / "cc" / "s1.jsonl"
    _write_jsonl(t, [_ctx("a", "2026-10-26T10:15:00Z", 70_000)])
    _register("s1", t)
    monkeypatch.setattr(gate, "_run_success_criteria", lambda *a, **k: (True, []))
    _write_retro(ledger.project_root(), "feat")
    assert gate.sprint_complete(argparse.Namespace(feature="feat", duration_min=None,
                                                   tokens=None, escalated=False)) == 0
    done = [r for r in ledger.read_all() if r["event"] == "sprint_complete"][-1]
    assert done["actual"]["usage"]["peak_context"] == 70_000


def _complete(feature, ts, snap, tokens=1000):
    return {"ts": ts, "event": "sprint_complete", "feature": feature,
            "actual": {"tokens": tokens, "usage": snap}}


def test_rollup_carries_the_peak_per_sprint_and_none_when_absent():
    recs = [
        _complete("old", "2026-10-01T10:00:00+00:00", {"by_phase": {}}),        # pre-peak
        _complete("good", "2026-10-02T10:00:00+00:00", {"peak_context": 123_456}),
        _complete("str", "2026-10-03T10:00:00+00:00", {"peak_context": "9"}),
        _complete("bool", "2026-10-04T10:00:00+00:00", {"peak_context": True}),
        _complete("null", "2026-10-05T10:00:00+00:00", {"peak_context": None}),
        {"ts": "2026-10-06T10:00:00+00:00", "event": "sprint_complete", "feature": "unm",
         "actual": {"tokens_unmeasured": "gone"}},
        {"ts": "2026-10-07T10:00:00+00:00", "event": "sprint_complete", "feature": "pre"},
    ]
    peaks = {e["feature"]: e["peak_context"] for e in usage.rollup(recs)["per_sprint"]}
    assert peaks == {"old": None, "good": 123_456, "str": None, "bool": None, "null": None,
                     "unm": None, "pre": None}


def _effort_ledger(proj, extra=()):
    _ledger(proj, _journey("big") + _journey("small") + [
        _complete("big", "2026-10-26T11:00:00+00:00", {"peak_context": 250_000}, 3_000_000),
        _complete("small", "2026-10-26T11:05:00+00:00", {"by_phase": {}}, 5_000), *extra])


def test_effort_shows_the_peak_column_and_json(proj, capsys):
    _effort_ledger(proj)
    effort.run()
    out = capsys.readouterr().out
    assert "peak" in out and "250k" in out
    assert "largest main-thread context of one turn" in out
    small = next(ln for ln in out.splitlines() if ln.strip().startswith("small"))
    assert "—" in small
    effort.run(json_output=True)
    assert json.loads(capsys.readouterr().out)["peak_context"] == {"big": 250_000,
                                                                   "small": None}


def test_effort_latest_completion_wins_even_when_it_is_unmeasured(proj, capsys):
    _effort_ledger(proj, [
        {"ts": "2026-10-26T12:00:00+00:00", "event": "sprint_complete", "feature": "big",
         "actual": {"tokens_unmeasured": "transcript unreadable: /gone.jsonl"}}])
    effort.run(json_output=True)
    assert json.loads(capsys.readouterr().out)["peak_context"]["big"] is None


def test_the_anonymized_export_carries_the_peak_and_no_feature(proj):
    _ledger(proj, _journey("secret-x") + [
        _complete("secret-x", "2026-10-26T11:00:00+00:00", {"peak_context": 250_000})])
    u = report.export_payload(ledger.read_all(), "prod", proj)["usage"]
    assert u["per_sprint"][0]["peak_context"] == 250_000
    assert "feature" not in u["per_sprint"][0]
    assert "secret-x" not in json.dumps(u)
