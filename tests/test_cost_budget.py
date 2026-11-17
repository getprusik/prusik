"""Budgets cap measured cost in USD, priced per model from one table (fb-1d6f8de5b8d3).

Raw tokens counted every category as 1, so a sprint that re-read cached context (cache reads
cost 0.05x input) tripped a cap that its dollar cost never came near, and an Opus token and a
Haiku token counted the same. Every cap now compares `cost_usd` priced per usage row from
prusik/prices.json; tokens stay as telemetry.

moat-finding: fb-1d6f8de5b8d3
"""

from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest
import yaml

from prusik import (
    gate, ledger, phases, platform_adapter as pa, pricing, refresh_merge, usage, watchdog,
)
from tests import test_measured_token_usage as mtu
from tests.test_sprint_drive import (  # noqa: F401 — fixtures: `rig` needs `proj`
    F, _measuring, _usage, proj, rig, set_config, set_phase,
)

_REPO = Path(__file__).resolve().parent.parent


def _cost(model, **kw):
    base = dict(input=0, output=0, cache_write=0, cache_write_5m=0, cache_write_1h=0,
                cache_read=0)
    return sum(pricing.price_row(model, **{**base, **kw}).values())


@pytest.fixture
def cproj(tmp_path, monkeypatch):
    return _make_proj(tmp_path, monkeypatch)


def _make_proj(tmp_path, monkeypatch):
    root = tmp_path / "cproj"
    (root / ".claude").mkdir(parents=True)
    (root / ".sprint").mkdir()
    import shutil
    shutil.copy(mtu._TEMPLATE_CFG, root / ".claude" / "sprint-config.yaml")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    return root


def _measure_rows(cproj, tmp_path, rows):
    mtu._ledger(cproj, mtu._journey())
    t = tmp_path / "cc" / "s1.jsonl"
    mtu._write_jsonl(t, rows)
    mtu._register("s1", t)
    return usage.measure("feat", now=mtu._NOW)


# ---- the price table ---------------------------------------------------------------------

def test_one_price_table_with_source_and_every_transcript_model():
    t = pricing.table()
    assert t["source_url"].startswith("https://") and t["as_of"]
    for model in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5",
                  "claude-fable-5-2", "claude-opus-4-8"):         # this repo's transcripts
        assert all(isinstance(t["models"][model][c], (int, float))
                   for c in pricing.PRICE_CATEGORIES), model
    opus = t["models"]["claude-opus-5-5"]
    assert (opus["input"], opus["output"], opus["cache_read"]) == (4, 20, 0.2)
    assert opus["cache_write_5m"] == 5 and opus["cache_write_1h"] == 8     # 1.25x and 2x


def test_prices_json_is_in_the_built_wheel(tmp_path):
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(tmp_path)], cwd=_REPO,
                   check=True, capture_output=True)
    (wheel,) = tmp_path.glob("prusik-*.whl")
    with zipfile.ZipFile(wheel) as z:
        assert "prusik/prices.json" in z.namelist()
        assert json.loads(z.read("prusik/prices.json"))["models"]["claude-opus-5-5"]


# ---- cost is measured, per row, per model -------------------------------------------------

def test_the_same_rows_cost_differently_under_haiku_and_opus(cproj, tmp_path):
    opus = _measure_rows(cproj, tmp_path, [mtu._row("a", "2026-10-26T10:15:00Z")])
    haiku = _measure_rows(cproj, tmp_path / "h", [mtu._row("a", "2026-10-26T10:15:00Z",
                                                          model="claude-haiku-5-5")])
    assert opus.measured and haiku.measured and opus.tokens == haiku.tokens
    assert opus.cost_usd == pytest.approx(mtu._ROW_COST)
    assert haiku.cost_usd == pytest.approx((1 * 0.1 + 10 * 0.5 + 100 * 0.2 + 1000 * 0.01) / 1e6)
    assert opus.cost_usd > 10 * haiku.cost_usd
    assert opus.cost_by_model == {"claude-opus-5-5": pytest.approx(opus.cost_usd)}
    assert opus.cost_by_category["cache_read"] == pytest.approx(1000 * 0.2 / 1e6)


def test_a_mixed_sprint_prices_each_row_by_its_own_model(cproj, tmp_path):
    u = _measure_rows(cproj, tmp_path, [
        mtu._row("a", "2026-10-26T10:15:00Z"),
        mtu._row("b", "2026-10-26T10:16:00Z", model="claude-sonnet-5-5")])
    sonnet = (1 * 2 + 10 * 10 + 100 * 4 + 1000 * 0.1) / 1e6
    assert u.cost_usd == pytest.approx(mtu._ROW_COST + sonnet)
    assert set(u.cost_by_model) == {"claude-opus-5-5", "claude-sonnet-5-5"}


