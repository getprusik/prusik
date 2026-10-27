"""Token usage rolls up per product, briefing included.

v0.228.0 recorded each sprint's measured usage at sprint-complete, but nothing aggregated it:
`prusik effort` summed only by_phase (its own copy of the summation), the HQ export carried no
usage at all, and the briefing work before `sprint_started` (brief + brief-critic) was never
attributed — measured 15-33% on top of the recorded cost. Now:

  - `usage.rollup(records)` is the ONE pure rollup over `sprint_complete` events, the only
    reader of a snapshot's by_phase; effort reads its by_phase;
  - the export carries an additive `usage` block, anonymized by default (no feature names,
    project-chosen role names summed into `other`);
  - the PreToolUse hook (and `prusik serve`) record a system-observed `briefing_started` on a
    feature's first brief write — only for a write the gate allowed, never for the active
    sprint's own brief, deduped by `.sprint/briefing.json`, cleared at `sprint_started`, and
    every recorder fault leaves a `briefing_record_failed` event;
  - effort's one timeline walk turns it into a non-overlapping `briefing` span that
    `sprint_started` closes, so `measure` attributes the brief-critic to `briefing`.

moat-finding: fb-1e3ecda9d011
"""

from __future__ import annotations

import builtins
import io
import json
import os
import random
import re
import shutil
import stat
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

import prusik
from prusik import (effort, gate, gate_class, ledger, main_writer, phases, report, schema,
                    serve, showcase, usage)
from prusik.platform_adapter import ToolEvent

_PKG = Path(prusik.__file__).resolve().parent          # the package under test, any checkout
_TEMPLATE_CFG = _PKG / "templates" / ".claude" / "sprint-config.yaml"
_AGENTS = _PKG / "templates" / ".claude" / "agents"

A, B = "session-aaaa-1111", "session-bbbb-2222"


# ---------- fixtures ----------

@pytest.fixture
def proj(tmp_path, monkeypatch):
    """A hermetic prusik project: its own .claude config, .sprint ledger and cwd."""
    root = tmp_path / "proj"
    (root / ".claude").mkdir(parents=True)
    (root / ".sprint").mkdir()
    shutil.copy(_TEMPLATE_CFG, root / ".claude" / "sprint-config.yaml")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.chdir(root)
    return root


def _ledger(root: Path, events) -> None:
    with open(root / ".sprint" / "ledger.jsonl", "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _events(name: str) -> list[dict]:
    return [r for r in ledger.read_all() if r.get("event") == name]


def _briefed() -> list[str]:
    return [r.get("feature") for r in _events("briefing_started")]


def _marker(root: Path) -> dict:
    p = root / ".sprint" / "briefing.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _fire(payload: dict) -> tuple[int, str]:
    """Drive the real PreToolUse hook entry with a Claude Code payload on stdin."""
    stdin_backup = sys.stdin
    sys.stdin = io.StringIO(json.dumps(payload))
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = gate.pre_tool()
    finally:
        sys.stdin = stdin_backup
    return rc, buf.getvalue()


def _edit(path: str, session: str | None = A) -> dict:
    p = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
         "tool_input": {"file_path": path}}
    if session:
        p["session_id"] = session
    return p


def _bash(cmd: str, session: str | None = A) -> dict:
    p = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd}}
    if session:
        p["session_id"] = session
    return p


def _denied(out: str) -> bool:
    return '"deny"' in out


def _complete(feature, ts, actual) -> dict:
    rec = {"ts": ts, "event": "sprint_complete", "feature": feature}
    if actual is not _NO_ACTUAL:
        rec["actual"] = actual
    return rec


_NO_ACTUAL = object()


def _measured(tokens, by_phase, by_role, by_model, session="sess-secret-7f3a"):
    return {"tokens": tokens, "duration_min": 30,
            "usage": {"input": 1, "output": 2, "cache_write": 3, "cache_read": 4,
                      "by_phase": by_phase, "by_role": by_role, "by_model": by_model,
                      "by_session": {session: tokens}, "malformed_lines": 0}}


def _mixed_ledger() -> list[dict]:
    """Two measured sprints, one `tokens_unmeasured`, one pre-v0.228.0 — plus noise events."""
    return [
        {"ts": "2026-08-01T09:00:00+00:00", "event": "sprint_started", "feature": "delta"},
        _complete("delta", "2026-08-01T10:00:00+00:00", {"duration_min": 60}),
        _complete("alpha", "2026-09-15T10:00:00+00:00", _measured(
            1000, {"building": 600, "reviewing": 400},
            {"main": 700, "regression-sentinel": 300}, {"claude-opus-5-5": 1000})),
        {"ts": "2026-09-20T10:00:00+00:00", "event": "gate_blocked", "feature": "beta"},
        _complete("beta", "2026-10-02T10:00:00+00:00", _measured(
            3000, {"building": 2000, "briefing": 1000}, {"main": 3000},
            {"claude-opus-5-5": 2500, "claude-sonnet-5": 500})),
        _complete("gamma", "2026-10-05T10:00:00+00:00",
                  {"tokens_unmeasured": "transcript unreadable: /gone.jsonl",
                   "duration_min": 9}),
    ]


