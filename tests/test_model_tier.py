"""Model-tier policy: the engine decides a role's tier class, the adopter's profile names the model.

`prusik/model_tier.py` turns triage signals into a stakes level and a tier class per role; the
adopter's `.claude/model-profile.yaml` maps the three classes to model refs the host accepts at
spawn, and fails closed. `prusik tier resolve` hands the orchestrator one `model=<ref>`; a
`--role` resolve records the prediction as a `tier_resolved` event; `prusik tier report`
compares it with the measured `by_role_model` and with bounces, read-only.

Written from design/model-tier-policy/plan.md (Interfaces + Test plan) before the builder.

moat-capability: fb-4328a6e686ea
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from prusik import __main__ as cli
from prusik import (agents_doctor, effort, gate, ledger, model_tier, phases,
                    platform_adapter as pa, report, schema, usage)

_REPO = Path(__file__).resolve().parent.parent
_TEMPLATES = _REPO / "prusik" / "templates" / ".claude"
_TEMPLATE_CFG = _TEMPLATES / "sprint-config.yaml"
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "model_profiles"
_EM = "—"
_FEATURE = "feat"
_UNSET = object()

_ALL_ROLES = ["backend-builder", "brief-critic", "cartographer", "conventions-enforcer",
              "conventions-scribe", "feature-planner", "frontend-builder", "integrator",
              "plan-critic", "pr-composer", "product-fit-critic", "regression-sentinel",
              "scope-critic", "scoping", "test-writer"]
_FIXED = ["brief-critic", "product-fit-critic", "cartographer", "scoping", "scope-critic",
          "conventions-scribe", "pr-composer"]


# ---------- helpers ----------

@pytest.fixture
def proj(tmp_path, monkeypatch):
    """A project root with the shipped sprint-config (tiering off) and no profile."""
    root = tmp_path / "proj"
    (root / ".claude").mkdir(parents=True)
    (root / ".sprint").mkdir()
    shutil.copy(_TEMPLATE_CFG, root / ".claude" / "sprint-config.yaml")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.chdir(root)
    return root


def _fixture(name: str) -> dict:
    return yaml.safe_load((_FIXTURES / f"{name}.yaml").read_text())


def _install(root: Path, profile) -> None:
    """Install a fixture (by name) or a dict at the one profile location."""
    data = _fixture(profile) if isinstance(profile, str) else profile
    (root / ".claude" / "model-profile.yaml").write_text(yaml.safe_dump(data))


def _set_tiering(root: Path, value) -> None:
    """Set the model_tiering block; `_UNSET` removes it (the shipped default)."""
    cfg_path = root / ".claude" / "sprint-config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    if value is _UNSET:
        cfg.pop("model_tiering", None)
    else:
        cfg["model_tiering"] = value
    cfg_path.write_text(yaml.safe_dump(cfg))


def _decision(root: Path, feature=_FEATURE, *, mode="team", size="M", priority="P1",
              domains=("backend",), risks=("hook path and migration ordering",)) -> None:
    """The shared decision fixture (plan: Interfaces), in triage's real file shape."""
    (root / "decisions").mkdir(exist_ok=True)
    (root / "decisions" / f"{feature}.json").write_text(json.dumps({
        "feature": feature, "mode": mode, "reason": "fixture",
        "scope_summary": {"modules": [], "domains": list(domains), "size": size,
                          "milestone": None, "related_work": [], "risks": list(risks)},
        "brief_meta": {"type": "new_feature", "priority": priority}}))


@pytest.fixture
def ready(proj):
    """Tiering on, reference profile installed, shared decision written. Standard lane."""
    _set_tiering(proj, {"enabled": True})
    _install(proj, "reference")
    _decision(proj)
    return proj


def _ledger(root: Path, events) -> None:
    with open(root / ".sprint" / "ledger.jsonl", "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _lbytes(root: Path) -> bytes:
    p = root / ".sprint" / "ledger.jsonl"
    return p.read_bytes() if p.exists() else b""


def _events(root: Path, name: str) -> list[dict]:
    p = root / ".sprint" / "ledger.jsonl"
    if not p.exists():
        return []
    return [r for r in map(json.loads, p.read_text().splitlines()) if r.get("event") == name]


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file()}


def _main(monkeypatch, capsys, *argv) -> tuple[object, str, str]:
    """Dispatch through `prusik.__main__.main` the way test_hook_hot_path does."""
    monkeypatch.setattr(sys, "argv", ["prusik", *argv])
    try:
        rc = cli.main()
    except SystemExit as e:
        rc = e.code
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def _role_cli(monkeypatch, capsys, role, feature=_FEATURE):
    return _main(monkeypatch, capsys, "tier", "resolve", feature, "--role", role)


def _assert_failed_closed(rc, out, err, feature=_FEATURE):
    assert rc == 2
    assert out == "" and "model=" not in out          # never a model on an error path
    assert err.startswith(f"[prusik-tier] cannot resolve {feature}:")
    assert "\n  - " in err


_PLAN = """# Plan: feat

## Goal recap
Route the models by policy so that spend follows the stakes.

## Modules touched
- prusik/x.py - the thing

## Build order
1. build it

## Interfaces
text

## Test plan
- one
- two
- three

## Risks
- a risk

## Out of scope
nothing

## Proposed roles
- backend-builder -> prusik/x.py
"""


def _plan(root: Path, tiers: list[str] | None = None, feature=_FEATURE) -> Path:
    text = _PLAN
    if tiers is not None:
        text += "\n## Model tiers\n" + "\n".join(tiers) + "\n"
    p = root / "design" / feature / "plan.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _profile(**over) -> "model_tier.Profile":
    kw = dict(tiers={"high_stakes": "opus", "bounded": "sonnet", "mechanical": "haiku"},
              stakes_low=1, stakes_high=4, risk_weights={"migration": 2, "security": 2},
              roles={})
    kw.update(over)
    return model_tier.Profile(**kw)


def _signals(**over) -> "model_tier.Signals":
    kw = dict(lane="standard", size="M", priority="P1", team=True, risk_text="",
              source="decisions/x.json")
    kw.update(over)
    return model_tier.Signals(**kw)


# ---------- policy and parity ----------

def test_base_classes_cover_exactly_the_packaged_roles():
    assert set(model_tier.BASE_CLASS) == usage.packaged_roles()
    assert set(model_tier.BASE_CLASS) == set(_ALL_ROLES)
    assert set(model_tier.BASE_CLASS.values()) <= set(model_tier.TIER_CLASSES)
    assert model_tier.TIER_CLASSES == ("high_stakes", "bounded", "mechanical")


def test_reference_profile_reproduces_template_frontmatter():
    tiers = _fixture("reference")["tiers"]
    checked = 0
    for role in sorted(usage.packaged_roles()):
        text = (_TEMPLATES / "agents" / f"{role}.md").read_text()
        declared = re.search(r"^model:\s*(\S+)\s*$", text, re.M).group(1)
        assert tiers[model_tier.BASE_CLASS[role]]["model"] == declared, role
        checked += 1
    assert checked == 15


@pytest.mark.parametrize("level", ["low", "normal", "high"])
@pytest.mark.parametrize("role", _FIXED)
def test_fixed_roles_never_move(role, level):
    assert model_tier.FIXED_ROLES == frozenset(_FIXED)
    cls, source = model_tier.tier_class(role, level, _profile())
    assert (cls, source) == (model_tier.BASE_CLASS[role], "fixed")
    # a fixed role needs no stakes at all
    assert model_tier.tier_class(role, None, _profile()) == (model_tier.BASE_CLASS[role], "fixed")


@pytest.mark.parametrize("level", ["low", "normal", "high", None])
def test_mechanical_never_moves(level):
    mech = [r for r, c in model_tier.BASE_CLASS.items() if c == "mechanical"]
    assert {"brief-critic", "pr-composer"} <= set(mech)
    for role in mech:
        assert model_tier.tier_class(role, level, _profile()) == ("mechanical", "fixed")