def test_a_5m_1h_split_is_priced_per_ttl_and_an_unsplit_write_is_priced_at_1h():
    adapter_row = {"type": "assistant", "timestamp": "2026-10-26T10:15:00Z", "message": {
        "id": "m", "model": "claude-opus-5-5", "usage": {
            "input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 1000,
            "cache_read_input_tokens": 0,
            "cache_creation": {"ephemeral_5m_input_tokens": 600,
                               "ephemeral_1h_input_tokens": 400}}}}
    rec = pa.ClaudeCodeAdapter._usage_row(adapter_row, "main")
    assert (rec.cache_write, rec.cache_write_5m, rec.cache_write_1h) == (1000, 600, 400)
    split = _cost("claude-opus-5-5", cache_write=1000, cache_write_5m=600, cache_write_1h=400)
    assert split == pytest.approx((600 * 5 + 400 * 8) / 1e6)
    unsplit = _cost("claude-opus-5-5", cache_write=1000)
    assert unsplit == pytest.approx(1000 * 8 / 1e6)            # the conservative upper bound
    assert unsplit > split
    assert pricing.unsplit_writes(1000, 600, 400) == 0 and pricing.unsplit_writes(1000, 0, 0) == 1000


def test_unsplit_cache_writes_are_counted_in_the_measured_usage(cproj, tmp_path):
    u = _measure_rows(cproj, tmp_path, [mtu._row("a", "2026-10-26T10:15:00Z", cw=100)])
    assert u.cache_write_unsplit == 100                         # the default row has no split
    assert u.breakdown()["cache_write_unsplit"] == 100
    assert u.cost_by_category["cache_write_1h"] == pytest.approx(100 * 8 / 1e6)
    assert u.cost_by_category["cache_write_5m"] == 0


def test_a_haiku_prompt_over_100k_is_priced_at_the_long_context_rates():
    short = _cost("claude-haiku-5-5", input=50_000)
    long = _cost("claude-haiku-5-5", input=150_000)
    assert short == pytest.approx(50_000 * 0.1 / 1e6)
    assert long == pytest.approx(150_000 * 0.5 / 1e6)


def test_an_unknown_model_is_unmeasured_naming_it_never_zero_or_a_neighbour(cproj, tmp_path):
    u = _measure_rows(cproj, tmp_path, [mtu._row("a", "2026-10-26T10:15:00Z"),
                                        mtu._row("b", "2026-10-26T10:16:00Z",
                                                 model="claude-mystery-9")])
    assert not u.measured and "claude-mystery-9" in u.reason
    assert u.cost_usd == 0.0 and "unmeasured" in u.reason
    with pytest.raises(pricing.UnpricedModel, match="claude-opus-5-5-preview"):
        pricing.price_row("claude-opus-5-5-preview", input=1, output=0, cache_write=0,
                          cache_write_5m=0, cache_write_1h=0, cache_read=0)


def test_a_missing_model_field_is_unmeasured_not_priced(cproj, tmp_path):
    row = mtu._row("a", "2026-10-26T10:15:00Z")
    del row["message"]["model"]
    u = _measure_rows(cproj, tmp_path, [row])
    assert not u.measured and "'unknown'" in u.reason


# ---- every cap is in USD (drive) ----------------------------------------------------------

def test_raw_tokens_over_the_old_cap_with_cost_under_budget_continues(rig, proj, monkeypatch):
    """pinned-engine: 17M tokens, 96% cache reads, about $9.50 — stopped by an 8M-token cap."""
    set_config(proj, suite="true", budget_usd=19)
    _measuring(monkeypatch, _usage(9.5, tokens=17_100_000, phase={"building": 9.5}))
    assert rig.run() == 0, rig.escalation()
    assert [r.role for r in rig.adapter.requests]


def test_cost_over_the_budget_escalates_naming_the_cost_with_tokens_as_telemetry(rig, monkeypatch):
    u = _usage(21.5, tokens=500, phase={"building": 21.5})
    u.input, u.output, u.cache_write, u.cache_read = 1, 2, 3, 494
    _measuring(monkeypatch, u)
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "budget_exceeded"
    assert "$21.50" in esc["reason"] and "$19.00" in esc["reason"]
    assert "API list price" in esc["reason"] and "drive.budget_usd" in esc["reason"]
    detail = " ".join(esc["evidence"])
    assert "cost by role: main $21.50" in detail and "cache read 494" in detail
    assert rig.adapter.requests == []