def _all_keys(obj) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            out |= _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= _all_keys(v)
    return out


# ---------- the rollup: one pure function over sprint_complete ----------

def test_rollup_counts_reasons_and_exact_sums_and_never_carries_by_session():
    r = usage.rollup(_mixed_ledger())
    assert (r["sprints"], r["measured"], r["unmeasured"]) == (4, 2, 2)
    assert r["unmeasured_reasons"] == {"transcript unreadable: /gone.jsonl": 1,
                                       "pre-measurement": 1}
    assert r["tokens"] == 4000
    assert r["by_phase"] == {"building": 2600, "reviewing": 400, "briefing": 1000}
    assert r["by_role"] == {"main": 3700, "regression-sentinel": 300}
    assert r["by_model"] == {"claude-opus-5-5": 3500, "claude-sonnet-5": 500}
    assert "by_session" not in _all_keys(r)               # session ids are local plumbing
    assert "sess-secret-7f3a" not in json.dumps(r)


@pytest.mark.parametrize("tokens,median", [([], None), ([1000, 5000, 2000], 2000),
                                           ([1000, 3000], 2000), ([1000, 2000], 1500)])
def test_rollup_median_over_measured_sprints(tokens, median):
    recs = [_complete(f"f{i}", f"2026-10-0{i + 1}T10:00:00+00:00",
                      _measured(n, {"building": n}, {"main": n}, {"m": n}))
            for i, n in enumerate(tokens)]
    recs.append(_complete("u", "2026-10-09T10:00:00+00:00", {"tokens_unmeasured": "x"}))
    r = usage.rollup(recs)
    assert r["median_tokens"] == median
    assert (median is None) == (r["median_tokens"] is None)
    assert r["measured"] == len(tokens) and r["sprints"] == len(tokens) + 1


def test_rollup_buckets_by_month_of_the_completion_ts():
    assert usage.rollup(_mixed_ledger())["by_month"] == {
        "2026-08": {"sprints": 1, "measured": 0, "tokens": 0},
        "2026-09": {"sprints": 1, "measured": 1, "tokens": 1000},
        "2026-10": {"sprints": 2, "measured": 1, "tokens": 3000},
    }


def test_rollup_per_sprint_is_in_ts_order_whatever_the_input_order():
    recs = _mixed_ledger()
    random.Random(7).shuffle(recs)
    per = usage.rollup(recs)["per_sprint"]
    assert [p["feature"] for p in per] == ["delta", "alpha", "beta", "gamma"]
    assert [p["ts"] for p in per] == sorted(p["ts"] for p in per)
    alpha = per[1]
    assert alpha["measured"] is True and alpha["reason"] is None and alpha["tokens"] == 1000
    assert alpha["by_phase"] == {"building": 600, "reviewing": 400}
    assert alpha["by_role"] == {"main": 700, "regression-sentinel": 300}
    assert alpha["by_model"] == {"claude-opus-5-5": 1000}
    gamma = per[3]                                    # unmeasured: no numbers invented
    assert gamma["measured"] is False and gamma["reason"] == "transcript unreadable: /gone.jsonl"
    assert gamma["tokens"] is None
    assert (gamma["by_phase"], gamma["by_role"], gamma["by_model"]) == ({}, {}, {})
    assert per[0]["reason"] == "pre-measurement" and per[0]["tokens"] is None


def test_rollup_skips_non_int_snapshot_values_without_raising():
    recs = [_complete("f", "2026-10-01T10:00:00+00:00", _measured(
        100, {"building": 100, "bad": "lots", "flt": 1.5, "nil": None},
        {"main": 100, "odd": [1]}, {"m": 100, "s": "9"}))]
    r = usage.rollup(recs)
    assert r["by_phase"] == {"building": 100}
    assert r["by_role"] == {"main": 100} and r["by_model"] == {"m": 100}
    assert r["per_sprint"][0]["by_phase"] == {"building": 100}


def test_rollup_tolerates_non_dict_attributions_and_odd_actuals():
    recs = [
        _complete("a", "2026-10-01T10:00:00+00:00", _measured(
            50, {"building": 50}, ["main", 50], "claude")),       # by_role/by_model not dicts
        _complete("b", "2026-10-02T10:00:00+00:00", None),        # actual: null
        _complete("c", "2026-10-03T10:00:00+00:00", _NO_ACTUAL),  # no actual at all
        _complete("d", "2026-10-04T10:00:00+00:00", {"tokens": 900}),   # a claim, no snapshot
        _complete("e", "2026-10-05T10:00:00+00:00",                     # bool is not a count
                  {"tokens": True, "usage": {"by_phase": {"x": 1}}}),
    ]
    r = usage.rollup(recs)
    assert r["measured"] == 1 and r["tokens"] == 50
    assert r["by_phase"] == {"building": 50} and r["by_role"] == {} and r["by_model"] == {}
    assert r["unmeasured_reasons"] == {"pre-measurement": 4}
    assert r["per_sprint"][3]["tokens"] is None              # the --tokens claim is not usage


