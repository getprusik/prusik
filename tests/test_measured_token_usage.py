"""Token usage is measured from the host's session transcripts, never an agent claim.

The per-phase `budget_tokens` watchdog summed a `tokens` field no ledger event carried, and
`actual.tokens` came only from an agent-passed `--tokens` no sprint ever set — every budget
was a silent no-op. Now SessionStart/Stop register the session transcript; the adapter reads
usage (deduplicated by message id across sessions, per-field max); usage is attributed to the
feature's ledger phase windows; sprint-complete records it, the watchdog enforces budgets with
it (loud when unmeasurable), and refresh retires the shipped guessed defaults.

moat-finding: fb-a1b7db140a5e
"""

from __future__ import annotations

import argparse
import builtins
import difflib
import io
import json
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from prusik import effort, gate, ledger, phases, platform_adapter as pa, usage, watchdog

_REPO = Path(__file__).resolve().parent.parent
_TEMPLATE_CFG = _REPO / "prusik" / "templates" / ".claude" / "sprint-config.yaml"
_PRESET = (_REPO / "prusik" / "templates" / ".claude" / "sprint-config-presets"
           / "fastapi-postgres.yaml")

# The phases block of the template as shipped through v0.227.1 (tag v0.227.1, lines 125-219),
# verbatim: indented sequences, blank phase separators, inline comments — the layout every
# untouched adopter config has, so retirement must delete ONLY the budget_tokens lines.
_SHIPPED_PHASES = r"""phases:
  - name: scoping
    writable:
      - "design/{feature}/scope.md"
      - "design/{feature}/trivial.md"
      - "reports/{feature}/scope-approval.txt"
    exit_artifacts:
      - path: "design/{feature}/scope.md"
        validator: scope_schema
      - path: "reports/{feature}/scope-approval.txt"
        must_contain: "APPROVED"
    # v0.11.0 #2 — proportional-ceremony lane. Used ONLY when the sprint
    # was started with `--trivial` (brief Type guarded to bug_fix/doc/
    # config/test/chore). Collapses scope.md + scope-critic into one
    # lightweight artifact; the orchestrator then advances scoping →
    # solo_execute (skipping triage + planning + plan-critic). The
    # reviewing correctness floor (regression + conventions PASS) and the
    # brief-critic pre-sprint gate are NOT skipped.
    trivial_exit_artifacts:
      - path: "design/{feature}/trivial.md"
        required_sections:
          - "## Change"
          - "## How verified"

  - name: triage
    writable:
      - "decisions/{feature}.json"
    exit_artifacts:
      - path: "decisions/{feature}.json"
        validator: triage_decision

  - name: planning
    writable:
      - "design/{feature}/plan.md"
      - "reports/{feature}/plan-approval.txt"
    exit_artifacts:
      - path: "design/{feature}/plan.md"
        required_sections:
          - "## Modules touched"
          - "## Test plan"
          - "## Risks"
      - path: "reports/{feature}/plan-approval.txt"
        must_contain: "APPROVED"
    budget_tokens: 150000

  - name: solo_execute
    writable:
      - "worktrees/solo/**"
    deny_commands:
      - "git push"
      - "git merge"
    budget_tokens: 400000

  - name: building
    writable:
      - "worktrees/*/**"
    deny_commands:
      - "git push"
      - "git merge"
    budget_tokens: 1000000

  - name: reviewing
    writable:
      - "reports/{feature}/**"
    exit_artifacts:
      - path: "reports/{feature}/regression.txt"
        must_contain: "PASS"
      # v0.12.0 (F) — a PASS token is honored only against prusik-captured
      # execution evidence (real exit + executed count from the tool's own
      # output, bound to the worktree hash). Closes the false-clean where a
      # gate emits PASS while the gated work silently did not run. The
      # reviewer produces this via `prusik gate capture` (see role-spec); it is
      # NOT agent-writable narration. Carry-forward-coherent (v0.11.0 #1): a
      # verdict carried at an unchanged hash satisfies it without re-run.
      - path: "reports/{feature}/regression.evidence.json"
        validator: execution_evidence
      - path: "reports/{feature}/conventions.txt"
        must_contain: "PASS"
      - path: "reports/{feature}/conventions.evidence.json"
        validator: execution_evidence
    budget_tokens: 300000

  - name: integrating
    writable:
      - "design/{feature}/retro.md"
      - "**"  # integrator has broad write; controlled by role permissions
    exit_artifacts:
      - path: "design/{feature}/retro.md"
        required_sections:
          - "## What happened"
          - "## What surprised"
          - "## Updates to CLAUDE.md"

# Pre-sprint gates — run BEFORE the sprint enters scoping.
# Engine checks artifacts; /sprint-start command tells Claude which role to invoke.
"""