def test_high_stakes_lifts_bounded_and_low_lowers_high_stakes():
    prof = _profile()
    # regression-sentinel: bounded base, not fixed
    assert model_tier.BASE_CLASS["regression-sentinel"] == "bounded"
    assert model_tier.tier_class("regression-sentinel", "high", prof) == ("high_stakes", "stakes")
    assert model_tier.tier_class("regression-sentinel", "normal", prof) == ("bounded", "stakes")
    assert model_tier.tier_class("regression-sentinel", "low", prof) == ("bounded", "stakes")
    # plan-critic: high_stakes base, not fixed. One step only, never to mechanical.
    assert model_tier.BASE_CLASS["plan-critic"] == "high_stakes"
    assert model_tier.tier_class("plan-critic", "low", prof) == ("bounded", "stakes")
    assert model_tier.tier_class("plan-critic", "normal", prof) == ("high_stakes", "stakes")
    assert model_tier.tier_class("plan-critic", "high", prof) == ("high_stakes", "stakes")
    # a stakes-sensitive role with unknown stakes is an error naming the role
    with pytest.raises(model_tier.TierError) as e:
        model_tier.tier_class("regression-sentinel", None, prof)
    assert "regression-sentinel" in " ".join(e.value.problems)


# ---------- stakes ----------

def test_stakes_score_and_reasons_name_every_signal():
    prof = _profile(risk_weights={"migration": 2, "hook": 1})
    sig = _signals(size="L", priority="P1", team=True, risk_text="hook path and migration order")
    s = model_tier.stakes(sig, prof)
    assert (s.level, s.score) == ("high", 7)
    for line in ("size L: +2", "priority P1: +1", "team mode: +1",
                 "risk 'migration': +2", "risk 'hook': +1"):
        assert line in s.reasons, (line, s.reasons)
    assert s.reasons[-1] == "score 7 >= high 4 -> high"
    # points tables are the contract
    assert model_tier.SIZE_POINTS == {"S": 0, "M": 1, "L": 2, "XL": 3}
    assert model_tier.PRIORITY_POINTS == {"P0": 2, "P1": 1}
    p2 = model_tier.stakes(_signals(size="S", priority="P3", team=False), prof)
    assert p2.score == 0                                  # an unlisted priority is 0 points


def test_risk_terms_match_whole_words_case_insensitively_once():
    prof = _profile(stakes_low=-1, stakes_high=99)        # thresholds out of the way
    base = dict(size="S", priority="P2", team=False)
    cap = model_tier.stakes(_signals(risk_text="The Migration is risky", **base), prof)
    assert cap.score == 2 and "risk 'migration': +2" in cap.reasons
    plural = model_tier.stakes(_signals(risk_text="many migrations queued", **base), prof)
    assert plural.score == 0 and not any("risk" in r for r in plural.reasons)
    prefix = model_tier.stakes(_signals(risk_text="premigration step", **base), prof)
    assert prefix.score == 0
    once = model_tier.stakes(
        _signals(risk_text="migration, migration and MIGRATION again", **base), prof)
    assert once.score == 2
    both = model_tier.stakes(
        _signals(risk_text="security review of migration", **base), prof)
    assert both.score == 4


@pytest.mark.parametrize("size,priority,team,score,level", [
    ("S", "P2", False, 0, "low"),
    ("M", "P2", False, 1, "low"),        # == low: inclusive
    ("L", "P2", False, 2, "normal"),
    ("L", "P1", False, 3, "normal"),
    ("L", "P0", False, 4, "high"),       # == high: inclusive
    ("XL", "P0", True, 6, "high"),
])
def test_stakes_thresholds_are_inclusive(size, priority, team, score, level):
    s = model_tier.stakes(_signals(size=size, priority=priority, team=team), _profile())
    assert (s.score, s.level) == (score, level)


# ---------- lanes ----------

def test_standard_lane_reads_the_triage_decision(ready):
    sig = model_tier.load_signals(_FEATURE, "standard")
    assert (sig.lane, sig.size, sig.priority, sig.team) == ("standard", "M", "P1", True)
    assert sig.source == f"decisions/{_FEATURE}.json"
    assert "migration" in sig.risk_text and "backend" in sig.risk_text
    res = model_tier.resolve(_FEATURE, "regression-sentinel")
    assert res.enabled and res.lane == "standard"
    assert (res.stakes.level, res.stakes.score) == ("high", 5)
    # solo mode is not team
    _decision(ready, mode="solo")
    assert model_tier.load_signals(_FEATURE, "standard").team is False
    # an absent decision is "triage not run", not an error here
    (ready / "decisions" / f"{_FEATURE}.json").unlink()
    assert model_tier.load_signals(_FEATURE, "standard") is None


def test_standard_lane_malformed_decision_is_an_error(ready):
    p = ready / "decisions" / f"{_FEATURE}.json"
    p.write_text("{not json")
    with pytest.raises(model_tier.TierError):
        model_tier.load_signals(_FEATURE, "standard")
    _decision(ready, size="HUGE")
    with pytest.raises(model_tier.TierError):
        model_tier.load_signals(_FEATURE, "standard")


def test_trivial_lane_resolves_from_the_brief_without_a_decision(ready):
    (ready / "decisions" / f"{_FEATURE}.json").unlink()
    (ready / "decisions").rmdir()
    (ready / "briefs").mkdir()
    brief = ready / "briefs" / f"{_FEATURE}.md"
    brief.write_text("# Brief\n\n## Type\nbug_fix\n\n## Priority\nP1\n\n"
                     "## Problem\nA security fix.\n")
    _ledger(ready, [{"ts": "2026-10-27T10:00:00+00:00", "event": "sprint_started",
                     "feature": _FEATURE, "lane": "trivial"}])
    sig = model_tier.load_signals(_FEATURE, "trivial")
    assert (sig.lane, sig.size, sig.team, sig.priority) == ("trivial", None, False, "P1")
    assert sig.source == f"briefs/{_FEATURE}.md"
    res = model_tier.resolve(_FEATURE, "regression-sentinel")
    assert res.lane == "trivial"
    assert res.roles["regression-sentinel"].tier_class == "bounded"
    assert res.roles["regression-sentinel"].model == "sonnet"
    assert res.stakes.score == 3                          # P1 1 + security 2; size and team 0
    assert any("trivial lane: size 0" in r for r in res.stakes.reasons)
    assert not (ready / "decisions").exists()             # it never reads or writes decisions/
    brief.unlink()
    with pytest.raises(model_tier.TierError):             # a missing brief is an error
        model_tier.load_signals(_FEATURE, "trivial")


def test_lane_is_the_latest_sprint_started():
    def started(ts, feature, lane=None):
        r = {"ts": ts, "event": "sprint_started", "feature": feature}
        if lane:
            r["lane"] = lane
        return r

    assert model_tier.lane_of([], "f") == "standard"
    assert model_tier.lane_of([started("2026-10-27T10:00:00+00:00", "f")], "f") == "standard"
    one = [started("2026-10-27T10:00:00+00:00", "f", "trivial")]
    assert model_tier.lane_of(one, "f") == "trivial"
    assert model_tier.lane_of(one + [started("2026-10-27T11:00:00+00:00", "f", "standard")],
                              "f") == "standard"
    assert model_tier.lane_of(
        [started("2026-10-27T10:00:00+00:00", "f", "standard"),
         started("2026-10-27T11:00:00+00:00", "f", "trivial")], "f") == "trivial"
    # another feature's lane never leaks
    assert model_tier.lane_of(one + [started("2026-10-27T12:00:00+00:00", "g", "standard")],
                              "f") == "trivial"


def test_standard_lane_stakes_role_before_triage_fails_run_triage_first(
        ready, monkeypatch, capsys):
    (ready / "decisions" / f"{_FEATURE}.json").unlink()
    rc, out, err = _role_cli(monkeypatch, capsys, "regression-sentinel")
    _assert_failed_closed(rc, out, err)
    assert "run triage first" in err


def test_fixed_role_resolves_before_triage(ready, monkeypatch, capsys):
    (ready / "decisions" / f"{_FEATURE}.json").unlink()
    rc, out, err = _role_cli(monkeypatch, capsys, "cartographer")
    assert (rc, out, err) == (0, "model=opus\n", "")
    res = model_tier.resolve(_FEATURE, "cartographer")
    assert res.stakes is None and res.roles["cartographer"].source == "fixed"
    (ev,) = _events(ready, "tier_resolved")
    assert ev["stakes"] is None and ev["score"] is None   # no signals were needed


# ---------- both fixture profiles ----------