def test_an_unknown_model_makes_drive_escalate_not_continue(rig, proj, monkeypatch):
    """Real measurement, no stub: a transcript row of a model with no price."""
    monkeypatch.setattr(usage, "measure", lambda f, phase=None, now=None:
                        usage._measure(f, phase, now))
    start = ledger.read_all()[-1]["ts"]
    t = proj / "cc" / "s.jsonl"
    mtu._write_jsonl(t, [mtu._row("a", start, model="claude-mystery-9")])
    usage.registry_path().write_text(json.dumps({"s": {"transcript_path": str(t),
                                                       "first_seen": start, "last_seen": start}}))
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "budget_unmeasurable" and "claude-mystery-9" in esc["reason"]
    assert rig.adapter.requests == []


def test_the_session_cap_compares_the_sessions_cost(rig, proj, monkeypatch):
    set_config(proj, suite="true", budget_usd=100, session_budget_usd=2)
    _measuring(monkeypatch, _usage(5, session={"holder-1": 3.0, "b": 2.0}))
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "budget_exceeded" and "this session cost $3.00" in esc["reason"]
    assert "drive.session_budget_usd" in esc["reason"] and "fresh session" in esc["reason"]
    rig.adapter.current_session_id = lambda: "fresh-2"          # its own cost is $0: proceeds
    assert rig.run() == 0, rig.escalation()


def test_the_phase_cap_compares_the_phases_cost(rig, proj, monkeypatch):
    cfg = yaml.safe_load((proj / ".claude/sprint-config.yaml").read_text())
    for ph in cfg["phases"]:
        if ph["name"] == "building":
            ph["budget_usd"] = 2
    (proj / ".claude/sprint-config.yaml").write_text(yaml.safe_dump(cfg))
    _measuring(monkeypatch, _usage(6, phase={"planning": 4.5, "building": 1.5}))   # building ok
    assert rig.run() == 0, rig.escalation()
    set_phase(proj, "building")                                  # the first run moved on
    _measuring(monkeypatch, _usage(6, phase={"planning": 3.5, "building": 2.5}))
    assert rig.run() == 3
    esc = rig.escalation()
    assert "phase building cost $2.50" in esc["reason"] and "phases[building].budget_usd" in esc["reason"]


# ---- no silent unit change ----------------------------------------------------------------

def _config_with(proj, drive=None, phase=None):
    cfg = yaml.safe_load((proj / ".claude/sprint-config.yaml").read_text())
    cfg["drive"] = {**cfg.get("drive", {}), **(drive or {})}
    for ph in cfg["phases"]:
        if phase and ph["name"] == "building":
            ph.update(phase)
    (proj / ".claude/sprint-config.yaml").write_text(yaml.safe_dump(cfg))


@pytest.mark.parametrize("drive_keys,phase_keys,named,replacement", [
    ({"budget_tokens": 8_000_000}, None, "drive.budget_tokens", "drive.budget_usd"),
    ({"session_budget_tokens": 2_000_000}, None, "drive.session_budget_tokens",
     "drive.session_budget_usd"),
    (None, {"budget_tokens": 1_000_000}, "phases[building].budget_tokens",
     "phases[building].budget_usd"),
])
def test_drive_refuses_a_leftover_budget_tokens_key_with_exit_2(
        rig, proj, drive_keys, phase_keys, named, replacement):
    _config_with(proj, drive_keys, phase_keys)
    assert rig.run() == 2
    assert named in rig.out and replacement in rig.out
    assert ".claude/sprint-config.yaml" in rig.out and "no single price" in rig.out
    assert rig.adapter.requests == []


def test_every_reader_of_budgets_refuses_budget_tokens(proj, monkeypatch, capsys):
    _config_with(proj, {"budget_tokens": 8_000_000})
    phases.set_sprint_state({"feature": F, "phase": "building"}, proj)
    assert watchdog.check(proj) == 2
    assert "drive.budget_usd" in capsys.readouterr().out
    assert phases.print_status() == 2
    assert "drive.budget_usd" in capsys.readouterr().err
    import argparse
    assert gate.advance(argparse.Namespace(phase="reviewing", feature=F)) == 2
    assert "drive.budget_usd" in capsys.readouterr().err


def test_a_clean_config_has_no_legacy_keys_and_the_repos_own_config_is_converted():
    tmpl = yaml.safe_load((_REPO / "prusik/templates/.claude/sprint-config.yaml").read_text())
    own = yaml.safe_load((_REPO / ".claude/sprint-config.yaml").read_text())
    assert pricing.legacy_budget_keys(tmpl) == [] and pricing.legacy_budget_keys(own) == []
    assert isinstance(own["drive"]["budget_usd"], (int, float)) and own["drive"]["budget_usd"] > 0
    shipped = {p["name"]: p.get("budget_usd") for p in tmpl["phases"]}
    assert all(isinstance(shipped[n], int) and shipped[n] > 0
               for n in ("planning", "solo_execute", "building", "reviewing"))