# ---------- fixtures ----------

def _row(mid, ts, *, out=10, inp=1, cw=100, cr=1000, model="claude-opus-5-5"):
    return {"type": "assistant", "timestamp": ts,
            "message": {"id": mid, "model": model,
                        "usage": {"input_tokens": inp, "output_tokens": out,
                                  "cache_creation_input_tokens": cw,
                                  "cache_read_input_tokens": cr}}}


_UNIT = 1 + 10 + 100 + 1000          # one default _row()'s tokens


def _write_jsonl(path: Path, rows, extra_lines=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
        for line in extra_lines:
            f.write(line + "\n")


@pytest.fixture
def proj(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".claude").mkdir(parents=True)
    (root / ".sprint").mkdir()
    shutil.copy(_TEMPLATE_CFG, root / ".claude" / "sprint-config.yaml")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    return root


def _ledger(root: Path, events):
    with open(root / ".sprint" / "ledger.jsonl", "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _journey(feature="feat"):
    """scoping 10:00-10:10 · solo_execute 10:10-10:20 · reviewing 10:20-10:30 · rewind to
    solo_execute 10:30-10:40 · reviewing 10:40-open. Ledger ts are `+00:00`; every transcript
    row below is `Z` — the two formats meet at this boundary (Python 3.10 rejects `Z`)."""
    return [
        {"ts": "2026-10-26T10:00:00+00:00", "event": "sprint_started", "feature": feature},
        {"ts": "2026-10-26T10:10:00+00:00", "event": "phase_advance", "from_phase": "scoping",
         "to_phase": "solo_execute", "feature": feature},
        {"ts": "2026-10-26T10:20:00+00:00", "event": "phase_advance",
         "from_phase": "solo_execute", "to_phase": "reviewing", "feature": feature},
        {"ts": "2026-10-26T10:30:00+00:00", "event": "phase_rewind", "from_phase": "reviewing",
         "to_phase": "solo_execute", "feature": feature},
        {"ts": "2026-10-26T10:40:00+00:00", "event": "phase_advance",
         "from_phase": "solo_execute", "to_phase": "reviewing", "feature": feature},
    ]


def _register(sid, transcript, last_seen="2026-10-26T10:59:00+00:00"):
    path = usage.registry_path()
    reg = json.loads(path.read_text()) if path.exists() else {}
    reg[sid] = {"transcript_path": str(transcript), "first_seen": "2026-10-26T09:00:00+00:00",
                "last_seen": last_seen}
    path.write_text(json.dumps(reg))


def _age(path: Path, iso: str):
    t = datetime.fromisoformat(iso).timestamp()
    os.utime(path, (t, t))


_NOW = datetime(2026, 10, 26, 11, 0, tzinfo=timezone.utc)


def _stdin(monkeypatch, payload):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))


# ---------- registry + hooks ----------

def test_register_upserts_beside_the_ledger_and_refreshes_last_seen(proj):
    assert usage.registry_path() == ledger.ledger_path().parent / "sessions.json"
    usage.register(pa.SessionRef(session="s1", transcript="/t/s1.jsonl"))
    first = json.loads(usage.registry_path().read_text())["s1"]
    assert first["transcript_path"] == "/t/s1.jsonl" and first["first_seen"] == first["last_seen"]
    time.sleep(0.002)
    usage.register(pa.SessionRef(session="s1", transcript="/t/s1.jsonl"))
    again = json.loads(usage.registry_path().read_text())["s1"]
    assert again["first_seen"] == first["first_seen"] and again["last_seen"] > first["last_seen"]


def test_register_none_is_a_noop_and_a_corrupt_registry_is_moved_aside(proj):
    usage.register(None)
    assert not usage.registry_path().exists()
    usage.registry_path().write_text("{not json")
    usage.register(pa.SessionRef(session="s1", transcript="/t/s1.jsonl"))
    assert "s1" in json.loads(usage.registry_path().read_text())
    assert usage.registry_path().with_name("sessions.json.corrupt").read_text() == "{not json"


@pytest.mark.parametrize("payload", [{}, {"session_id": "s"}, {"transcript_path": "/t"},
                                     {"session_id": 3, "transcript_path": None}])
def test_session_ref_rejects_partial_payloads(payload):
    assert pa.ClaudeCodeAdapter().session_ref(payload) is None


def test_session_ref_from_a_hook_payload():
    assert pa.ClaudeCodeAdapter().session_ref(
        {"session_id": "s", "transcript_path": "/t.jsonl", "hook_event_name": "Stop"}
    ) == pa.SessionRef(session="s", transcript="/t.jsonl")