def test_rollup_is_pure_and_reads_only_sprint_complete(monkeypatch):
    recs = _mixed_ledger() + [
        {"ts": "2026-10-06T10:00:00+00:00", "event": "phase_advance", "feature": "z",
         "actual": _measured(77, {"building": 77}, {"main": 77}, {"m": 77})}]
    before = json.dumps(recs, sort_keys=True)

    def no_io(*a, **k):
        raise AssertionError("rollup must read no file, transcript or ledger")

    monkeypatch.setattr(builtins, "open", no_io)
    monkeypatch.setattr(ledger, "read_all", no_io)
    monkeypatch.setattr(Path, "read_text", no_io)
    r = usage.rollup(recs)
    assert r["sprints"] == 4 and r["tokens"] == 4000           # the phase_advance is ignored
    assert json.dumps(recs, sort_keys=True) == before          # input not mutated


def test_packaged_roles_is_the_single_source_and_excludes_project_agents(tmp_path):
    stems = frozenset(p.stem for p in _AGENTS.glob("*.md"))
    assert usage.packaged_roles() == stems and "brief-critic" in stems
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "acme-internal-agent.md").write_text(
        "---\nname: acme-internal-agent\n---\nbody\n")
    assert "acme-internal-agent" not in usage.packaged_roles()
    known = usage.known_roles(tmp_path)
    assert stems <= known and "acme-internal-agent" in known


# ---------- effort reads the rollup; one summation only ----------

def test_effort_json_tokens_by_phase_is_the_rollup(proj, capsys):
    recs = _mixed_ledger() + [
        # unmeasured, yet carrying a stray snapshot: the rollup does NOT count it
        _complete("eps", "2026-10-07T10:00:00+00:00",
                  {"tokens_unmeasured": "no window",
                   "usage": {"by_phase": {"building": 999_999}}})]
    _ledger(proj, recs)
    records = ledger.read_all()
    assert effort.run(json_output=True) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["tokens_by_phase"] == usage.rollup(records)["by_phase"]
    assert out["tokens_by_phase"] == {"building": 2600, "reviewing": 400, "briefing": 1000}


def _prusik_sources():
    files = sorted(_PKG.glob("*.py"))
    assert len(files) > 20, f"package sources not found under {_PKG}"
    return files


def test_no_prusik_module_names_summarize_phase_tokens():
    offenders = [p.name for p in _prusik_sources()
                 if re.search(r"\bsummarize_phase_tokens\b", p.read_text(encoding="utf-8"))]
    assert offenders == []


def _key_read(key: str) -> re.Pattern:
    k = re.escape(key)
    return re.compile(rf"""\.get\(\s*["']{k}["']|\[\s*["']{k}["']\s*\](?!\s*=(?!=))""")


_ON_THE_ROLLUP = re.compile(r"\brollup\([^()]*\)\s*$")


def _reads_a_snapshot(text: str) -> bool:
    """(a) a key read of by_phase, or (b) key reads of BOTH actual and usage in one file
    (the one-line chain and the two-step form, whatever the variable is named).

    The one by_phase read that is NOT a snapshot read is a read of the rollup's own result,
    `usage.rollup(records)["by_phase"]` — the consumer form the plan prescribes for effort —
    so a key read applied directly to a `rollup(...)` call is exempt from (a)."""
    for m in _key_read("by_phase").finditer(text):
        prefix = text[text.rfind("\n", 0, m.start()) + 1:m.start()]
        if not _ON_THE_ROLLUP.search(prefix):
            return True
    return bool(_key_read("actual").search(text) and _key_read("usage").search(text))


def test_snapshot_read_matcher_catches_the_two_step_form_and_spares_writers():
    two_step = ("act" "ual_ = r.get('act" "ual') or {}\n"
                "snap = act" "ual_.get(\"us" "age\") or {}\n")
    chained = "x = (r.get(\"act" "ual\") or {}).get(\"us" "age\")\n"
    subscript = "n = rec['by_" "phase']['building']\n"
    assert _reads_a_snapshot(two_step) and _reads_a_snapshot(chained)
    assert _reads_a_snapshot(subscript)
    writer = "act" "ual[\"us" "age\"] = u.breakdown()\nok = r.get(\"act" "ual\")\n"
    literal = "snap = {\"by_" "phase\": phases, \"by_role\": roles}\n"
    compare = "if snap[\"by_" "phase\"] == other: pass\n"
    assert not _reads_a_snapshot(writer)
    assert not _reads_a_snapshot(literal)
    assert _reads_a_snapshot(compare)                 # `==` is a read, not an assignment
    consumer = "phase_tokens = usage.rollup(records)[\"by_" "phase\"]\n"
    consumer_get = "t = usage.rollup(recs).get('by_" "phase')\n"
    assert not _reads_a_snapshot(consumer) and not _reads_a_snapshot(consumer_get)
    sneaky = "t = usage.rollup(recs)\nx = snap[\"by_" "phase\"]\n"
    assert _reads_a_snapshot(sneaky)                  # the exemption is per read, not per file