def test_two_profiles_route_the_same_decision_differently(proj):
    _set_tiering(proj, {"enabled": True})
    _decision(proj)
    adapter = pa.ClaudeCodeAdapter()
    assert model_tier.validate_profile(_fixture("reference"), adapter) == []
    assert model_tier.validate_profile(_fixture("sonnet-topped"), adapter) == []

    _install(proj, "reference")
    ref = model_tier.resolve(_FEATURE, "regression-sentinel")
    assert (ref.stakes.level, ref.stakes.score) == ("high", 5)
    assert ref.roles["regression-sentinel"].model == "opus"
    assert model_tier.resolve(_FEATURE, "cartographer").roles["cartographer"].model == "opus"

    _install(proj, "sonnet-topped")
    top = model_tier.resolve(_FEATURE, "regression-sentinel")
    assert (top.stakes.level, top.stakes.score) == ("normal", 4)
    assert top.roles["regression-sentinel"].tier_class == "bounded"
    assert top.roles["regression-sentinel"].model == "haiku"
    assert model_tier.resolve(_FEATURE, "cartographer").roles["cartographer"].model == "sonnet"
    assert ref.stakes.level != top.stakes.level
    assert (ref.roles["regression-sentinel"].model
            != top.roles["regression-sentinel"].model)


# ---------- fail closed ----------

def test_missing_profile_fails_closed(proj, monkeypatch, capsys):
    _set_tiering(proj, {"enabled": True})
    _decision(proj)
    rc, out, err = _role_cli(monkeypatch, capsys, "regression-sentinel")
    _assert_failed_closed(rc, out, err)
    assert ".claude/model-profile.yaml missing (model_tiering is enabled)" in err


def test_partial_profile_fails_closed(ready, monkeypatch, capsys):
    data = _fixture("reference")
    del data["tiers"]["mechanical"]
    _install(ready, data)
    for role in ("regression-sentinel", "cartographer"):
        rc, out, err = _role_cli(monkeypatch, capsys, role)
        _assert_failed_closed(rc, out, err)
        assert "mechanical" in err


def _m_stakes(d):
    d["stakes"] = {"low": 4, "high": 4}


def _m_key(d):
    d["bogus_key"] = 1


def _m_version(d):
    d["schema_version"] = 1.0


def _m_weight(d):
    d["risk_weights"]["migration"] = True


def _m_role(d):
    d["roles"] = {"regression-sentinel": "bounded"}


def _m_ref(d):
    d["tiers"]["bounded"] = {"model": "turbo-9"}


_INVALID = {
    "low-not-below-high": (_m_stakes, ["stakes"]),
    "unknown-top-level-key": (_m_key, ["bogus_key"]),
    "float-schema-version": (_m_version, ["schema_version"]),
    "bool-weight": (_m_weight, ["migration"]),
    "packaged-role-in-roles": (_m_role, ["regression-sentinel"]),
    "unknown-ref": (_m_ref, ["not a model ref the host accepts at spawn", "turbo-9"]),
}


@pytest.mark.parametrize("case", [*_INVALID, "all-at-once"])
def test_invalid_profile_names_every_problem(ready, monkeypatch, capsys, case):
    data = _fixture("reference")
    if case == "all-at-once":
        needles = []
        for mutate, ns in _INVALID.values():
            mutate(data)
            needles += ns
    else:
        mutate, needles = _INVALID[case]
        mutate(data)
    _install(ready, data)
    rc, out, err = _role_cli(monkeypatch, capsys, "regression-sentinel")
    _assert_failed_closed(rc, out, err)
    for needle in needles:
        assert needle in err, (needle, err)
    problems = model_tier.validate_profile(data, pa.ClaudeCodeAdapter())
    assert problems and all(isinstance(p, str) for p in problems)
    if case == "all-at-once":
        assert len(problems) >= 6                         # never just the first
        assert err.count("\n  - ") >= 6


@pytest.mark.parametrize("ref", ["claude-opus-4-1-20250805",
                                 "us.anthropic.claude-sonnet-4-5-v1:0"])
def test_pinned_id_in_profile_explains_aliases_only(ready, monkeypatch, capsys, ref):
    data = _fixture("reference")
    data["tiers"]["high_stakes"] = {"model": ref}
    _install(ready, data)
    rc, out, err = _role_cli(monkeypatch, capsys, "cartographer")
    _assert_failed_closed(rc, out, err)
    assert ref in err and "alias" in err.lower()
    assert "not a model ref the host accepts at spawn" not in err   # the pinned note instead


def test_unknown_role_fails_closed_by_name(ready, monkeypatch, capsys):
    rc, out, err = _role_cli(monkeypatch, capsys, "ghost-role")
    _assert_failed_closed(rc, out, err)
    assert "ghost-role" in err
    with pytest.raises(model_tier.TierError) as e:
        model_tier.tier_class("ghost-role", "normal", _profile())
    assert "ghost-role" in " ".join(e.value.problems)


def test_project_role_from_profile_resolves(ready, monkeypatch, capsys):
    data = _fixture("reference")
    data["roles"] = {"docs-writer": "mechanical", "my-reviewer": "bounded"}
    _install(ready, data)
    assert _role_cli(monkeypatch, capsys, "docs-writer") == (0, "model=haiku\n", "")
    # stakes are high, yet a project role never moves
    res = model_tier.resolve(_FEATURE)
    assert res.stakes.level == "high"
    mine = res.roles["my-reviewer"]
    assert (mine.tier_class, mine.model, mine.source) == ("bounded", "sonnet", "project")
    assert set(res.roles) == set(_ALL_ROLES) | {"docs-writer", "my-reviewer"}


@pytest.mark.parametrize("block", ["on", ["enabled"], {"enabled": "yes"}, {"enabled": 1}])
def test_malformed_model_tiering_block_fails_closed(ready, monkeypatch, capsys, block):
    _set_tiering(ready, block)
    rc, out, err = _role_cli(monkeypatch, capsys, "regression-sentinel")
    _assert_failed_closed(rc, out, err)
    assert "model_tiering" in err
    with pytest.raises(model_tier.TierError):
        model_tier.tiering_enabled({"model_tiering": block})


def test_tiering_enabled_reads_only_an_explicit_true():
    assert model_tier.tiering_enabled(None) is False
    assert model_tier.tiering_enabled({}) is False
    assert model_tier.tiering_enabled({"phases": []}) is False
    assert model_tier.tiering_enabled({"model_tiering": {"enabled": False}}) is False
    assert model_tier.tiering_enabled({"model_tiering": {"enabled": True}}) is True


# ---------- off ----------

@pytest.mark.parametrize("block", [None, {"enabled": False}])
def test_tiering_off_prints_model_off_and_records_nothing(proj, monkeypatch, capsys, block):
    _set_tiering(proj, _UNSET if block is None else block)
    (proj / ".claude" / "model-profile.yaml").write_text("::: [not yaml\n  - {")   # never read
    before = _tree(proj)
    rc, out, err = _role_cli(monkeypatch, capsys, "regression-sentinel")
    assert (rc, out, err) == (0, "model=off\n", "")
    assert _events(proj, "tier_resolved") == []
    assert _tree(proj) == before
    res = model_tier.resolve(_FEATURE, "regression-sentinel")
    assert res.enabled is False and res.roles == {} and res.stakes is None


# ---------- overrides ----------

def test_override_applies_with_source_override(ready, monkeypatch, capsys):
    _plan(ready, [f"- plan-critic: bounded {_EM} cheap structural check",
                  f"- regression-sentinel: high_stakes {_EM} touches the hook path"])
    res = model_tier.resolve(_FEATURE)
    pc = res.roles["plan-critic"]
    assert (pc.tier_class, pc.model, pc.source, pc.reason) == (
        "bounded", "sonnet", "override", "cheap structural check")
    assert pc.base_class == "high_stakes"
    assert res.roles["regression-sentinel"].source == "override"
    assert res.roles["regression-sentinel"].reason == "touches the hook path"
    # an unrelated role keeps its policy source
    assert res.roles["integrator"].source == "stakes"
    assert res.roles["integrator"].reason is None
    got = model_tier.parse_overrides(
        f"# P\n\n## Model tiers\n- plan-critic: bounded {_EM} cheap check\n\n## Risks\n- x\n",
        set(_ALL_ROLES))
    assert got == {"plan-critic": ("bounded", "cheap check")}
    # the override is visible in --json and in what a --role resolve records
    rc, out, _ = _main(monkeypatch, capsys, "tier", "resolve", _FEATURE, "--json")
    row = json.loads(out)["roles"]["plan-critic"]
    assert (rc, row["source"], row["reason"], row["tier_class"]) == (
        0, "override", "cheap structural check", "bounded")
    assert _role_cli(monkeypatch, capsys, "plan-critic") == (0, "model=sonnet\n", "")
    (ev,) = _events(ready, "tier_resolved")
    assert ev["roles"]["plan-critic"]["source"] == "override"