@pytest.mark.parametrize("extra,paused", [({}, False), ({"stop_hook_active": True}, False),
                                          ({}, True)])
def test_stop_registers_on_every_turn_shape(proj, monkeypatch, extra, paused):
    """Continued turns (stop_hook_active) and paused sprints still refresh the registry."""
    phases.set_sprint_state({"phase": "solo_execute", "feature": "feat"})
    if paused:
        from prusik import pause
        monkeypatch.setattr(pause, "is_paused", lambda *a, **k: True)
    _stdin(monkeypatch, {"session_id": "s1", "transcript_path": "/t/s1.jsonl", **extra})
    gate.stop()
    assert "s1" in json.loads(usage.registry_path().read_text())


def test_session_start_registers_and_survives_an_unreadable_stdin(proj, monkeypatch, capsys):
    _stdin(monkeypatch, {"session_id": "s2", "transcript_path": "/t/s2.jsonl"})
    assert gate.session_start() == 0
    assert "s2" in json.loads(usage.registry_path().read_text())

    class Broken(io.StringIO):
        def read(self, *a):
            raise OSError("reading from stdin while output is captured")

    monkeypatch.setattr("sys.stdin", Broken())
    assert gate.session_start() == 0                    # pytest-capture shape: no crash
    assert "additionalContext" in capsys.readouterr().out