def test_only_usage_py_reads_a_sprint_complete_snapshot():
    offenders = [p.name for p in _prusik_sources()
                 if p.name != "usage.py" and _reads_a_snapshot(p.read_text(encoding="utf-8"))]
    assert offenders == []
    assert _reads_a_snapshot((_PKG / "usage.py").read_text(encoding="utf-8"))


# ---------- the export carries usage, anonymized by default ----------

def _export_records() -> list[dict]:
    return [
        {"ts": "2026-10-20T09:00:00+00:00", "event": "briefing_started",
         "feature": "secret-feature-x"},
        {"ts": "2026-10-20T10:00:00+00:00", "event": "sprint_started",
         "feature": "secret-feature-x"},
        _complete("secret-feature-x", "2026-10-20T12:00:00+00:00", _measured(
            180, {"briefing": 30, "building": 150},
            {"main": 100, "acme-internal-agent": 50, "general-purpose": 7,
             "brief-critic": 20, "subagent": 3},
            {"claude-opus-5-5": 180})),
    ]


@pytest.fixture
def export_proj(proj):
    agents = proj / ".claude" / "agents"            # the adopter's own agent is a KNOWN role
    agents.mkdir()
    (agents / "acme-internal-agent.md").write_text("---\nname: acme-internal-agent\n---\n")
    _ledger(proj, _export_records())
    return proj


def test_export_usage_leaks_no_feature_or_project_agent_name(export_proj):
    payload = report.export_payload(ledger.read_all(), "prod", export_proj)
    blob = json.dumps(payload["usage"])
    for secret in ("secret-feature-x", "acme-internal-agent", "general-purpose"):
        assert secret not in blob, secret
    u = payload["usage"]
    assert u["by_role"]["other"] == 57                  # 50 + 7, summed, not dropped
    assert u["per_sprint"][0]["by_role"]["other"] == 57
    assert "feature" not in u["per_sprint"][0]
    assert "by_session" not in _all_keys(u)


def test_export_usage_keeps_packaged_roles_main_subagent_and_the_numbers(export_proj):
    records = ledger.read_all()
    u = report.export_payload(records, "prod", export_proj)["usage"]
    raw = usage.rollup(records)
    assert u["by_role"] == {"main": 100, "brief-critic": 20, "subagent": 3, "other": 57}
    assert sum(u["by_role"].values()) == raw["tokens"] == 180
    assert u["by_model"] == raw["by_model"] == {"claude-opus-5-5": 180}
    assert u["by_phase"] == raw["by_phase"] and u["tokens"] == raw["tokens"]
    for k in ("sprints", "measured", "unmeasured", "unmeasured_reasons", "median_tokens",
              "by_month"):
        assert u[k] == raw[k], k


def test_export_full_detail_carries_the_rollup_verbatim(export_proj):
    records = ledger.read_all()
    u = report.export_payload(records, "prod", export_proj, full_detail=True)["usage"]
    assert u == usage.rollup(records)
    assert u["per_sprint"][0]["feature"] == "secret-feature-x"
    assert u["by_role"]["acme-internal-agent"] == 50 and "other" not in u["by_role"]


def test_export_usage_is_additive(export_proj):
    payload = report.export_payload(ledger.read_all(), "prod", export_proj)
    assert payload["schema_version"] == report.EXPORT_SCHEMA_VERSION == "1.0"
    assert {"schema_version", "prusik_version", "product", "product_hash", "as_of", "window",
            "metrics", "feedback", "feedback_store_bytes", "tickets", "timeseries",
            "usage"} <= set(payload)
    assert {"events", "journeys", "completed", "completion_pct", "open_feature_count",
            "total_wall_sec", "convergence_stalls", "loop_fueled", "trust", "phases",
            "feedback_count"} <= set(payload["metrics"])
    assert "open_features" not in payload["metrics"]
    assert payload["usage"]["sprints"] == 1 and payload["usage"]["tokens"] == 180


# ---------- the recorder, through the real hook ----------

def test_first_brief_write_with_no_sprint_records_once_and_marks(proj):
    rc, out = _fire(_edit("briefs/new.md"))
    assert rc == 0 and not _denied(out)
    assert _briefed() == ["new"]
    mark = _marker(proj)
    assert set(mark) == {"new"}
    assert datetime.fromisoformat(mark["new"]).tzinfo is not None
    assert _events("briefing_record_failed") == []