_OVERRIDE_ERRORS = {
    "unknown-role": ([f"- ghost-role: bounded {_EM} why"],
                     ["override: unknown role 'ghost-role'"]),
    "unknown-class": ([f"- plan-critic: premium {_EM} why"],
                      ["override plan-critic: unknown class 'premium' "
                       "(one of high_stakes, bounded, mechanical)"]),
    "missing-reason-no-dash": (["- plan-critic: bounded"],
                               ["override plan-critic: missing reason"]),
    "missing-reason-empty": ([f"- plan-critic: bounded {_EM}"],
                             ["override plan-critic: missing reason"]),
    "malformed": (["- just some words"],
                  ["malformed override 'just some words' "
                   "(expected '<role>: <class> — <reason>')"]),
    "duplicate": ([f"- plan-critic: bounded {_EM} one", f"- plan-critic: mechanical {_EM} two"],
                  ["override plan-critic: duplicate"]),
    "several-at-once": ([f"- ghost-role: bounded {_EM} why", "- plan-critic: premium",
                         "- ???"],
                        ["override: unknown role 'ghost-role'", "plan-critic", "malformed override"]),
}


@pytest.mark.parametrize("case", list(_OVERRIDE_ERRORS))
def test_override_errors_are_rc2(ready, monkeypatch, capsys, case):
    lines, needles = _OVERRIDE_ERRORS[case]
    _plan(ready, lines)
    rc, out, err = _role_cli(monkeypatch, capsys, "plan-critic")
    _assert_failed_closed(rc, out, err)
    for needle in needles:
        assert needle in err, (needle, err)
    if case == "several-at-once":
        assert err.count("\n  - ") >= 3                  # every one listed
    with pytest.raises(model_tier.TierError):
        model_tier.parse_overrides("## Model tiers\n" + "\n".join(lines) + "\n",
                                   set(_ALL_ROLES))


def test_override_section_validated_on_single_role_resolve(ready, monkeypatch, capsys):
    """A bad item for plan-critic must fail a resolve for another role, while plan.md is still
    writable (plan-critic's own spawn), not at a building spawn."""
    _plan(ready, [f"- plan-critic: premium {_EM} why"])
    rc, out, err = _role_cli(monkeypatch, capsys, "regression-sentinel")
    _assert_failed_closed(rc, out, err)
    assert "unknown class 'premium'" in err
    assert _events(ready, "tier_resolved") == []


def test_no_plan_means_no_override(ready):
    res = model_tier.resolve(_FEATURE)                    # no design/ at all
    assert all(r.source != "override" for r in res.roles.values())
    _plan(ready)                                          # a plan without the section
    assert all(r.source != "override" for r in model_tier.resolve(_FEATURE).roles.values())
    # the section is read only from design/<f>/plan.md: a bad one elsewhere is ignored
    (ready / "design" / _FEATURE / "scope.md").write_text(
        f"# Scope\n\n## Model tiers\n- ghost-role: premium {_EM} nope\n")
    (ready / "briefs").mkdir()
    (ready / "briefs" / f"{_FEATURE}.md").write_text(
        f"## Model tiers\n- plan-critic: mechanical {_EM} nope\n")
    res = model_tier.resolve(_FEATURE)
    assert res.roles["plan-critic"].source == "stakes"


def test_plan_schema_declares_model_tiers_optional(tmp_path):
    assert schema.load_schema("plan")["optional_fields"]["model_tiers"] == {
        "section": "## Model tiers", "type": "list"}
    without = tmp_path / "a" / "plan.md"
    with_section = tmp_path / "b" / "plan.md"
    for p, txt in ((without, _PLAN),
                   (with_section, _PLAN + f"\n## Model tiers\n- plan-critic: bounded {_EM} r\n")):
        p.parent.mkdir()
        p.write_text(txt)
        ok, errors = schema.validate_plan(p)
        assert ok, errors


# ---------- CLI ----------

def test_cli_resolve_table_has_header_reasons_and_a_row_per_role(ready, monkeypatch, capsys):
    data = _fixture("reference")
    data["roles"] = {"docs-writer": "mechanical"}
    _install(ready, data)
    rc, out, err = _main(monkeypatch, capsys, "tier", "resolve", _FEATURE)
    assert (rc, err) == (0, "")
    lines = out.splitlines()
    assert lines[0] == f"model tiers: {_FEATURE} (lane standard, stakes high, score 5)"
    for reason in ("size M: +1", "priority P1: +1", "team mode: +1", "risk 'migration': +2",
                   "score 5 >= high 4 -> high"):
        assert any(reason in ln for ln in lines), reason
    rows = {}
    for ln in lines[1:]:
        cols = ln.split()
        if len(cols) == 4 and cols[0] in set(_ALL_ROLES) | {"docs-writer"}:
            assert cols[0] not in rows, f"duplicate row for {cols[0]}"
            rows[cols[0]] = cols[1:]
    assert set(rows) == set(_ALL_ROLES) | {"docs-writer"}          # 15 packaged + profile role
    assert rows["regression-sentinel"] == ["high_stakes", "opus", "stakes"]
    assert rows["cartographer"] == ["high_stakes", "opus", "fixed"]
    assert rows["pr-composer"] == ["mechanical", "haiku", "fixed"]
    assert rows["docs-writer"] == ["mechanical", "haiku", "project"]
    assert "model=" not in out


def test_cli_resolve_table_off_line(proj, monkeypatch, capsys):
    rc, out, err = _main(monkeypatch, capsys, "tier", "resolve", _FEATURE)
    assert (rc, err) == (0, "")
    assert out.strip() == ("model tiering: off (set model_tiering.enabled in "
                           ".claude/sprint-config.yaml)")
    assert len(out.strip().splitlines()) == 1             # no role rows


def test_cli_resolve_json_shape(ready, monkeypatch, capsys):
    rc, out, err = _main(monkeypatch, capsys, "tier", "resolve", _FEATURE, "--json")
    assert (rc, err) == (0, "")
    body = json.loads(out)
    assert set(body) == {"feature", "enabled", "lane", "stakes", "roles"}
    assert body["feature"] == _FEATURE and body["enabled"] is True and body["lane"] == "standard"
    assert set(body["stakes"]) == {"level", "score", "reasons"}
    assert (body["stakes"]["level"], body["stakes"]["score"]) == ("high", 5)
    assert isinstance(body["stakes"]["reasons"], list)
    assert set(body["roles"]) == set(_ALL_ROLES)
    for row in body["roles"].values():
        assert set(row) == {"base_class", "tier_class", "model", "source", "reason"}
    assert body["roles"]["regression-sentinel"]["reason"] is None

    _set_tiering(ready, _UNSET)                            # the key absent: off
    rc, out, _ = _main(monkeypatch, capsys, "tier", "resolve", _FEATURE, "--json")
    off = json.loads(out)
    assert rc == 0 and off["enabled"] is False and off["stakes"] is None and off["roles"] == {}
    assert off["feature"] == _FEATURE and "lane" in off


def _report_ledger():
    """One completed sprint: the sentinel was predicted sonnet but ran on opus; the planner
    was predicted bounded and its plan was REJECTED."""
    t = "2026-10-27T1{}:00:00+00:00"
    return [
        {"ts": t.format(0), "event": "sprint_started", "feature": _FEATURE},
        {"ts": t.format(1), "event": "tier_resolved", "feature": _FEATURE, "lane": "standard",
         "stakes": "normal", "score": 3,
         "roles": {"regression-sentinel": {"tier_class": "bounded", "model": "sonnet",
                                           "source": "stakes"}}},
        {"ts": t.format(2), "event": "tier_resolved", "feature": _FEATURE, "lane": "standard",
         "stakes": "normal", "score": 3,
         "roles": {"feature-planner": {"tier_class": "bounded", "model": "sonnet",
                                       "source": "override"}}},
        {"ts": t.format(3), "event": "critic_verdict", "feature": _FEATURE,
         "role": "plan-critic", "artifact": f"design/{_FEATURE}/plan.md", "verdict": "REJECTED"},
        {"ts": t.format(4), "event": "sprint_complete", "feature": _FEATURE,
         "actual": {"tokens": 900, "usage": {"by_role_model": {
             "regression-sentinel": {"claude-opus-5-5": 700},
             "feature-planner": {"claude-sonnet-5-5": 200}}}}},
    ]