def test_stop_reads_no_transcript_and_not_the_ledger(proj, monkeypatch, tmp_path):
    """Stop runs every turn: registration touches only the registry."""
    _ledger(proj, _journey())
    phases.set_sprint_state({"phase": "solo_execute", "feature": "feat"})
    transcript = tmp_path / "cc" / "s1.jsonl"
    _write_jsonl(transcript, [_row("m1", "2026-10-26T10:15:00Z")])
    opened: list[str] = []
    real_open = builtins.open

    def spy(file, *a, **k):
        opened.append(os.path.realpath(str(file)))
        return real_open(file, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(ledger, "read_all", lambda *a, **k: pytest.fail("Stop read the ledger"))
    _stdin(monkeypatch, {"session_id": "s1", "transcript_path": str(transcript)})
    gate.stop()
    assert os.path.realpath(str(transcript)) not in opened
    assert "s1" in json.loads(usage.registry_path().read_text())


# ---------- adapter: transcript → usage records ----------

def test_adapter_yields_rows_roles_and_counts_malformed(tmp_path):
    main = tmp_path / "s1.jsonl"
    _write_jsonl(main, [
        _row("m1", "2026-10-26T10:01:00Z", out=3),          # partial content-block row
        _row("m1", "2026-10-26T10:01:00Z", out=480),        # final row
        {"type": "user", "timestamp": "2026-10-26T10:01:01Z", "message": {}},
        _row("m0", "2026-10-26T10:00:30Z", inp=0, out=0, cw=0, cr=0, model="<synthetic>"),
    ], extra_lines=['{"type": "assistant", broken'])
    sub = tmp_path / "s1" / "subagents"
    metas = {"a1": {"agentType": "general-purpose", "description": "Regression-sentinel review"},
             "a2": {"agentType": "conventions-enforcer"},
             "a3": {"agentType": "general-purpose", "description": "Explore the codebase"},
             "a4": None}
    for i, (aid, meta) in enumerate(metas.items(), start=2):
        _write_jsonl(sub / f"agent-{aid}.jsonl", [_row(f"m{i}", f"2026-10-26T10:0{i}:00Z")])
        if meta is not None:
            (sub / f"agent-{aid}.meta.json").write_text(json.dumps(meta))

    got = pa.ClaudeCodeAdapter().read_usage(str(main), {"regression-sentinel",
                                                        "conventions-enforcer"})
    assert got.unreadable == [] and got.malformed == 1
    by_id: dict[str, list] = {}
    for r in got.records:
        by_id.setdefault(r.message_id, []).append(r)
    assert "m0" not in by_id                                   # zero usage skipped
    assert sorted(r.output for r in by_id["m1"]) == [3, 480]   # both rows; policy merges
    assert {mid: rs[0].role for mid, rs in by_id.items()} == {
        "m1": "main", "m2": "regression-sentinel", "m3": "conventions-enforcer",
        "m4": "general-purpose", "m5": "subagent"}


def test_adapter_counts_every_malformed_row_shape_and_never_raises(tmp_path):
    """A live transcript can end mid-character, and any row can be odd: each is counted as
    malformed (or skipped), never an exception into sprint-complete / the watchdog."""
    good = _row("ok", "2026-10-26T10:01:00Z")
    bad_rows = [
        {"type": "assistant", "timestamp": "2026-10-26T10:02:00Z", "message": "a string"},
        {"type": "assistant", "timestamp": "2026-10-26T10:02:00Z",
         "message": {"id": "m", "usage": [1, 2]}},
        {"type": "assistant", "timestamp": "2026-10-26T10:02:00Z",
         "message": {"id": "m", "usage": {"output_tokens": "lots"}}},
        {"type": "assistant", "timestamp": "2026-10-26T10:02:00Z",
         "message": {"id": {"not": "a string"}, "usage": {"output_tokens": 5}}},
        {"type": "assistant", "timestamp": None, "message": {"id": "m", "usage": {"output_tokens": 5}}},
    ]
    main = tmp_path / "s1.jsonl"
    _write_jsonl(main, [good, *bad_rows])
    with open(main, "ab") as f:                        # a stray byte + a cut-off character
        f.write(b'{"type": "assistant", "x": "\xff"}\n{"type": "assistant", "m": "\xe2\x82')
    got = pa.ClaudeCodeAdapter().read_usage(str(main), set())
    assert [r.message_id for r in got.records] == ["ok"]
    # +2: the stray-byte row (decodes, but has no usage) and the truncated tail line
    assert got.malformed == len(bad_rows) + 2
    assert got.unreadable == []


def test_measure_is_total(proj, monkeypatch):
    _ledger(proj, _journey())
    monkeypatch.setattr(usage, "_measure", lambda *a, **k: {}["boom"])
    u = usage.measure("feat", now=_NOW)
    assert not u.measured and u.reason.startswith("usage read failed: KeyError")


def test_adapter_reports_a_missing_main_transcript(tmp_path):
    got = pa.ClaudeCodeAdapter().read_usage(str(tmp_path / "gone.jsonl"), set())
    assert got.unreadable == [str(tmp_path / "gone.jsonl")] and not got.records
    assert pa.ClaudeCodeAdapter().last_write(str(tmp_path / "gone.jsonl")) is None


def test_adapter_last_write_includes_subagent_files(tmp_path):
    main = tmp_path / "s1.jsonl"
    _write_jsonl(main, [])
    sub = tmp_path / "s1" / "subagents" / "agent-a1.jsonl"
    _write_jsonl(sub, [])
    _age(main, "2026-10-26T09:00:00+00:00")
    _age(sub, "2026-10-26T10:30:00+00:00")
    assert pa.ClaudeCodeAdapter().last_write(str(main)) == pytest.approx(sub.stat().st_mtime)


# ---------- policy: dedup, windows, measurability ----------

def test_measure_takes_per_field_max_and_dedups_across_sessions(proj, tmp_path):
    _ledger(proj, _journey())
    s1, s2 = tmp_path / "cc" / "s1.jsonl", tmp_path / "cc" / "s2.jsonl"
    _write_jsonl(s1, [_row("m1", "2026-10-26T10:05:00Z", out=3),
                      _row("m1", "2026-10-26T10:05:00Z", out=480),
                      _row("m1", "2026-10-26T10:05:00Z", out=5)])   # a late smaller row
    # s2 is a resumed session: it copies m1 (original ts) and adds its own m2
    _write_jsonl(s2, [_row("m1", "2026-10-26T10:05:00Z", out=480),
                      _row("m2", "2026-10-26T10:12:00Z")])
    _register("s1", s1)
    _register("s2", s2)
    u = usage.measure("feat", now=_NOW)
    m1 = 1 + 480 + 100 + 1000
    assert u.measured and u.output == 480 + 10
    assert u.tokens == m1 + _UNIT
    assert u.by_phase == {"scoping": m1, "solo_execute": _UNIT}
    assert u.by_session == {"s1": m1, "s2": _UNIT}
    assert (u.input, u.cache_write, u.cache_read) == (2, 200, 2000)


def test_measure_attributes_rewinds_and_the_open_phase(proj, tmp_path):
    _ledger(proj, _journey())
    t = tmp_path / "cc" / "s1.jsonl"
    _write_jsonl(t, [
        _row("pre", "2026-10-26T09:59:59Z"),            # before the sprint: not counted
        _row("a", "2026-10-26T10:15:00Z"),              # solo_execute (1st visit)
        _row("b", "2026-10-26T10:25:00Z"),              # reviewing (1st)
        _row("c", "2026-10-26T10:35:00Z"),              # solo_execute (after the rewind)
        _row("d", "2026-10-26T10:50:00Z"),              # reviewing, the open phase
    ])
    _register("s1", t)
    u = usage.measure("feat", now=_NOW)
    assert u.measured and u.by_phase == {"solo_execute": 2 * _UNIT, "reviewing": 2 * _UNIT}
    solo = usage.measure("feat", phase="solo_execute", now=_NOW)
    assert solo.tokens == 2 * _UNIT and solo.by_phase == {"solo_execute": 2 * _UNIT}


def test_window_boundary_is_half_open(proj, tmp_path):
    _ledger(proj, _journey())
    t = tmp_path / "cc" / "s1.jsonl"
    _write_jsonl(t, [_row("edge", "2026-10-26T10:10:00.000Z")])   # == the advance ts
    _register("s1", t)
    assert usage.measure("feat", now=_NOW).by_phase == {"solo_execute": _UNIT}


def test_an_unparseable_record_ts_is_counted_not_dropped_silently(proj, tmp_path):
    _ledger(proj, _journey())
    t = tmp_path / "cc" / "s1.jsonl"
    _write_jsonl(t, [_row("ok", "2026-10-26T10:15:00Z"), _row("bad", "yesterday-ish")])
    _register("s1", t)
    u = usage.measure("feat", now=_NOW)
    assert u.tokens == _UNIT and u.malformed_lines == 1


def test_mid_turn_session_is_measured_despite_an_earlier_last_seen(proj, tmp_path):
    """Stop fires at turn END; a phase entered and left mid-turn has no Stop inside it. The
    session's last_seen precedes the window, yet its transcript holds every in-window row."""
    _ledger(proj, _journey())
    t = tmp_path / "cc" / "s1.jsonl"
    _write_jsonl(t, [_row("a", "2026-10-26T10:15:00Z")])
    _register("s1", t, last_seen="2026-10-26T09:58:00+00:00")
    u = usage.measure("feat", phase="solo_execute", now=_NOW)
    assert u.measured and u.tokens == _UNIT


def test_a_background_subagent_keeps_an_idle_session_in_range(proj, tmp_path):
    _ledger(proj, _journey())
    main = tmp_path / "cc" / "s1.jsonl"
    _write_jsonl(main, [_row("old", "2026-10-26T09:00:00Z")])
    _age(main, "2026-10-26T09:00:00+00:00")             # main idle since before the sprint
    sub = tmp_path / "cc" / "s1" / "subagents" / "agent-a1.jsonl"
    _write_jsonl(sub, [_row("bg", "2026-10-26T10:15:00Z")])
    _register("s1", main, last_seen="2026-10-26T09:00:00+00:00")
    u = usage.measure("feat", now=_NOW)
    assert u.measured and u.tokens == _UNIT


def test_measure_is_unmeasured_with_a_reason(proj, tmp_path):
    assert "window" in usage.measure("feat", now=_NOW).reason          # no ledger journey
    _ledger(proj, _journey())
    u = usage.measure("feat", now=_NOW)
    assert not u.measured and "no registered session was active since" in u.reason
    stale = tmp_path / "cc" / "stale.jsonl"
    _write_jsonl(stale, [_row("x", "2026-10-25T00:00:00Z")])
    _age(stale, "2026-10-25T00:00:00+00:00")
    _register("stale", stale, last_seen="2026-10-25T00:00:00+00:00")
    assert "no registered session was active since" in usage.measure("feat", now=_NOW).reason
    _register("gone", tmp_path / "cc" / "gone.jsonl")                  # last_seen in window
    u = usage.measure("feat", now=_NOW)
    assert not u.measured and "gone.jsonl" in u.reason


def test_a_stale_missing_transcript_is_skipped(proj, tmp_path):
    _ledger(proj, _journey())
    _register("cleaned-up", tmp_path / "cc" / "old.jsonl", last_seen="2026-09-01T00:00:00+00:00")
    t = tmp_path / "cc" / "s1.jsonl"
    _write_jsonl(t, [_row("a", "2026-10-26T10:15:00Z")])
    _register("s1", t)
    u = usage.measure("feat", now=_NOW)
    assert u.measured and u.tokens == _UNIT


def test_phase_windows_share_effort_spans():
    ev = _journey()
    wins = effort.phase_windows(ev, "feat")
    closed = [s for s in effort.extract_spans(ev) if s["feature"] == "feat"]
    assert [(w["phase"], w["start"], w["end"]) for w in wins[:-1]] == \
        [(s["phase"], s["start"], s["end"]) for s in closed]
    assert wins[-1] == {"phase": "reviewing", "start": "2026-10-26T10:40:00+00:00", "end": None}


# ---------- sprint-complete: measured, never the agent claim ----------

def _complete(monkeypatch, tokens=None) -> int:
    monkeypatch.setattr(gate, "_run_success_criteria", lambda *a, **k: (True, []))
    return gate.sprint_complete(argparse.Namespace(feature="feat", duration_min=None,
                                                   tokens=tokens, escalated=False))


def _last_complete():
    return [r for r in ledger.read_all() if r["event"] == "sprint_complete"][-1]


def test_sprint_complete_records_measured_usage_without_the_flag(proj, tmp_path, monkeypatch):
    _ledger(proj, _journey())
    phases.set_sprint_state({"phase": "integrating", "feature": "feat"})
    t = tmp_path / "cc" / "s1.jsonl"
    _write_jsonl(t, [_row("a", "2026-10-26T10:15:00Z"), _row("b", "2026-10-26T10:45:00Z")])
    _register("s1", t)
    assert _complete(monkeypatch) == 0
    actual = _last_complete()["actual"]
    assert actual["tokens"] == 2 * _UNIT
    assert actual["usage"]["by_phase"] == {"solo_execute": _UNIT, "reviewing": _UNIT}
    assert set(actual["usage"]) >= {"input", "output", "cache_write", "cache_read",
                                    "by_role", "by_model", "by_session"}


def test_sprint_complete_never_records_the_tokens_claim(proj, tmp_path, monkeypatch, capsys):
    _ledger(proj, _journey())
    phases.set_sprint_state({"phase": "integrating", "feature": "feat"})
    t = tmp_path / "cc" / "s1.jsonl"
    _write_jsonl(t, [_row("a", "2026-10-26T10:15:00Z")])
    _register("s1", t)
    assert _complete(monkeypatch, tokens=123000) == 0
    assert _last_complete()["actual"]["tokens"] == _UNIT
    assert "--tokens ignored" in capsys.readouterr().err


def test_sprint_complete_survives_a_usage_failure(proj, monkeypatch, capsys):
    _ledger(proj, _journey())
    phases.set_sprint_state({"phase": "integrating", "feature": "feat"})
    monkeypatch.setattr(usage, "_measure", lambda *a, **k: 1 / 0)
    assert _complete(monkeypatch) == 0
    assert "usage read failed" in _last_complete()["actual"]["tokens_unmeasured"]
    assert "UNMEASURED" in capsys.readouterr().err


def test_sprint_complete_unmeasured_is_loud_and_recorded(proj, monkeypatch, capsys):
    _ledger(proj, _journey())
    phases.set_sprint_state({"phase": "integrating", "feature": "feat"})
    assert _complete(monkeypatch) == 0                        # telemetry never blocks
    actual = _last_complete()["actual"]
    assert actual["tokens"] is None
    assert "no registered session was active since" in actual["tokens_unmeasured"]
    assert "UNMEASURED" in capsys.readouterr().err


# ---------- gate advance: one advisory line ----------

def test_advance_prints_the_left_phase_usage_and_never_blocks_on_it(proj, tmp_path,
                                                                    monkeypatch, capsys):
    _ledger(proj, _journey()[:2])                             # in solo_execute
    phases.set_sprint_state({"phase": "solo_execute", "feature": "feat"})
    t = tmp_path / "cc" / "s1.jsonl"
    _write_jsonl(t, [_row("a", "2026-10-26T10:15:00Z")])
    _register("s1", t)
    line = gate._phase_usage_line("feat", "solo_execute", {"budget_tokens": 5000})
    assert "solo_execute" in line and "1k" in line and "budget 5k" in line
    monkeypatch.setattr(usage, "_measure", lambda *a, **k: 1 / 0)
    assert "UNMEASURED: usage read failed: ZeroDivisionError" in gate._phase_usage_line(
        "feat", "solo_execute", {})


# ---------- watchdog: budgets enforced, loud when unmeasurable ----------

def _budget(root, budget):
    cfg = yaml.safe_load(_TEMPLATE_CFG.read_text())
    for p in cfg["phases"]:
        if p["name"] == "reviewing":
            p["budget_tokens"] = budget
    (root / ".claude" / "sprint-config.yaml").write_text(yaml.safe_dump(cfg))


def _incidents(root, kind):
    d = root / ".sprint" / "incidents"
    return [json.loads(f.read_text()) for f in d.glob(f"*-{kind}.json")] if d.is_dir() else []


def test_watchdog_files_budget_exceeded_from_measured_usage(proj, tmp_path):
    _budget(proj, _UNIT)
    _ledger(proj, _journey())
    phases.set_sprint_state({"phase": "reviewing", "feature": "feat"})
    t = tmp_path / "cc" / "s1.jsonl"
    _write_jsonl(t, [_row("b", "2026-10-26T10:25:00Z"), _row("d", "2026-10-26T10:45:00Z")])
    _register("s1", t)
    assert watchdog.check(proj) == 1
    (inc,) = _incidents(proj, "budget_exceeded")
    assert inc["tokens_used"] == 2 * _UNIT and inc["budget"] == _UNIT
    assert inc["by_session"] == {"s1": 2 * _UNIT}
    assert inc["by_model"] == {"claude-opus-5-5": 2 * _UNIT} and inc["by_role"] == {
        "main": 2 * _UNIT}


def test_watchdog_files_a_budget_incident_once_per_phase(proj, tmp_path):
    """Live budgets on real numbers would otherwise re-file every 15-minute poll."""
    _budget(proj, _UNIT)
    _ledger(proj, _journey())
    phases.set_sprint_state({"phase": "reviewing", "feature": "feat"})
    t = tmp_path / "cc" / "s1.jsonl"
    _write_jsonl(t, [_row("b", "2026-10-26T10:25:00Z"), _row("d", "2026-10-26T10:45:00Z")])
    _register("s1", t)
    watchdog.check(proj)
    watchdog.check(proj)
    assert len(_incidents(proj, "budget_exceeded")) == 1
    for f in (proj / ".sprint" / "incidents").glob("*-budget_exceeded.json"):
        f.unlink()                                    # operator resolves it
    watchdog.check(proj)
    assert len(_incidents(proj, "budget_exceeded")) == 1


def test_watchdog_files_budget_unmeasurable(proj):
    _budget(proj, 10**9)
    _ledger(proj, _journey())
    phases.set_sprint_state({"phase": "reviewing", "feature": "feat"})
    assert watchdog.check(proj) == 1
    (inc,) = _incidents(proj, "budget_unmeasurable")
    assert "no registered session was active since" in inc["reason"]


def test_watchdog_refuses_a_foreign_root_for_budgets(proj, tmp_path, monkeypatch):
    other = tmp_path / "other"
    (other / ".claude").mkdir(parents=True)
    (other / ".sprint").mkdir()
    _budget(other, 10**9)
    phases.set_sprint_state({"phase": "reviewing", "feature": "feat"}, root=other)
    assert watchdog.check(other) == 1
    (inc,) = _incidents(other, "budget_unmeasurable")
    assert "fb-5b83acbaab4a" in inc["reason"]


def test_watchdog_accepts_its_own_root_spelled_differently(proj):
    _budget(proj, 10**9)
    _ledger(proj, _journey())
    phases.set_sprint_state({"phase": "reviewing", "feature": "feat"})
    spelled = Path(str(proj) + "/.claude/..")              # same dir, different spelling
    watchdog.check(spelled)
    assert all("fb-5b83acbaab4a" not in i["reason"] for i in _incidents(proj, "budget_unmeasurable"))


def test_watchdog_without_a_budget_measures_nothing(proj, monkeypatch):
    _ledger(proj, _journey())
    phases.set_sprint_state({"phase": "reviewing", "feature": "feat"})
    monkeypatch.setattr(usage, "measure", lambda *a, **k: pytest.fail("no budget, no measure"))
    watchdog.check(proj)
    assert not _incidents(proj, "budget_unmeasurable")


# ---------- shipped defaults retired; refresh migrates the fleet ----------

def test_shipped_configs_carry_no_budget():
    for cfg in (_TEMPLATE_CFG, _PRESET):
        assert all("budget_tokens" not in p for p in yaml.safe_load(cfg.read_text())["phases"])


def test_retired_table_is_exactly_the_previously_shipped_defaults():
    shipped = {("planning", 150000), ("solo_execute", 400000), ("building", 1000000),
               ("reviewing", 300000), ("solo_execute", 1000000)}   # template + preset
    assert shipped == {(ph, v) for ph, vs in usage.RETIRED_BUDGET_DEFAULTS.items() for v in vs}


def _only_budget_lines_removed(before: str, after: str) -> list[str]:
    diff = [d for d in difflib.ndiff(before.splitlines(), after.splitlines())
            if d[:1] in "+-?"]
    assert not [d for d in diff if d[:1] in "+?"], "retirement may only delete lines"
    removed = [d[2:] for d in diff]
    assert all(re.match(r"^\s*budget_tokens:", r) for r in removed), removed
    return removed


def test_retire_is_surgical_on_the_shipped_layout():
    from prusik import refresh_merge
    out, retired, unretirable = refresh_merge.retire_budget_defaults(_SHIPPED_PHASES)
    assert unretirable == []
    assert len(_only_budget_lines_removed(_SHIPPED_PHASES, out)) == 4
    assert sorted(retired) == ["building.budget_tokens=1000000", "planning.budget_tokens=150000",
                               "reviewing.budget_tokens=300000",
                               "solo_execute.budget_tokens=400000"]
    before = {p["name"]: p for p in yaml.safe_load(_SHIPPED_PHASES)["phases"]}
    for p in before.values():
        p.pop("budget_tokens", None)
    assert {p["name"]: p for p in yaml.safe_load(out)["phases"]} == before


def test_retire_keeps_user_values_and_is_a_noop_without_a_match():
    from prusik import refresh_merge
    text = ("# mine\nphases:\n"
            "  - name: planning\n    budget_tokens: 150000\n\n"
            "  - name: reviewing\n    budget_tokens: 999999   # user tuned\n\n"
            "  - name: building\n    budget_tokens: 150000   # planning's default, wrong phase\n")
    out, retired, _ = refresh_merge.retire_budget_defaults(text)
    assert retired == ["planning.budget_tokens=150000"]
    assert _only_budget_lines_removed(text, out) == ["    budget_tokens: 150000"]
    assert "user tuned" in out and "wrong phase" in out
    assert refresh_merge.retire_budget_defaults(out) == (out, [], [])


@pytest.mark.parametrize("text", [
    "phases:\n  - {name: planning, budget_tokens: 150000}\n",                      # flow style
    "phases:\n  - budget_tokens: 150000\n    name: planning\n",                    # first key
    "base: &b\n  budget_tokens: 150000\nphases:\n  - name: planning\n    <<: *b\n",  # merge key
])
def test_retire_names_layouts_it_cannot_edit_safely_and_leaves_them(text):
    from prusik import refresh_merge
    out, retired, unretirable = refresh_merge.retire_budget_defaults(text)
    assert out == text and retired == []
    assert unretirable == ["planning.budget_tokens=150000"]


def test_retire_is_surgical_on_the_preset_layout_as_shipped():
    from prusik import refresh_merge
    preset = _PRESET.read_text()
    phases_at = preset.index("phases:")
    shipped = preset[:phases_at] + _SHIPPED_PHASES.replace(
        "    budget_tokens: 400000", "    budget_tokens: 1000000")
    out, retired, _ = refresh_merge.retire_budget_defaults(shipped)
    assert "solo_execute.budget_tokens=1000000" in retired
    _only_budget_lines_removed(shipped, out)


def test_refresh_writes_and_reports_a_retirement_only_delta(tmp_path, monkeypatch, capsys):
    """Populated state: a user-modified adopter config whose only change from the template is
    a user comment plus the retired budgets is written, re-hashed and the removal named, not
    reported unchanged."""
    from prusik import init as kit_init, refresh as kit_refresh
    root = tmp_path / "adopter"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    assert kit_init.run() == 0
    cfg = root / ".claude" / "sprint-config.yaml"
    text = cfg.read_text().replace("  - name: planning\n",
                                   "  - name: planning\n    budget_tokens: 150000\n", 1)
    cfg.write_text("# my project\n" + text)
    capsys.readouterr()
    assert kit_refresh.run() == 0
    out = capsys.readouterr().out
    after = cfg.read_text()
    assert not re.search(r"(?m)^\s*budget_tokens:", after) and after.startswith("# my project\n")
    assert "planning.budget_tokens=150000" in out and "retired" in out
    assert kit_refresh.run() == 0                       # re-hashed: the next run is a no-op
    assert "planning.budget_tokens" not in capsys.readouterr().out


def test_refresh_names_an_unretirable_default_even_when_nothing_else_changes(
        tmp_path, monkeypatch, capsys):
    from prusik import init as kit_init, refresh as kit_refresh
    root = tmp_path / "adopter"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    assert kit_init.run() == 0
    cfg = root / ".claude" / "sprint-config.yaml"
    cfg.write_text(cfg.read_text().replace(
        "  - name: planning\n", "  - budget_tokens: 150000\n    name: planning\n", 1))
    assert kit_refresh.run() == 0
    out = capsys.readouterr().out
    assert "! planning.budget_tokens=150000" in out and "delete it by hand" in out


# ---------- effort shows tokens from the completion snapshots ----------

def test_effort_reports_tokens_per_feature_and_phase(proj, capsys):
    ev = _journey() + [{"ts": "2026-10-26T11:00:00+00:00", "event": "sprint_complete",
                        "feature": "feat",
                        "actual": {"tokens": 3_000_000,
                                   "usage": {"by_phase": {"solo_execute": 2_000_000,
                                                          "reviewing": 1_000_000}}}}]
    _ledger(proj, ev)
    records = ledger.read_all()
    assert effort.summarize_features(records)["feat"]["tokens"] == 3_000_000
    assert effort.summarize_phase_tokens(records) == {"solo_execute": 2_000_000,
                                                      "reviewing": 1_000_000}
    effort.run()
    out = capsys.readouterr().out
    assert "3.0M" in out and "2.0M" in out