def test_repeat_and_criteria_writes_dedup_to_the_same_slug(proj):
    _fire(_edit("briefs/new.md"))
    _fire(_edit("briefs/new.md"))
    _fire(_edit("briefs/new.criteria.yaml"))
    _fire(_bash("echo x > briefs/new.md"))
    assert _briefed() == ["new"]                             # not "new.criteria"
    assert set(_marker(proj)) == {"new"}
    # the criteria file alone opens a window for a slug it names first
    _fire(_edit("briefs/solo.criteria.yaml"))
    assert _briefed() == ["new", "solo"]


def test_amending_the_active_sprints_brief_records_nothing(proj):
    phases.set_sprint_state({"phase": "building", "feature": "live"})
    for p in (_edit("briefs/live.md"), _edit("briefs/live.criteria.yaml"),
              _bash("echo x >> briefs/live.md")):
        rc, out = _fire(p)
        assert rc == 0 and not _denied(out)
    assert _briefed() == [] and "live" not in _marker(proj)
    _fire(_edit("briefs/next-up.md"))                        # another slug still records
    assert _briefed() == ["next-up"]


def _startable_brief(root: Path, feature: str) -> None:
    from prusik import discovery
    (root / "briefs").mkdir(exist_ok=True)
    (root / "briefs" / f"{feature}.md").write_text(
        "## Goal\nFix the typo in the retry log message so operators read it.\n\n"
        "## Success criteria\nLog reads correctly within 1 line changed.\n\n"
        "## Type\nbug_fix\n")
    rdir = root / "reports" / feature
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "brief-critique.txt").write_text("PASS\n")
    (root / ".sprint" / "dep-graph.json").write_text(json.dumps(
        {"forward": {}, "reverse": {}, "stats": {"by_language": {}}}))
    (root / "design").mkdir(exist_ok=True)
    (root / "design" / "map.md").write_text("map placeholder")
    discovery.fingerprint_map()


def test_sprint_start_clears_only_its_slug_so_a_rebrief_records_again(proj, capsys):
    import argparse
    _fire(_edit("briefs/new.md"))
    _fire(_edit("briefs/bystander.md"))
    assert set(_marker(proj)) == {"new", "bystander"}
    _startable_brief(proj, "new")
    assert gate.sprint_start(argparse.Namespace(feature="new", trivial=False)) == 0
    assert set(_marker(proj)) == {"bystander"}               # the other slug is preserved
    phases.clear_sprint_state()                               # the sprint ends (abandoned)
    _fire(_edit("briefs/new.md"))
    _fire(_edit("briefs/bystander.md"))                       # still deduped
    assert _briefed() == ["new", "bystander", "new"]
    assert set(_marker(proj)) == {"new", "bystander"}


def test_bash_redirect_into_briefs_records_end_to_end(proj):
    rc, out = _fire(_bash("echo x > briefs/new.md"))
    assert rc == 0 and not _denied(out)
    assert _briefed() == ["new"] and set(_marker(proj)) == {"new"}
    assert _events("briefing_record_failed") == []


def test_a_redirect_source_fault_keeps_file_targets_and_is_recorded(proj, monkeypatch):
    def boom(*a, **k):
        raise ValueError("redirect parser exploded")

    monkeypatch.setattr(gate, "_bash_redirect_targets", boom)
    ev = ToolEvent(tool="X", file_targets=("briefs/a.md",), command="echo > briefs/b.md")
    assert gate._record_briefing_for_event(ev, None) is None
    assert _briefed() == ["a"]
    (fail,) = _events("briefing_record_failed")
    assert fail["source"] == "bash_redirect" and fail["error_type"] == "ValueError"
    assert "redirect parser exploded" in fail["error"]


def test_a_file_targets_fault_keeps_the_redirect_source_and_is_recorded(proj):
    ev = ToolEvent(tool="X", file_targets=None, command="echo x > briefs/b.md")
    gate._record_briefing_for_event(ev, None)
    assert _briefed() == ["b"]
    (fail,) = _events("briefing_record_failed")
    assert fail["source"] == "file_targets" and fail["error_type"] == "TypeError"


def test_a_recorder_fault_returns_empty_never_raises_and_is_recorded(proj, monkeypatch):
    real = ledger.project_root
    calls = {"n": 0}

    def flaky_root(*a, **k):                    # the recorder's root lookup fails, once
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("root lookup failed")
        return real(*a, **k)

    monkeypatch.setattr(ledger, "project_root", flaky_root)
    assert gate.record_briefing(["briefs/a.md"], None) == []
    monkeypatch.setattr(ledger, "project_root", real)
    assert _briefed() == [] and _marker(proj) == {}
    (fail,) = _events("briefing_record_failed")
    assert fail["source"] == "recorder" and fail["error_type"] == "RuntimeError"
    assert "root lookup failed" in fail["error"]