def test_cli_report_text_form(ready, monkeypatch, capsys):
    _ledger(ready, _report_ledger())
    rc, out, err = _main(monkeypatch, capsys, "tier", "report")
    assert (rc, err) == (0, "")
    sentinel = [ln for ln in out.splitlines() if "regression-sentinel" in ln]
    planner = [ln for ln in out.splitlines() if "feature-planner" in ln]
    assert len(sentinel) == 1 and "not_applied" in sentinel[0]
    assert len(planner) >= 1 and any("bounced_below_top" in ln for ln in planner)
    assert "summary" in out.lower()
    assert "flags are signals for a human, not verdicts" in out
    assert not out.startswith("model tiering: off")


def test_cli_report_text_says_off_when_tiering_off(proj, monkeypatch, capsys):
    rc, out, err = _main(monkeypatch, capsys, "tier", "report")
    assert rc == 0 and err == ""
    assert out.splitlines()[0] == "model tiering: off"


def test_cli_report_unreadable_ledger_is_rc2_never_an_empty_history(ready, monkeypatch, capsys):
    _ledger(ready, _report_ledger())
    path = ledger.ledger_path()
    path.write_text(path.read_text() + "{not json\n")
    rc, out, err = _main(monkeypatch, capsys, "tier", "report")
    assert rc == 2 and "ledger unreadable" in err
    assert "no completed sprint" not in out and out == ""


def test_cli_report_malformed_tiering_config_is_rc2_never_off(proj, monkeypatch, capsys):
    cfg = proj / ".claude" / "sprint-config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text((cfg.read_text() if cfg.exists() else "") + "\nmodel_tiering: yes\n")
    rc, out, err = _main(monkeypatch, capsys, "tier", "report")
    assert rc == 2 and "model_tiering" in err
    assert "model tiering: off" not in out and out == ""