# ---- refresh -------------------------------------------------------------------------------

def test_refresh_retires_a_template_default_budget_tokens_and_reports_a_custom_one(
        tmp_path, monkeypatch, capsys):
    from prusik import init as kit_init, refresh as kit_refresh
    root = tmp_path / "adopter"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    assert kit_init.run() == 0
    cfg = root / ".claude" / "sprint-config.yaml"
    text = cfg.read_text()
    text = text.replace("  - name: planning\n", "  - name: planning\n    budget_tokens: 150000\n", 1)
    text = text.replace("  - name: reviewing\n",
                        "  - name: reviewing\n    budget_tokens: 999999   # tuned\n", 1)
    cfg.write_text(text)
    capsys.readouterr()
    assert kit_refresh.run() == 0                       # the retirement convention: rc 0
    out = capsys.readouterr().out
    after = yaml.safe_load(cfg.read_text())
    by_name = {p["name"]: p for p in after["phases"]}
    assert "budget_tokens" not in by_name["planning"]                   # template default: gone
    assert by_name["reviewing"]["budget_tokens"] == 999999              # custom: preserved
    assert "-planning.budget_tokens=150000 (retired default; budgets are now USD)" in out
    assert "! reviewing.budget_tokens=999999 is a token cap you set" in out
    assert "phase reviewing's budget_usd" in out and "nothing is converted" in out
    assert pricing.legacy_budget_keys(after)            # still refused until the operator converts


def test_leftover_budget_tokens_finds_drive_and_phase_keys():
    text = ("drive:\n  budget_tokens: 5\n  session_budget_tokens: 6\n"
            "phases:\n  - name: a\n    budget_tokens: 7\n  - name: b\n")
    assert refresh_merge.leftover_budget_tokens(text) == [
        "a.budget_tokens=7", "drive.budget_tokens=5", "drive.session_budget_tokens=6"]


# ---- tokens stay visible, cost is added -----------------------------------------------------

def test_sprint_complete_records_cost_and_keeps_the_token_categories(cproj, tmp_path, monkeypatch,
                                                                     capsys):
    mtu._ledger(cproj, mtu._journey())
    phases.set_sprint_state({"phase": "integrating", "feature": "feat"})
    t = tmp_path / "cc" / "s1.jsonl"
    mtu._write_jsonl(t, [mtu._row("a", "2026-10-26T10:15:00Z")])
    mtu._register("s1", t)
    assert mtu._complete(monkeypatch) == 0
    actual = mtu._last_complete()["actual"]
    assert actual["tokens"] == mtu._UNIT and actual["cost_usd"] == pytest.approx(mtu._ROW_COST, abs=1e-6)
    snap = actual["usage"]
    assert (snap["input"], snap["output"], snap["cache_write"], snap["cache_read"]) == (1, 10, 100, 1000)
    assert snap["cost_by_role"] == {"main": pytest.approx(mtu._ROW_COST, abs=1e-6)}
    assert "cost $0.00 (API list price)" in capsys.readouterr().err


def test_rollup_and_effort_report_tokens_and_cost_and_an_old_snapshot_has_no_cost(cproj, capsys):
    done = lambda f, ts, actual: {"ts": ts, "event": "sprint_complete", "feature": f,  # noqa: E731
                                  "actual": actual}
    new = done("new", "2026-11-01T00:00:00+00:00", {"tokens": 3_000_000, "cost_usd": 4.5, "usage": {
        "by_phase": {"reviewing": 3_000_000}, "cost_by_phase": {"reviewing": 4.5},
        "cost_by_role": {"main": 4.5}, "cost_usd": 4.5}})
    old = done("old", "2026-10-01T00:00:00+00:00", {"tokens": 1_000_000, "usage": {
        "by_phase": {"reviewing": 1_000_000}}})
    rolled = usage.rollup([old, new])
    assert rolled["tokens"] == 4_000_000 and rolled["cost_usd"] == 4.5
    assert rolled["cost_measured"] == 1 and rolled["median_cost_usd"] == 4.5
    per = {e["feature"]: e["cost_usd"] for e in rolled["per_sprint"]}
    assert per == {"old": None, "new": 4.5}                     # unmeasured, never 0
    assert rolled["cost_by_phase"] == {"reviewing": 4.5}
    mtu._ledger(cproj, mtu._journey() + [old, new])
    from prusik import effort
    effort.run()
    out = capsys.readouterr().out
    assert "cost" in out and "$4.50" in out and "3.0M" in out