def test_only_top_level_brief_files_inside_the_root_match(proj, tmp_path):
    outside = tmp_path / "elsewhere" / "briefs" / "out.md"
    outside.parent.mkdir(parents=True)
    targets = [str(proj / "briefs" / "abs.md"),            # absolute, inside: records
               "briefs/crit.criteria.yaml",                # slug "crit"
               "worktrees/a/briefs/wt.md", "briefs/x/y.md", str(outside),
               "../briefs/up.md", "briefs/notes.txt", "briefs/.hidden.md", "briefs/.md",
               "briefs/.criteria.yaml", "src/briefs.py", "briefs"]
    got = gate.record_briefing(targets, None)
    assert sorted(got) == ["abs", "crit"]
    assert sorted(_briefed()) == ["abs", "crit"]
    assert set(_marker(proj)) == {"abs", "crit"}
    assert _events("briefing_record_failed") == []


def test_a_non_brief_call_never_touches_the_marker(proj, monkeypatch):
    def no_marker():
        raise AssertionError("marker resolved on a non-brief call")

    monkeypatch.setattr(gate, "_briefing_marker_path", no_marker)
    assert gate.record_briefing(["src/app.py", "README.md"], None) == []
    rc, out = _fire(_edit("src/app.py", session=None))
    assert rc == 0
    assert ledger.read_all() == []                 # no event, not even a failure
    assert not (proj / ".sprint" / "briefing.json").exists()


@pytest.mark.parametrize("corrupt", ["{not json", "[1, 2]", '"a string"', ""])
def test_a_corrupt_marker_never_raises_and_reads_as_empty(proj, corrupt):
    (proj / ".sprint" / "briefing.json").write_text(corrupt)
    rc, out = _fire(_edit("briefs/c.md"))
    assert rc == 0 and not _denied(out)
    assert _briefed() == ["c"]
    assert _marker(proj) == {"c": _marker(proj)["c"]}     # rewritten as a valid dict


def test_an_unwritable_marker_never_blocks_and_leaves_a_failure_event(proj):
    (proj / ".sprint" / "briefing.json").mkdir()            # cannot be read or replaced
    rc, out = _fire(_edit("briefs/d.md"))
    assert rc == 0 and not _denied(out)
    assert [f["source"] for f in _events("briefing_record_failed")] == ["recorder"]


def test_an_unwritable_sprint_dir_never_raises_from_the_hook(proj):
    sprint = proj / ".sprint"
    sprint.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        rc, out = _fire(_edit("briefs/e.md", session=None))   # no lease write on this path
        assert rc == 0 and not _denied(out)
    finally:
        sprint.chmod(stat.S_IRWXU)
    assert _briefed() == [] and _marker(proj) == {}


def test_a_lease_denied_brief_write_records_nothing(proj):
    main_writer.check_and_touch(proj, A)                     # A holds the live lease
    rc, out = _fire(_edit("briefs/new.md", session=B))
    assert _denied(out)
    (blocked,) = _events("gate_blocked")
    assert blocked["gate_class"] == gate_class.WRITER_LEASE
    assert _briefed() == [] and "new" not in _marker(proj)
    rc, out = _fire(_bash("echo x > briefs/new.md", session=B))
    assert _denied(out) and _briefed() == []


def _deny_echo_in_building(root: Path) -> None:
    cfg_path = root / ".claude" / "sprint-config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    (spec,) = [p for p in cfg["phases"] if p["name"] == "building"]
    spec.setdefault("deny_commands", []).append("echo")
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False))


def test_a_deny_commands_denied_redirect_records_nothing(proj):
    """The redirect target briefs/ is writable in this phase (kit-internal), so the write
    reaches deny_commands — the gate class on the block proves which gate denied it."""
    _deny_echo_in_building(proj)
    phases.set_sprint_state({"phase": "building", "feature": "other"})
    ok, _ = phases.is_path_writable("briefs/new.md", phases.load_sprint_config(),
                                    "building", "other")
    assert ok                                     # the writable gate cannot deny first
    rc, out = _fire(_bash("printf x > briefs/control.md"))  # control: allowed → records
    assert not _denied(out) and _briefed() == ["control"]
    rc, out = _fire(_bash("echo x > briefs/new.md"))
    assert _denied(out)
    (blocked,) = _events("gate_blocked")
    assert blocked["gate_class"] == gate_class.DENY_COMMAND
    assert blocked["reason"] == "deny command: echo"
    assert _briefed() == ["control"] and "new" not in _marker(proj)


# ---------- prusik serve records through the same recorder ----------