def test_cli_tier_dispatches_both_subcommands(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(model_tier, "run_resolve",
                        lambda feature, role, json_output: calls.append(
                            ("resolve", feature, role, json_output)) or 7)
    monkeypatch.setattr(model_tier, "run_report",
                        lambda json_output: calls.append(("report", json_output)) or 8)
    monkeypatch.setattr(sys, "argv", ["prusik", "tier", "resolve", "f", "--role", "r"])
    assert cli.main() == 7
    monkeypatch.setattr(sys, "argv", ["prusik", "tier", "resolve", "f", "--json"])
    assert cli.main() == 7
    monkeypatch.setattr(sys, "argv", ["prusik", "tier", "report", "--json"])
    assert cli.main() == 8
    assert calls == [("resolve", "f", "r", False), ("resolve", "f", None, True),
                     ("report", True)]
    monkeypatch.setattr(sys, "argv", ["prusik", "tier", "resolve", "f", "--role", "r", "--json"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 2
    assert len(calls) == 3                                # argparse rejected it before dispatch


# ---------- ledger: only a --role resolve records ----------

def test_role_resolve_records_tier_resolved_with_exact_fields(ready, monkeypatch, capsys):
    assert _role_cli(monkeypatch, capsys, "regression-sentinel") == (0, "model=opus\n", "")
    (ev,) = _events(ready, "tier_resolved")
    assert set(ev) == {"ts", "event", "feature", "lane", "stakes", "score", "roles"}
    assert (ev["feature"], ev["lane"], ev["stakes"], ev["score"]) == (
        _FEATURE, "standard", "high", 5)
    assert ev["roles"] == {"regression-sentinel": {"tier_class": "high_stakes",
                                                   "model": "opus", "source": "stakes"}}


def test_table_and_json_resolves_record_nothing(ready, monkeypatch, capsys):
    _ledger(ready, [{"ts": "2026-10-27T10:00:00+00:00", "event": "sprint_started",
                     "feature": _FEATURE}])
    before = _lbytes(ready)
    assert _main(monkeypatch, capsys, "tier", "resolve", _FEATURE)[0] == 0
    assert _main(monkeypatch, capsys, "tier", "resolve", _FEATURE, "--json")[0] == 0
    assert _lbytes(ready) == before


def test_failed_resolve_records_nothing(ready, monkeypatch, capsys):
    _ledger(ready, [{"ts": "2026-10-27T10:00:00+00:00", "event": "sprint_started",
                     "feature": _FEATURE}])
    before = _lbytes(ready)
    assert _role_cli(monkeypatch, capsys, "ghost-role")[0] == 2           # unknown role
    _plan(ready, [f"- plan-critic: premium {_EM} why"])
    assert _role_cli(monkeypatch, capsys, "plan-critic")[0] == 2          # bad override
    (ready / "design" / _FEATURE / "plan.md").unlink()
    (ready / "decisions" / f"{_FEATURE}.json").unlink()
    assert _role_cli(monkeypatch, capsys, "regression-sentinel")[0] == 2  # before triage
    _decision(ready)
    (ready / ".claude" / "model-profile.yaml").unlink()
    assert _role_cli(monkeypatch, capsys, "cartographer")[0] == 2         # missing profile
    assert _lbytes(ready) == before


def test_tier_resolved_does_not_change_phase_windows():
    t = "2026-10-27T{}:00:00+00:00"
    base = [
        {"ts": t.format("10"), "event": "sprint_started", "feature": "f"},
        {"ts": t.format("10"), "event": "phase_advance", "from_phase": "scoping",
         "to_phase": "planning", "feature": "f"},
        {"ts": t.format("12"), "event": "phase_advance", "from_phase": "planning",
         "to_phase": "building", "feature": "f"},
    ]
    extra = {"ts": t.format("11"), "event": "tier_resolved", "feature": "f",
             "lane": "standard", "stakes": "high", "score": 5,
             "roles": {"plan-critic": {"tier_class": "high_stakes", "model": "opus",
                                       "source": "stakes"}}}
    with_event = sorted(base + [extra], key=lambda r: r["ts"])
    assert effort.phase_windows(with_event, "f") == effort.phase_windows(base, "f")
    assert effort.extract_spans(with_event) == effort.extract_spans(base)


# ---------- anti-generalization guard ----------

# Model and provider names. Adopter identity is NOT listed here: naming adopters in public
# source is itself the leak. scripts/boundary_check.py owns that, repo-wide (prusik/ included),
# against the private HQ registry at pre-commit.
_GUARD = re.compile(
    r"\b(opus|sonnet|haiku|fable|gemini|anthropic|openai|bedrock|vertex)\b"
    r"|\bclaude-[\w.-]+|\bgpt-[\w.-]+", re.I)


def _scan(package_dir: Path, exclude: Path | None = None) -> list[str]:
    hits = []
    for p in sorted(package_dir.rglob("*.py")):
        if exclude is not None and p == exclude:
            continue
        for n, line in enumerate(p.read_text().splitlines(), 1):
            for m in _GUARD.finditer(line):
                hits.append(f"{p}:{n}: {m.group(0)}")
    return hits


def test_no_model_or_provider_names_outside_the_adapter():
    pkg = _REPO / "prusik"
    assert (pkg / "model_tier.py").exists()
    assert _scan(pkg, exclude=pkg / "platform_adapter.py") == []


def test_guard_fires_on_a_planted_sonnet(tmp_path):
    pkg = tmp_path / "prusik"
    pkg.mkdir()
    (pkg / "planted.py").write_text('MODEL = "sonnet"\nPATH = ".claude/x"\nWORD = "diffable"\n')
    hits = _scan(pkg)
    assert len(hits) == 1 and hits[0].endswith(": sonnet") and ":1:" in hits[0]


# ---------- adapter ----------

def test_adapter_contract_includes_model_methods():
    assert {"spawn_model_refs", "pinned_model_note", "model_matches"} <= \
        pa.PlatformAdapter.__abstractmethods__
    assert not pa.ClaudeCodeAdapter.__abstractmethods__


def test_spawn_refs_cover_both_fixture_profiles():
    refs = pa.ClaudeCodeAdapter().spawn_model_refs()
    assert isinstance(refs, frozenset)
    for name in ("reference", "sonnet-topped"):
        for tier in _fixture(name)["tiers"].values():
            assert tier["model"] in refs, (name, tier)


@pytest.mark.parametrize("ref,observed,expected", [
    ("opus", "claude-opus-5-5", True),
    ("sonnet", "claude-3-5-sonnet-20241022", True),
    ("sonnet", "us.anthropic.claude-sonnet-4-5-v1:0", True),
    ("haiku", "claude-haiku-4-5@20251001", True),
    ("Opus", "CLAUDE-OPUS-5-5", True),
    ("opus", "claude-sonnet-5-5", False),
    ("sonnet", "unknown", False),
    ("sonnet", "<synthetic>", False),
    ("opus", "opus-5", False),                      # no "claude" token
    ("sonnet", "claude-sonnets-5", False),          # a token, not a substring
    ("haiku", "", False),
])
def test_model_matches_observed_ids(ref, observed, expected):
    assert pa.ClaudeCodeAdapter().model_matches(ref, observed) is expected


def test_pinned_model_note_alias_is_none_dated_is_explained():
    a = pa.ClaudeCodeAdapter()
    for alias in ("opus", "sonnet", "haiku", "fable", "inherit", "turbo-9"):
        assert a.pinned_model_note(alias) is None, alias
    for pinned in ("claude-opus-4-1-20250805", "CLAUDE-sonnet-4", "us.anthropic.claude-haiku-4-5"):
        note = a.pinned_model_note(pinned)
        assert note and "alias" in note.lower(), pinned


# ---------- doctor ----------

def _agent_text(model: str) -> str:
    return f"---\nname: probe\ndescription: d\ntools: Read\nmodel: {model}\n---\nBody.\n"


@pytest.mark.parametrize("model", ["opus", "sonnet", "haiku", "inherit",
                                   "claude-opus-4-1-20250805"])
def test_doctor_accepts_everything_it_accepted_before(model):
    assert agents_doctor._check_agent(Path("probe.md"), _agent_text(model)) == []


@pytest.mark.parametrize("model", ["gpt4", "turbo"])
def test_doctor_still_flags_an_unknown_model(model):
    problems = agents_doctor._check_agent(Path("probe.md"), _agent_text(model))
    assert len(problems) == 1 and f"model: '{model}' is unusual" in problems[0]


def test_doctor_set_comes_from_the_adapter(monkeypatch):
    """Spawn refs (fable included) and pinned Bedrock ids pass; the set is the adapter's."""
    for model in ("fable", "us.anthropic.claude-sonnet-4-5-v1:0"):
        assert agents_doctor._check_agent(Path("probe.md"), _agent_text(model)) == [], model
    monkeypatch.setattr(pa.ClaudeCodeAdapter, "spawn_model_refs",
                        lambda self: frozenset({"zeta"}))
    assert agents_doctor._check_agent(Path("probe.md"), _agent_text("zeta")) == []
    assert agents_doctor._check_agent(Path("probe.md"), _agent_text("opus")) != []


def test_packaged_agents_pass_doctor(tmp_path, capsys):
    root = tmp_path / "adopter"
    shutil.copytree(_TEMPLATES / "agents", root / ".claude" / "agents")
    assert agents_doctor.doctor(root) == 0
    capsys.readouterr()


# ---------- usage: by_role_model ----------

def _row(mid, ts, model, *, out=10, inp=1, cw=100, cr=1000):
    return {"type": "assistant", "timestamp": ts,
            "message": {"id": mid, "model": model,
                        "usage": {"input_tokens": inp, "output_tokens": out,
                                  "cache_creation_input_tokens": cw,
                                  "cache_read_input_tokens": cr}}}


_UNIT = 1 + 10 + 100 + 1000
_NOW = datetime(2026, 10, 26, 11, 0, tzinfo=timezone.utc)
_OPUS, _SONNET, _HAIKU = "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _journey(feature=_FEATURE):
    return [
        {"ts": "2026-10-26T10:00:00+00:00", "event": "sprint_started", "feature": feature},
        {"ts": "2026-10-26T10:10:00+00:00", "event": "phase_advance", "from_phase": "scoping",
         "to_phase": "solo_execute", "feature": feature},
        {"ts": "2026-10-26T10:20:00+00:00", "event": "phase_advance",
         "from_phase": "solo_execute", "to_phase": "reviewing", "feature": feature},
    ]


_EXPECTED_ROLE_MODELS = {
    "main": {_OPUS: _UNIT},
    "regression-sentinel": {_SONNET: _UNIT, _HAIKU: _UNIT},
    "conventions-enforcer": {_SONNET: _UNIT},
}


def _usage_scene(root: Path, tmp_path: Path) -> None:
    """A main session on opus; the sentinel ran on two models; the enforcer on one."""
    _ledger(root, _journey())
    main = tmp_path / "cc" / "s1.jsonl"
    _write_jsonl(main, [_row("m0", "2026-10-26T10:12:00Z", _OPUS)])
    sub = tmp_path / "cc" / "s1" / "subagents"
    _write_jsonl(sub / "agent-a1.jsonl", [_row("s1", "2026-10-26T10:14:00Z", _SONNET),
                                          _row("s2", "2026-10-26T10:15:00Z", _HAIKU)])
    (sub / "agent-a1.meta.json").write_text(json.dumps({"agentType": "regression-sentinel"}))
    _write_jsonl(sub / "agent-a2.jsonl", [_row("c1", "2026-10-26T10:16:00Z", _SONNET)])
    (sub / "agent-a2.meta.json").write_text(json.dumps({"agentType": "conventions-enforcer"}))
    reg = usage.registry_path()
    reg.write_text(json.dumps({"s1": {"transcript_path": str(main),
                                      "first_seen": "2026-10-26T09:00:00+00:00",
                                      "last_seen": "2026-10-26T10:59:00+00:00"}}))


def test_measure_records_by_role_model_consistent_with_by_role_and_by_model(proj, tmp_path):
    _usage_scene(proj, tmp_path)
    u = usage.measure(_FEATURE, now=_NOW)
    assert u.measured
    assert u.by_role_model == _EXPECTED_ROLE_MODELS
    assert {r: sum(m.values()) for r, m in u.by_role_model.items()} == u.by_role
    by_model: dict[str, int] = {}
    for models in u.by_role_model.values():
        for model, n in models.items():
            by_model[model] = by_model.get(model, 0) + n
    assert by_model == u.by_model


def test_breakdown_carries_by_role_model(proj, tmp_path):
    _usage_scene(proj, tmp_path)
    u = usage.measure(_FEATURE, now=_NOW)
    bd = u.breakdown()
    assert bd["by_role_model"] == _EXPECTED_ROLE_MODELS
    assert {"by_role", "by_model", "by_phase", "by_session"} <= set(bd)   # nothing dropped


def _complete(monkeypatch) -> int:
    monkeypatch.setattr(gate, "_run_success_criteria", lambda *a, **k: (True, []))
    return gate.sprint_complete(argparse.Namespace(feature=_FEATURE, duration_min=None,
                                                   tokens=None, escalated=False))


def test_sprint_complete_snapshot_carries_by_role_model(proj, tmp_path, monkeypatch):
    _usage_scene(proj, tmp_path)
    phases.set_sprint_state({"phase": "integrating", "feature": _FEATURE})
    assert _complete(monkeypatch) == 0
    rec = [r for r in ledger.read_all() if r["event"] == "sprint_complete"][-1]
    assert rec["actual"]["usage"]["by_role_model"] == _EXPECTED_ROLE_MODELS
    assert usage.role_models(rec) == _EXPECTED_ROLE_MODELS


def _has_key(obj, key) -> bool:
    if isinstance(obj, dict):
        return key in obj or any(_has_key(v, key) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_has_key(v, key) for v in obj)
    return False


def test_rollup_and_export_never_carry_by_role_model(proj):
    records = _journey() + [{
        "ts": "2026-10-26T11:00:00+00:00", "event": "sprint_complete", "feature": _FEATURE,
        "actual": {"tokens": 3 * _UNIT, "usage": {
            "by_phase": {"solo_execute": 3 * _UNIT},
            "by_role": {"main": _UNIT, "regression-sentinel": 2 * _UNIT},
            "by_model": {_OPUS: _UNIT, _SONNET: 2 * _UNIT},
            "by_role_model": _EXPECTED_ROLE_MODELS}}}]
    _ledger(proj, records)
    assert usage.role_models(records[-1]) == _EXPECTED_ROLE_MODELS    # the snapshot has it ...
    assert not _has_key(usage.rollup(records), "by_role_model")       # ... the rollup never
    assert usage.rollup(records)["by_role"]["main"] == _UNIT          # (and still rolls up)
    for full in (False, True):
        payload = report.export_payload(records, "prod", proj, full_detail=full)
        assert not _has_key(payload, "by_role_model"), full
        assert "by_role_model" not in json.dumps(payload), full


def test_role_models_absent_is_empty():
    for rec in ({}, {"actual": None}, {"actual": "x"}, {"actual": {}},
                {"actual": {"tokens": None, "tokens_unmeasured": "no session"}},
                {"actual": {"usage": None}}, {"actual": {"usage": []}},
                {"actual": {"usage": {}}}, {"actual": {"usage": {"by_role_model": None}}}):
        assert usage.role_models(rec) == {}, rec
    good = {"actual": {"usage": {"by_role_model": {"r": {"m": 5}}}}}
    assert usage.role_models(good) == {"r": {"m": 5}}


@pytest.mark.parametrize("raw", [[1, 2], "x", {"r": "x"}, {"r": {"m": 5, "bad": "x"}},
                                 {"r": {"m": True}}])
def test_role_models_present_but_malformed_raises_never_reads_as_empty(raw):
    with pytest.raises(ValueError, match="by_role_model"):
        usage.role_models({"actual": {"usage": {"by_role_model": raw}}})


def test_report_lists_every_record_it_could_not_use(ready, monkeypatch, capsys):
    events = _report_ledger()
    events[-1]["actual"]["usage"]["by_role_model"]["feature-planner"] = {"m": "x"}
    events.insert(0, {"ts": "2026-01-01T00:00:00+00:00", "event": "tier_resolved",
                      "feature": None, "roles": {}})
    events.insert(1, {"ts": "2026-01-01T00:00:01+00:00", "event": "tier_resolved",
                      "feature": _FEATURE, "lane": "standard", "roles": {"plan-critic": "opus"}})
    _ledger(ready, events)
    rep = model_tier.calibrate(ledger.read_all(), pa.get_adapter(), True)
    assert len(rep["skipped"]) == 3, rep["skipped"]
    joined = "\n".join(rep["skipped"])
    assert "without a string ts/feature" in joined
    assert "'plan-critic' lacks a string tier_class/model" in joined
    assert "usage snapshot malformed" in joined
    rc, out, err = _main(monkeypatch, capsys, "tier", "report")
    assert rc == 0
    assert "skipped 3 record(s) it could not use:" in out


def test_report_with_nothing_skipped_prints_no_skip_line(ready, monkeypatch, capsys):
    _ledger(ready, _report_ledger())
    rc, out, _ = _main(monkeypatch, capsys, "tier", "report")
    assert rc == 0 and "skipped" not in out


# ---------- calibration ----------

_T = "2026-10-27T{:02d}:00:00+00:00"


def _tr(h, feature, roles, *, lane="standard", stakes="normal", score=3):
    """roles: {role: (tier_class, model)}."""
    return {"ts": _T.format(h), "event": "tier_resolved", "feature": feature, "lane": lane,
            "stakes": stakes, "score": score,
            "roles": {r: {"tier_class": c, "model": m, "source": "stakes"}
                      for r, (c, m) in roles.items()}}


def _done(h, feature, by_role_model=None):
    actual: dict = {"tokens": None, "tokens_unmeasured": "no session"}
    if by_role_model is not None:
        actual = {"tokens": 1, "usage": {"by_role_model": by_role_model}}
    return {"ts": _T.format(h), "event": "sprint_complete", "feature": feature,
            "actual": actual}


def _verdict(h, feature, artifact, verdict, role="plan-critic"):
    return {"ts": _T.format(h), "event": "critic_verdict", "feature": feature, "role": role,
            "artifact": artifact, "verdict": verdict}


def _rewind(h, feature, to_phase):
    return {"ts": _T.format(h), "event": "phase_rewind", "feature": feature,
            "from_phase": "reviewing", "to_phase": to_phase}


def _cal(records) -> dict:
    return model_tier.calibrate(records, pa.ClaudeCodeAdapter())


def test_report_flags_not_applied_when_observed_model_does_not_match():
    out = _cal([_tr(1, "f", {"regression-sentinel": ("bounded", "sonnet")}),
                _done(5, "f", {"regression-sentinel": {_OPUS: 100}})])
    assert {"enabled", "sprints", "summary", "note"} <= set(out)
    assert out["note"] == "flags are signals for a human, not verdicts"
    (sprint,) = out["sprints"]
    assert (sprint["feature"], sprint["ts"], sprint["lane"], sprint["stakes"],
            sprint["score"]) == ("f", _T.format(5), "standard", "normal", 3)
    row = sprint["roles"]["regression-sentinel"]
    assert row["tier_class"] == "bounded" and row["model"] == "sonnet"
    assert row["source"] == "stakes"
    assert row["observed"] == {_OPUS: 100} and row["not_applied"] is True
    # one off-model row among matching ones is enough; a zero-token row is not
    mixed = _cal([_tr(1, "f", {"regression-sentinel": ("bounded", "sonnet")}),
                  _done(5, "f", {"regression-sentinel": {_SONNET: 50, _OPUS: 5}})])
    assert mixed["sprints"][0]["roles"]["regression-sentinel"]["not_applied"] is True
    zero = _cal([_tr(1, "f", {"regression-sentinel": ("bounded", "sonnet")}),
                 _done(5, "f", {"regression-sentinel": {_SONNET: 50, _OPUS: 0}})])
    assert zero["sprints"][0]["roles"]["regression-sentinel"]["not_applied"] is False


def test_report_not_applied_false_when_matched_and_null_when_unobserved():
    pred = {"regression-sentinel": ("bounded", "sonnet"),
            "conventions-enforcer": ("bounded", "sonnet")}
    matched = _cal([_tr(1, "f", pred),
                    _done(5, "f", {"regression-sentinel": {_SONNET: 100}})])
    roles = matched["sprints"][0]["roles"]
    assert roles["regression-sentinel"]["not_applied"] is False
    # the enforcer has no rows in a measured snapshot: unobserved, not "applied"
    assert roles["conventions-enforcer"]["observed"] is None
    assert roles["conventions-enforcer"]["not_applied"] is None
    unmeasured = _cal([_tr(1, "f", pred), _done(5, "f")])
    for role in pred:
        row = unmeasured["sprints"][0]["roles"][role]
        assert row["observed"] is None and row["not_applied"] is None


def test_rejected_plan_bounces_feature_planner_below_top():
    plan, scope = "design/f/plan.md", "design/f/scope.md"
    out = _cal([
        _tr(1, "f", {"feature-planner": ("bounded", "sonnet"), "scoping": ("bounded", "sonnet"),
                     "integrator": ("bounded", "sonnet")}),
        _verdict(2, "f", plan, "REJECTED"),
        _verdict(3, "f", plan, "APPROVED"),                    # approval is not a bounce
        _verdict(3, "f", scope, "REJECTED", role="scope-critic"),
        _verdict(3, "g", plan, "REJECTED"),                    # another feature's
        _done(5, "f")])
    roles = out["sprints"][0]["roles"]
    assert roles["feature-planner"]["bounces"] == 1
    assert roles["feature-planner"]["bounced_below_top"] is True
    assert roles["scoping"]["bounces"] == 1                    # scope.md rejected -> scoping
    assert roles["scoping"]["bounced_below_top"] is True
    assert roles["integrator"]["bounces"] == 0
    assert roles["integrator"]["bounced_below_top"] is False


def test_rewind_into_building_bounces_builders_and_test_writer():
    builders = {r: ("bounded", "sonnet") for r in
                ("backend-builder", "frontend-builder", "test-writer", "regression-sentinel")}
    out = _cal([_tr(1, "f", builders),
                _rewind(2, "f", "building"),
                _rewind(3, "f", "solo_execute"),
                _rewind(4, "f", "planning"),                   # not a build rewind
                _rewind(4, "g", "building"),                   # another feature's
                _done(6, "f")])
    roles = out["sprints"][0]["roles"]
    for r in ("backend-builder", "frontend-builder", "test-writer"):
        assert roles[r]["bounces"] == 2 and roles[r]["bounced_below_top"] is True, r
    assert roles["regression-sentinel"]["bounces"] == 0


def test_reviewer_pass_fail_on_worktrees_is_never_a_bounce():
    reviewers = {r: ("bounded", "sonnet") for r in ("regression-sentinel",
                                                    "conventions-enforcer")}
    out = _cal([_tr(1, "f", reviewers),
                _verdict(2, "f", "worktrees", "PASS", role="regression-sentinel"),
                _verdict(3, "f", "worktrees", "FAIL", role="regression-sentinel"),
                _verdict(3, "f", "worktrees", "PASS", role="conventions-enforcer"),
                _verdict(4, "f", "worktrees", "FAIL", role="conventions-enforcer"),
                _verdict(4, "f", "worktrees", "REJECTED", role="conventions-enforcer"),
                _done(6, "f")])
    for r in reviewers:
        row = out["sprints"][0]["roles"][r]
        assert row["bounces"] == 0 and row["bounced_below_top"] is False, r


def test_high_stakes_bounce_is_not_below_top():
    out = _cal([_tr(1, "f", {"feature-planner": ("high_stakes", "opus")}),
                _verdict(2, "f", "design/f/plan.md", "REJECTED"),
                _done(5, "f")])
    row = out["sprints"][0]["roles"]["feature-planner"]
    assert row["bounces"] == 1 and row["bounced_below_top"] is False


def test_report_window_excludes_a_previous_sprints_predictions():
    out = _cal([
        _tr(1, "f", {"regression-sentinel": ("high_stakes", "opus"),
                     "feature-planner": ("bounded", "sonnet")}),
        _tr(1, "g", {"regression-sentinel": ("mechanical", "haiku")}),   # other feature
        _verdict(2, "f", "design/f/plan.md", "REJECTED"),
        _done(3, "f", {"regression-sentinel": {_OPUS: 10}}),
        _tr(4, "f", {"regression-sentinel": ("bounded", "sonnet")}),
        _tr(5, "f", {"regression-sentinel": ("bounded", "sonnet"),
                     "feature-planner": ("bounded", "sonnet")}),
        _tr(6, "f", {"regression-sentinel": ("high_stakes", "opus")}),   # latest wins
        _done(7, "f", {"regression-sentinel": {_OPUS: 20}}),
        _done(9, "f"),                                  # a sprint with no prediction: omitted
    ])
    first, second = out["sprints"]
    assert (first["ts"], second["ts"]) == (_T.format(3), _T.format(7))
    assert len(out["sprints"]) == 2
    assert first["roles"]["regression-sentinel"]["tier_class"] == "high_stakes"
    assert first["roles"]["feature-planner"]["bounces"] == 1
    assert second["roles"]["regression-sentinel"]["tier_class"] == "high_stakes"
    assert second["roles"]["regression-sentinel"]["model"] == "opus"
    assert second["roles"]["feature-planner"]["bounces"] == 0        # the bounce was sprint 1's
    assert second["roles"]["feature-planner"]["tier_class"] == "bounded"


def test_report_window_includes_briefing_predictions():
    out = _cal([_tr(1, "f", {"product-fit-critic": ("high_stakes", "opus")}),   # briefing
                {"ts": _T.format(2), "event": "sprint_started", "feature": "f"},
                _done(5, "f", {"product-fit-critic": {_OPUS: 9}})])
    row = out["sprints"][0]["roles"]["product-fit-critic"]
    assert row["tier_class"] == "high_stakes" and row["not_applied"] is False


def test_report_summary_per_role_and_class():
    plan = "design/{}/plan.md"
    out = _cal([
        _tr(1, "f", {"feature-planner": ("bounded", "sonnet")}),
        _verdict(2, "f", plan.format("f"), "REJECTED"),
        _done(3, "f", {"feature-planner": {_SONNET: 100}}),
        _tr(4, "g", {"feature-planner": ("bounded", "sonnet")}),
        _done(5, "g", {"feature-planner": {_SONNET: 50}}),
        _tr(6, "f", {"feature-planner": ("high_stakes", "opus")}),
        _done(7, "f", {"feature-planner": {_OPUS: 400}}),
    ])
    assert out["summary"]["feature-planner"] == {
        "bounded": {"sprints": 2, "bounces": 1, "tokens": 150},
        "high_stakes": {"sprints": 1, "bounces": 0, "tokens": 400}}


def test_report_is_read_only_and_rc0(proj, monkeypatch, capsys):
    weird = [
        {"ts": _T.format(1), "event": "tier_resolved", "feature": "f", "roles": "oops"},
        {"ts": _T.format(2), "event": "tier_resolved", "feature": "f",
         "roles": {"regression-sentinel": 5}},
        {"ts": _T.format(3), "event": "tier_resolved", "feature": "f"},
        {"ts": _T.format(4), "event": "sprint_complete", "feature": "f", "actual": "x"},
        {"event": "sprint_complete", "feature": "f"},
        {"ts": _T.format(5), "event": "sprint_complete", "feature": "f",
         "actual": {"usage": {"by_role_model": [1]}}},
        {"ts": _T.format(6), "event": "critic_verdict", "feature": "f"},
        {"ts": _T.format(7), "event": "phase_rewind", "feature": "f"},
    ]
    scenarios = {"empty": None, "valid": _report_ledger(), "malformed": weird}
    for name, events in scenarios.items():
        for tiering in (_UNSET, {"enabled": True}):
            _set_tiering(proj, tiering)
            if tiering is not _UNSET:
                _install(proj, "reference")
            if events is None:
                (proj / ".sprint" / "ledger.jsonl").unlink(missing_ok=True)
            else:
                _ledger(proj, events)
            before = _tree(proj)
            for extra in ((), ("--json",)):
                rc, out, err = _main(monkeypatch, capsys, "tier", "report", *extra)
                assert rc == 0, (name, tiering, extra, err)
                assert _tree(proj) == before, (name, tiering, extra)
                if extra:
                    assert "sprints" in json.loads(out)
    # and the library entry point is pure on the same shapes
    assert _cal([])["sprints"] == []
    _cal(weird)


# ---------- hot path and templates ----------

def _module_level_imports(tree: ast.Module) -> list[str]:
    names: list[str] = []

    def visit(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue                                        # function-local imports are fine
            if isinstance(child, ast.Import):
                names.extend(a.name for a in child.names)
            elif isinstance(child, ast.ImportFrom):
                base = child.module or ""
                names.append(base)
                names.extend(f"{base}.{a.name}" for a in child.names)
            visit(child)

    visit(tree)
    return names


def test_model_tier_stays_off_the_hook_hot_path():
    for mod in ("gate.py", "usage.py", "platform_adapter.py", "__main__.py"):
        tree = ast.parse((_REPO / "prusik" / mod).read_text())
        bad = [n for n in _module_level_imports(tree) if n.split(".")[-1] == "model_tier"]
        assert bad == [], (mod, bad)
    code = ("import sys, prusik.__main__, prusik.gate; "
            "sys.exit(1 if 'prusik.model_tier' in sys.modules else 0)")
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=str(_REPO))
    assert res.returncode == 0, res.stderr


@pytest.mark.parametrize("name", ["sprint-run.md", "sprint-start.md", "brief-new.md"])
def test_spawn_templates_carry_the_tier_rule(name):
    text = (_TEMPLATES / "commands" / name).read_text()
    assert "prusik tier resolve <feature-slug> --role" in text
    assert not re.search(r"prusik tier resolve\s+\$ARGUMENTS", text)


def test_template_sprint_config_leaves_tiering_off():
    text = _TEMPLATE_CFG.read_text()
    cfg = yaml.safe_load(text)
    assert "model_tiering" not in cfg and model_tier.tiering_enabled(cfg) is False
    assert "# model_tiering:" in text and "enabled: true" in text.split("# model_tiering:")[1][:120]


# ---------- a section shown inside a fenced example is not a section ----------

def test_fenced_override_grammar_example_is_not_an_override(ready):
    """The dogfood failure: a plan documenting the grammar in a fenced block had every
    following line of its Interfaces read as malformed overrides (rc 2 on every spawn)."""
    plan = ready / "design" / _FEATURE / "plan.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(f"# P\n\n## Interfaces\n### override grammar\n```\n## Model tiers\n"
                    f"- <role>: <class> {_EM} <reason>\n```\n- prose that is not an override\n\n"
                    f"## Risks\n- x\n")
    res = model_tier.resolve(_FEATURE)
    assert all(r.source != "override" for r in res.roles.values())


def test_an_unreadable_input_is_named_as_a_read_failure(ready, monkeypatch, capsys):
    """A read failure inside resolve and a write failure in record are different causes;
    both fail closed, and each is named for what actually failed."""
    def unreadable(*a, **k):
        raise OSError("plan.md: permission denied")
    monkeypatch.setattr(model_tier, "resolve", unreadable)
    rc, out, err = _role_cli(monkeypatch, capsys, "regression-sentinel")
    _assert_failed_closed(rc, out, err)
    assert "cannot read the resolution's inputs" in err and "cannot record" not in err


def test_an_unwritable_ledger_is_named_as_a_record_failure(ready, monkeypatch, capsys):
    def unwritable(res):
        raise OSError("ledger: read-only file system")
    monkeypatch.setattr(model_tier, "record", unwritable)
    rc, out, err = _role_cli(monkeypatch, capsys, "regression-sentinel")
    _assert_failed_closed(rc, out, err)
    assert "cannot record the resolution" in err and "cannot read" not in err