class _FakeHandler(serve._Handler):
    def __init__(self, body: bytes, root: Path):
        self.path = "/submit"
        self.headers = {"content-length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.root = root
        self.brief_schema = schema.load_schema("brief")

    def send_response(self, code, *a):
        pass

    def send_header(self, *a):
        pass

    def end_headers(self):
        pass

    def reply(self) -> dict:
        return json.loads(self.wfile.getvalue().decode())


def _post(root: Path, slug: str) -> dict:
    body = json.dumps({"slug": slug,
                       "goal": "Add email receipts on successful checkout for customers",
                       "success": "Receipt arrives within 10 seconds of payment",
                       "type": "new_feature"}).encode()
    h = _FakeHandler(body, root)
    h.do_POST()
    return h.reply()


def test_a_serve_post_records_one_window_and_later_writes_dedup(proj):
    assert _post(proj, "receipts")["ok"] is True
    assert _briefed() == ["receipts"] and set(_marker(proj)) == {"receipts"}
    _fire(_edit(str(proj / "briefs" / "receipts.md")))
    assert gate.record_briefing([str(proj / "briefs" / "receipts.criteria.yaml")], None) == []
    assert _briefed() == ["receipts"]


def test_a_serve_post_of_the_active_slug_records_nothing(proj):
    phases.set_sprint_state({"phase": "building", "feature": "receipts"})
    assert _post(proj, "receipts")["ok"] is True
    assert _briefed() == [] and _marker(proj) == {}
    assert _post(proj, "next-thing")["ok"] is True           # a different slug records
    assert _briefed() == ["next-thing"]


# ---------- the walk: a non-overlapping briefing span ----------

def _t(hhmm: str) -> str:
    return f"2026-10-26T{hhmm}:00+00:00"


def _briefing_spans(records, feature=None):
    return [s for s in effort.extract_spans(records)
            if s["phase"] == "briefing" and (feature is None or s["feature"] == feature)]


def test_sprint_started_closes_the_latest_briefing_into_a_span():
    recs = [
        {"ts": _t("08:00"), "event": "briefing_started", "feature": "b"},
        {"ts": _t("09:00"), "event": "briefing_started", "feature": "b"},   # latest wins
        {"ts": _t("10:00"), "event": "sprint_started", "feature": "b"},
        {"ts": _t("10:30"), "event": "phase_advance", "feature": "b",
         "from_phase": "scoping", "to_phase": "triage"},
    ]
    assert _briefing_spans(recs) == [{"feature": "b", "phase": "briefing", "start": _t("09:00"),
                                      "end": _t("10:00"), "seconds": 3600.0, "via": "start"}]
    assert effort.phase_windows(recs, "b") == [
        {"phase": "briefing", "start": _t("09:00"), "end": _t("10:00")},
        {"phase": "scoping", "start": _t("10:00"), "end": _t("10:30")},
        {"phase": "triage", "start": _t("10:30"), "end": None}]


def test_a_briefing_is_clipped_to_a_sprint_that_completed_after_it_started():
    recs = [
        {"ts": _t("07:00"), "event": "sprint_started", "feature": "a"},
        {"ts": _t("09:00"), "event": "briefing_started", "feature": "b"},
        {"ts": _t("09:30"), "event": "sprint_complete", "feature": "a"},
        {"ts": _t("10:00"), "event": "sprint_started", "feature": "b"},
    ]
    (span,) = _briefing_spans(recs, "b")
    assert span["start"] == _t("09:30") and span["end"] == _t("10:00")
    assert span["seconds"] == 1800.0
    a_end = max(s["end"] for s in effort.extract_spans(recs) if s["feature"] == "a")
    assert span["start"] >= a_end                          # no token window counted twice


def test_a_sprint_closed_before_the_brief_does_not_move_its_start():
    recs = [
        {"ts": _t("07:00"), "event": "sprint_started", "feature": "a"},
        {"ts": _t("08:30"), "event": "sprint_complete", "feature": "a"},
        {"ts": _t("09:00"), "event": "briefing_started", "feature": "b"},
        {"ts": _t("10:00"), "event": "sprint_started", "feature": "b"},
    ]
    (span,) = _briefing_spans(recs, "b")
    assert span["start"] == _t("09:00") and span["seconds"] == 3600.0


def test_a_clip_down_to_nothing_yields_no_span():
    recs = [
        {"ts": _t("07:00"), "event": "sprint_started", "feature": "a"},
        {"ts": _t("09:00"), "event": "briefing_started", "feature": "b"},
        {"ts": _t("10:00"), "event": "sprint_complete", "feature": "a"},
        {"ts": _t("10:00"), "event": "sprint_started", "feature": "b"},
    ]
    assert _briefing_spans(recs, "b") == []
    assert effort.phase_windows(recs, "b") == [
        {"phase": "scoping", "start": _t("10:00"), "end": None}]


def test_another_features_still_open_span_does_not_clip():
    recs = [
        {"ts": _t("07:00"), "event": "sprint_started", "feature": "a"},     # never closed
        {"ts": _t("09:00"), "event": "briefing_started", "feature": "b"},
        {"ts": _t("10:00"), "event": "sprint_started", "feature": "b"},
    ]
    (span,) = _briefing_spans(recs, "b")
    assert span["start"] == _t("09:00") and span["seconds"] == 3600.0


def test_a_briefing_with_no_sprint_start_or_a_bad_ts_yields_no_span():
    recs = [
        {"ts": _t("09:00"), "event": "briefing_started", "feature": "c"},   # never started
        {"ts": "2026-10-26T0bad", "event": "briefing_started", "feature": "d"},
        {"ts": _t("10:00"), "event": "sprint_started", "feature": "d"},
        {"ts": _t("11:00"), "event": "briefing_record_failed", "source": "recorder",
         "error_type": "OSError", "error": "x"},
    ]
    assert _briefing_spans(recs) == []
    assert effort.phase_windows(recs, "c") == []
    assert "c" not in effort.summarize_features(recs)


def test_a_restart_without_a_new_brief_opens_no_second_briefing_span():
    recs = [
        {"ts": _t("09:00"), "event": "briefing_started", "feature": "d"},
        {"ts": _t("10:00"), "event": "sprint_started", "feature": "d"},
        {"ts": _t("10:20"), "event": "phase_advance", "feature": "d",
         "from_phase": "scoping", "to_phase": "triage"},
        {"ts": _t("11:00"), "event": "sprint_started", "feature": "d"},      # abandoned, again
    ]
    spans = _briefing_spans(recs, "d")
    assert len(spans) == 1 and spans[0]["start"] == _t("09:00") and spans[0]["end"] == _t("10:00")


def test_wall_clock_still_starts_at_sprint_started():
    recs = [
        {"ts": _t("09:00"), "event": "briefing_started", "feature": "e"},
        {"ts": _t("10:00"), "event": "sprint_started", "feature": "e"},
        {"ts": _t("11:00"), "event": "sprint_complete", "feature": "e"},
    ]
    f = effort.summarize_features(recs)["e"]
    assert f["started"] == _t("10:00") and f["wall_clock_sec"] == 3600.0
    assert f["phases"] == {"briefing": 3600.0, "scoping": 3600.0}
    assert effort.summarize_phases(recs)["briefing"]["visits"] == 1


# ---------- measure attributes the brief-critic to briefing ----------

def _row(mid, ts, *, model="claude-opus-5-5"):
    return {"type": "assistant", "timestamp": ts,
            "message": {"id": mid, "model": model,
                        "usage": {"input_tokens": 1, "output_tokens": 10,
                                  "cache_creation_input_tokens": 100,
                                  "cache_read_input_tokens": 1000}}}


_UNIT = 1 + 10 + 100 + 1000


def _jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_measure_puts_the_brief_critic_in_briefing(proj, tmp_path):
    _ledger(proj, [
        {"ts": "2026-10-26T09:30:00+00:00", "event": "briefing_started", "feature": "feat"},
        {"ts": "2026-10-26T10:00:00+00:00", "event": "sprint_started", "feature": "feat"},
        {"ts": "2026-10-26T10:30:00+00:00", "event": "phase_advance", "feature": "feat",
         "from_phase": "scoping", "to_phase": "triage"},
    ])
    main = tmp_path / "cc" / "s1.jsonl"
    _jsonl(main, [_row("pre", "2026-10-26T09:00:00Z"),          # before the brief: not counted
                  _row("brief", "2026-10-26T09:40:00Z"),
                  _row("scope", "2026-10-26T10:10:00Z")])
    sub = tmp_path / "cc" / "s1" / "subagents"
    _jsonl(sub / "agent-c1.jsonl", [_row("critic", "2026-10-26T09:50:00Z")])
    (sub / "agent-c1.meta.json").write_text(json.dumps({"agentType": "brief-critic"}))
    written = datetime(2026, 10, 26, 10, 59, tzinfo=timezone.utc).timestamp()
    for f in (main, sub / "agent-c1.jsonl"):              # deterministic, not wall-clock
        os.utime(f, (written, written))
    usage.registry_path().write_text(json.dumps({"s1": {
        "transcript_path": str(main), "first_seen": "2026-10-26T09:00:00+00:00",
        "last_seen": "2026-10-26T10:59:00+00:00"}}))
    u = usage.measure("feat", now=datetime(2026, 10, 26, 11, 0, tzinfo=timezone.utc))
    assert u.measured, u.reason
    assert u.by_phase == {"briefing": 2 * _UNIT, "scoping": _UNIT}
    assert u.by_role == {"brief-critic": _UNIT, "main": 2 * _UNIT}
    assert u.tokens == 3 * _UNIT


# ---------- showcase shows the briefing phase first ----------

def test_dossier_lists_briefing_first_when_present(proj):
    recs = [
        {"ts": _t("09:00"), "event": "briefing_started", "feature": "x"},
        {"ts": _t("10:00"), "event": "sprint_started", "feature": "x"},
        {"ts": _t("10:30"), "event": "phase_advance", "feature": "x",
         "from_phase": "scoping", "to_phase": "building"},
        {"ts": _t("11:00"), "event": "sprint_complete", "feature": "x"},
    ]
    names = [p for p, _ in showcase.dossier("x", recs)["progress"]["phases"]]
    assert names == ["briefing", "scoping", "building"]
    no_brief = [r for r in recs if r["event"] != "briefing_started"]
    assert [p for p, _ in showcase.dossier("x", no_brief)["progress"]["phases"]] == [
        "scoping", "building"]
