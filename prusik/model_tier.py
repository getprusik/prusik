"""Model-tier policy: the engine decides a role's tier class, the project's profile names the model.

Triage signals (size, priority, team mode, risk terms) become a stakes level; the level moves a
role between three abstract tier classes (`high_stakes`, `bounded`, `mechanical`); the project's
`.claude/model-profile.yaml` maps each class to a model ref the host accepts at spawn. Nothing
here names a model, price or provider: refs are opaque strings the profile supplies and the
platform adapter validates (`spawn_model_refs`, `pinned_model_note`, `model_matches`).

  - `resolve` is pure: it reads config, profile, decision/brief, plan and ledger, and writes none.
  - `run_resolve --role` is the spawn-time call. It prints exactly `model=<ref>` (or
    `model=off`), and is the only path that records a `tier_resolved` ledger event, so a
    prediction is on record only where a spawn used it.
  - Tiering is opt-in (`model_tiering: {enabled: true}` in sprint-config) and FAILS CLOSED once
    on: a missing, partial or invalid profile, an unknown role, or stakes needed before triage
    has run is rc 2 with every problem named, never a default and never a model.
  - `calibrate` / `run_report` compare the recorded prediction with the measured role x model
    tokens and with bounces. Read-only and advisory: its flags never fail it (rc 0), every
    record it cannot use is listed under `skipped` with the reason, and an input it cannot
    read at all is rc 2.

Off the hook hot path: nothing on the PreToolUse/Stop import set imports this module.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import yaml

from prusik import ledger, phases, usage
from prusik.platform_adapter import PlatformAdapter, get_adapter
from prusik.schema import extract_list_items, parse_sections
from prusik.triage import parse_brief_meta

TIER_CLASSES: tuple[str, ...] = ("high_stakes", "bounded", "mechanical")

# Engine-owned base class per packaged role. Each equals the role's template frontmatter model
# under the reference mapping (tests pin the parity).
BASE_CLASS: dict[str, str] = {
    "cartographer": "high_stakes", "scope-critic": "high_stakes",
    "product-fit-critic": "high_stakes", "feature-planner": "high_stakes",
    "plan-critic": "high_stakes",
    "scoping": "bounded", "backend-builder": "bounded", "frontend-builder": "bounded",
    "test-writer": "bounded", "regression-sentinel": "bounded",
    "conventions-enforcer": "bounded", "conventions-scribe": "bounded", "integrator": "bounded",
    "brief-critic": "mechanical", "pr-composer": "mechanical",
}

# Pre-scope roles (stakes are not known yet) plus pr-composer: their class never moves.
FIXED_ROLES: frozenset[str] = frozenset({
    "brief-critic", "product-fit-critic", "cartographer", "scoping", "scope-critic",
    "conventions-scribe", "pr-composer"})

PROFILE_RELPATH = Path(".claude") / "model-profile.yaml"
SIZE_POINTS = {"S": 0, "M": 1, "L": 2, "XL": 3}
PRIORITY_POINTS = {"P0": 2, "P1": 1}                       # anything else 0

_PROFILE_KEYS = ("schema_version", "tiers", "stakes", "risk_weights", "roles")
_NOTE = "flags are signals for a human, not verdicts"
# The events calibrate reads: one of these without a string ts/feature is reported as skipped.
_CALIBRATION_EVENTS = frozenset({"sprint_complete", "tier_resolved", "critic_verdict",
                                 "phase_rewind"})
_SLUG = re.compile(r"[A-Za-z0-9][\w.-]*")
_OVERRIDE = re.compile(r"^(?P<role>[A-Za-z0-9_-]+)\s*:\s*(?P<cls>[A-Za-z_]+)\s*—\s*"
                       r"(?P<reason>\S.*)$")
_OVERRIDE_NO_REASON = re.compile(r"^(?P<role>[A-Za-z0-9_-]+)\s*:\s*(?P<cls>[A-Za-z_]+)\s*—?\s*$")


class TierError(Exception):
    """Every problem found, never just the first. The CLI maps it to rc 2."""

    def __init__(self, problems: list[str] | str):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True)
class Profile:
    tiers: dict[str, str]           # class -> model ref
    stakes_low: int
    stakes_high: int
    risk_weights: dict[str, int]
    roles: dict[str, str]           # project role -> class


class Stakes(NamedTuple):
    level: str                      # "low" | "normal" | "high"
    score: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Signals:
    lane: str                       # "standard" | "trivial"
    size: str | None
    priority: str
    team: bool
    risk_text: str
    source: str


@dataclass(frozen=True)
class RoleTier:
    role: str
    base_class: str
    tier_class: str
    model: str
    source: str                     # "fixed" | "stakes" | "project" | "override"
    reason: str | None = None


@dataclass
class Resolution:
    feature: str
    enabled: bool
    lane: str | None                # None when tiering is off: nothing was resolved
    stakes: Stakes | None
    roles: dict[str, RoleTier]

    def to_json(self) -> dict:
        st = None if self.stakes is None else {"level": self.stakes.level,
                                               "score": self.stakes.score,
                                               "reasons": list(self.stakes.reasons)}
        return {"feature": self.feature, "enabled": self.enabled, "lane": self.lane,
                "stakes": st,
                "roles": {r: {"base_class": t.base_class, "tier_class": t.tier_class,
                              "model": t.model, "source": t.source, "reason": t.reason}
                          for r, t in self.roles.items()}}


# ---- config + profile ----------------------------------------------------------------------

def tiering_enabled(config: dict | None) -> bool:
    """True iff `model_tiering: {enabled: true}`. The key absent is off; present but not a
    mapping, or `enabled` not a bool, is an error (a typo never reads as off)."""
    if not isinstance(config, dict) or "model_tiering" not in config:
        return False
    block = config["model_tiering"]
    if not isinstance(block, dict) or not isinstance(block.get("enabled"), bool):
        raise TierError("model_tiering must be a mapping with `enabled: true|false` "
                        "in .claude/sprint-config.yaml")
    return block["enabled"]


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate_profile(data: object, adapter: PlatformAdapter) -> list[str]:
    """Every problem with a profile document; [] when valid. Nothing is defaulted."""
    if not isinstance(data, dict):
        return ["profile must be a mapping"]
    problems = [f"unknown top-level key '{k}' (allowed: {', '.join(_PROFILE_KEYS)})"
                for k in data if k not in _PROFILE_KEYS]
    if data.get("schema_version") != "1.0" or not isinstance(data.get("schema_version"), str):
        problems.append('schema_version: must be the string "1.0"')

    tiers = data.get("tiers")
    if not isinstance(tiers, dict):
        problems.append("tiers: must map high_stakes, bounded and mechanical to {model: <ref>}")
    else:
        refs = adapter.spawn_model_refs()
        problems += [f"tiers: unknown class '{c}'" for c in tiers if c not in TIER_CLASSES]
        for cls in TIER_CLASSES:
            entry = tiers.get(cls)
            if cls not in tiers:
                problems.append(f"tiers.{cls}: missing")
            elif not (isinstance(entry, dict) and set(entry) == {"model"}
                      and isinstance(entry["model"], str)):
                problems.append(f"tiers.{cls}: must be {{model: <ref>}}")
            elif entry["model"] not in refs:
                note = adapter.pinned_model_note(entry["model"])
                problems.append(f"tiers.{cls}: {note}" if note else
                                f"tiers.{cls}: '{entry['model']}' is not a model ref the host "
                                f"accepts at spawn: {sorted(refs)}")

    stakes_ = data.get("stakes")
    if not (isinstance(stakes_, dict) and set(stakes_) == {"low", "high"}
            and _is_int(stakes_["low"]) and _is_int(stakes_["high"])):
        problems.append("stakes: must be {low: <int>, high: <int>}")
    elif stakes_["low"] >= stakes_["high"]:
        problems.append(f"stakes: low ({stakes_['low']}) must be below high "
                        f"({stakes_['high']})")

    weights = data.get("risk_weights")
    if not isinstance(weights, dict):
        problems.append("risk_weights: must be a mapping of term to int (may be {})")
    else:
        for term, w in weights.items():
            if not (isinstance(term, str) and term.strip()):
                problems.append(f"risk_weights: term {term!r} must be a non-empty string")
            elif not _is_int(w):
                problems.append(f"risk_weights.{term}: weight must be an int, got {w!r}")

    if "roles" in data:
        roles = data["roles"]
        if not isinstance(roles, dict):
            problems.append("roles: must be a mapping of project role to class")
        else:
            for name, cls in roles.items():
                if name in BASE_CLASS:
                    problems.append(f"roles.{name}: packaged roles are engine policy, "
                                    "not project roles")
                elif cls not in TIER_CLASSES:
                    problems.append(f"roles.{name}: unknown class {cls!r} "
                                    f"(one of {', '.join(TIER_CLASSES)})")
    return problems


def load_profile(adapter: PlatformAdapter) -> Profile:
    path = ledger.project_root() / PROFILE_RELPATH
    rel = PROFILE_RELPATH.as_posix()
    if not path.is_file():
        raise TierError(f"{rel} missing (model_tiering is enabled)")
    try:
        loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
        data = yaml.load(path.read_text(), Loader=loader)
    except (yaml.YAMLError, OSError) as e:
        raise TierError(f"{rel} is not readable YAML: {str(e).splitlines()[0]}") from e
    problems = validate_profile(data, adapter)
    if problems:
        raise TierError(problems)
    return Profile(tiers={c: data["tiers"][c]["model"] for c in TIER_CLASSES},
                   stakes_low=data["stakes"]["low"], stakes_high=data["stakes"]["high"],
                   risk_weights=dict(data["risk_weights"]),
                   roles=dict(data.get("roles") or {}))


# ---- lane, signals, stakes -----------------------------------------------------------------

def lane_of(records: list[dict], feature: str) -> str:
    """The latest sprint_started(feature).lane; `standard` when absent."""
    started = [r for r in records if isinstance(r, dict) and r.get("event") == "sprint_started"
               and r.get("feature") == feature]
    started.sort(key=lambda r: str(r.get("ts") or ""))      # stable: ties keep ledger order
    return "trivial" if started and started[-1].get("lane") == "trivial" else "standard"


def load_signals(feature: str, lane: str) -> Signals | None:
    root = ledger.project_root()
    if lane == "trivial":
        brief = root / "briefs" / f"{feature}.md"
        if not brief.is_file():
            raise TierError(f"briefs/{feature}.md missing (the trivial lane resolves from it)")
        meta = parse_brief_meta(brief)
        return Signals(lane="trivial", size=None, priority=str(meta["priority"]), team=False,
                       risk_text=brief.read_text(), source=f"briefs/{feature}.md")
    rel = f"decisions/{feature}.json"
    path = root / rel
    if not path.is_file():
        return None
    try:
        d = json.loads(path.read_text())
        summary, meta = d["scope_summary"], d["brief_meta"]
        size, priority, mode = summary["size"], meta["priority"], d["mode"]
        terms = [*summary["domains"], *summary["risks"]]
    except (ValueError, KeyError, TypeError) as e:
        raise TierError(f"{rel} is malformed ({type(e).__name__}: {e})") from e
    if size not in SIZE_POINTS:
        raise TierError(f"{rel}: size {size!r} is not one of {', '.join(SIZE_POINTS)}")
    if not (isinstance(priority, str) and isinstance(mode, str)
            and all(isinstance(t, str) for t in terms)):
        raise TierError(f"{rel} is malformed (priority, mode, domains and risks must be text)")
    return Signals(lane="standard", size=size, priority=priority, team=mode == "team",
                   risk_text="\n".join(terms), source=rel)


def stakes(signals: Signals, profile: Profile) -> Stakes:
    reasons: list[str] = []
    if signals.size is None:
        score = 0
        reasons.append("trivial lane: size 0")
    else:
        score = SIZE_POINTS[signals.size]
        reasons.append(f"size {signals.size}: +{score}")
    pts = PRIORITY_POINTS.get(signals.priority, 0)
    score += pts
    reasons.append(f"priority {signals.priority}: +{pts}")
    if signals.team:
        score += 1
        reasons.append("team mode: +1")
    for term, weight in profile.risk_weights.items():
        if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", signals.risk_text, re.I):
            score += weight
            reasons.append(f"risk '{term}': +{weight}")
    if score >= profile.stakes_high:
        level, why = "high", f"score {score} >= high {profile.stakes_high} -> high"
    elif score <= profile.stakes_low:
        level, why = "low", f"score {score} <= low {profile.stakes_low} -> low"
    else:
        level, why = "normal", (f"score {score} between low {profile.stakes_low} "
                                f"and high {profile.stakes_high} -> normal")
    reasons.append(why)
    return Stakes(level, score, tuple(reasons))


# ---- class + overrides ---------------------------------------------------------------------

def _stakes_sensitive(role: str, profile: Profile) -> bool:
    return (role not in profile.roles and role in BASE_CLASS
            and role not in FIXED_ROLES and BASE_CLASS[role] != "mechanical")


def tier_class(role: str, level: str | None, profile: Profile) -> tuple[str, str]:
    """(class, source). A project role keeps its profile class; a fixed or mechanical role its
    base; every other packaged role moves one step with the stakes level."""
    if role in profile.roles:
        return profile.roles[role], "project"
    if role not in BASE_CLASS:
        raise TierError(f"{role}: not a packaged role and not in the profile's roles")
    base = BASE_CLASS[role]
    if role in FIXED_ROLES or base == "mechanical":
        return base, "fixed"
    if level is None:
        raise TierError(f"{role}: stakes unknown - run triage first (prusik triage <feature>)")
    if level == "high" and base == "bounded":
        return "high_stakes", "stakes"
    if level == "low" and base == "high_stakes":
        return "bounded", "stakes"
    return base, "stakes"


def parse_overrides(plan_text: str, known_roles: set[str]) -> dict[str, tuple[str, str]]:
    """`## Model tiers` items -> {role: (class, reason)}; TierError lists every bad item."""
    body = parse_sections(plan_text).get("## Model tiers", "")
    if not body.strip():
        return {}
    items = extract_list_items(body)
    problems: list[str] = []
    out: dict[str, tuple[str, str]] = {}
    if not items:
        problems.append("## Model tiers has no list items "
                        "(expected '- <role>: <class> — <reason>')")
    for item in items:
        full = _OVERRIDE.match(item)
        hit = full or _OVERRIDE_NO_REASON.match(item)
        if hit is None:
            problems.append(f"malformed override '{item}' "
                            "(expected '<role>: <class> — <reason>')")
            continue
        role, cls = hit.group("role"), hit.group("cls")
        if role not in known_roles:
            problems.append(f"override: unknown role '{role}'")
        if cls not in TIER_CLASSES:
            problems.append(f"override {role}: unknown class '{cls}' "
                            f"(one of {', '.join(TIER_CLASSES)})")
        if full is None:
            problems.append(f"override {role}: missing reason")
        elif role in out:
            problems.append(f"override {role}: duplicate")
        elif role in known_roles and cls in TIER_CLASSES:
            out[role] = (cls, full.group("reason").strip())
    if problems:
        raise TierError(problems)
    return out


# ---- resolve + record ----------------------------------------------------------------------

def resolve(feature: str, role: str | None = None) -> Resolution:
    """Pure. One root: config, profile, decision, brief and plan come from
    `ledger.project_root()`, and the ledger is read with no explicit root (fb-5b83acbaab4a)."""
    if not _SLUG.fullmatch(feature):
        raise TierError(f"feature {feature!r} is not a valid slug")
    try:
        enabled = tiering_enabled(phases.load_sprint_config())
    except (yaml.YAMLError, OSError) as e:
        raise TierError(f"sprint-config.yaml unreadable: {str(e).splitlines()[0]}") from e
    if not enabled:
        return Resolution(feature, False, None, None, {})
    profile = load_profile(get_adapter())
    try:
        lane = lane_of(ledger.read_all(), feature)
    except (OSError, ValueError) as e:
        raise TierError(f"ledger unreadable: {e}") from e
    plan = ledger.project_root() / "design" / feature / "plan.md"
    overrides = (parse_overrides(plan.read_text(), set(BASE_CLASS) | set(profile.roles))
                 if plan.is_file() else {})          # the WHOLE section, whichever role asks
    names = [role] if role else [*sorted(BASE_CLASS), *sorted(profile.roles)]
    st = None
    if any(n not in overrides and _stakes_sensitive(n, profile) for n in names):
        sig = load_signals(feature, lane)
        st = stakes(sig, profile) if sig is not None else None
    roles: dict[str, RoleTier] = {}
    for n in names:
        if n in overrides:
            cls, reason = overrides[n]
            base = BASE_CLASS.get(n) or profile.roles[n]
            roles[n] = RoleTier(n, base, cls, profile.tiers[cls], "override", reason)
            continue
        cls, source = tier_class(n, st.level if st else None, profile)
        roles[n] = RoleTier(n, BASE_CLASS.get(n) or cls, cls, profile.tiers[cls], source)
    return Resolution(feature, True, lane, st, roles)


def record(res: Resolution) -> None:
    """Append the spawn-time prediction. Called only for an enabled `--role` resolve."""
    ledger.append("tier_resolved", feature=res.feature, lane=res.lane,
                  stakes=res.stakes.level if res.stakes else None,
                  score=res.stakes.score if res.stakes else None,
                  roles={r: {"tier_class": t.tier_class, "model": t.model, "source": t.source}
                         for r, t in res.roles.items()})


# ---- calibrate -----------------------------------------------------------------------------

_BUILD_PHASES = ("building", "solo_execute")
_REWOUND_ROLES = ("backend-builder", "frontend-builder", "test-writer")


def _bounces(role: str, feature: str, window: list[dict]) -> int:
    artifact = {"scoping": f"design/{feature}/scope.md",
                "feature-planner": f"design/{feature}/plan.md"}.get(role)
    n = 0
    for r in window:
        if (artifact and r.get("event") == "critic_verdict" and r.get("verdict") == "REJECTED"
                and r.get("artifact") == artifact):
            n += 1
        elif (role in _REWOUND_ROLES and r.get("event") == "phase_rewind"
              and r.get("to_phase") in _BUILD_PHASES):
            n += 1
    return n


def calibrate(records: list[dict], adapter: PlatformAdapter, enabled: bool = True) -> dict:
    """Predicted tiers vs measured models and bounces, one entry per completed sprint that has
    a `tier_resolved` in its window (previous sprint_complete, this one]. Pure. A record the
    report needs but cannot use is never dropped silently: it lands in `skipped` with why."""
    skipped: list[str] = []
    recs: list[dict] = []
    for r in records:
        if not isinstance(r, dict):
            continue
        if isinstance(r.get("ts"), str) and isinstance(r.get("feature"), str):
            recs.append(r)
        elif r.get("event") in _CALIBRATION_EVENTS:
            skipped.append(f"{r.get('event')} without a string ts/feature "
                           f"(ts={r.get('ts')!r}, feature={r.get('feature')!r})")
    recs.sort(key=lambda r: r["ts"])
    sprints: list[dict] = []
    summary: dict[str, dict[str, dict[str, int]]] = {}
    previous: dict[str, str] = {}
    for done in (r for r in recs if r.get("event") == "sprint_complete"):
        feature, prev = done["feature"], previous.get(done["feature"], "")
        previous[feature] = done["ts"]
        window = [r for r in recs if r["feature"] == feature and prev < r["ts"] <= done["ts"]]
        events = [r for r in window if r.get("event") == "tier_resolved"]
        predicted: dict[str, dict] = {}
        for ev in events:                                    # ts order: the latest wins
            if not isinstance(ev.get("roles"), dict):
                skipped.append(f"{feature} {ev['ts']}: tier_resolved without a roles mapping")
                continue
            for role, p in ev["roles"].items():
                if (isinstance(p, dict) and isinstance(p.get("tier_class"), str)
                        and isinstance(p.get("model"), str)):
                    predicted[role] = p
                else:
                    skipped.append(f"{feature} {ev['ts']}: tier_resolved role {role!r} "
                                   f"lacks a string tier_class/model")
        if not predicted:
            continue
        head = next((e for e in reversed(events) if e.get("stakes") is not None), events[-1])
        try:
            observed_all = usage.role_models(done)
        except ValueError as e:
            skipped.append(f"{feature} {done['ts']}: usage snapshot malformed ({e}); "
                           f"observed models unknown for this sprint")
            observed_all = {}
        rows: dict[str, dict] = {}
        for role in sorted(predicted):
            p = predicted[role]
            observed = observed_all.get(role) or None
            not_applied = (None if observed is None else
                           any(n > 0 and not adapter.model_matches(p["model"], obs)
                               for obs, n in observed.items()))
            bounces = _bounces(role, feature, window)
            rows[role] = {"tier_class": p["tier_class"], "model": p["model"],
                          "source": p.get("source"), "observed": observed,
                          "not_applied": not_applied, "bounces": bounces,
                          "bounced_below_top": p["tier_class"] != "high_stakes" and bounces > 0}
            agg = summary.setdefault(role, {}).setdefault(
                p["tier_class"], {"sprints": 0, "bounces": 0, "tokens": 0})
            agg["sprints"] += 1
            agg["bounces"] += bounces
            agg["tokens"] += sum((observed or {}).values())
        sprints.append({"feature": feature, "ts": done["ts"], "lane": head.get("lane"),
                        "stakes": head.get("stakes"), "score": head.get("score"),
                        "roles": rows})
    return {"enabled": enabled, "sprints": sprints, "summary": summary, "skipped": skipped,
            "note": _NOTE}


# ---- CLI runners ---------------------------------------------------------------------------

def _fail(feature: str, e: TierError) -> int:
    print(f"[prusik-tier] cannot resolve {feature}:", file=sys.stderr)
    for p in e.problems:
        print(f"  - {p}", file=sys.stderr)
    return 2


def _print_table(res: Resolution) -> None:
    st = res.stakes
    print(f"model tiers: {res.feature} (lane {res.lane}, "
          f"stakes {st.level if st else 'n/a'}, score {st.score if st else 'n/a'})")
    for reason in (st.reasons if st else ()):
        print(f"  {reason}")
    w = max(len(r) for r in res.roles)
    mw = max([len("model")] + [len(t.model) for t in res.roles.values()])
    print(f"{'role'.ljust(w)}  class        {'model'.ljust(mw)}  source")
    for r, t in res.roles.items():
        extra = f"  ({t.reason})" if t.reason else ""
        print(f"{r.ljust(w)}  {t.tier_class.ljust(11)}  {t.model.ljust(mw)}  {t.source}{extra}")


def run_resolve(feature: str, role: str | None, json_output: bool) -> int:
    try:
        res = resolve(feature, role)
    except TierError as e:
        return _fail(feature, e)
    except OSError as e:
        return _fail(feature, TierError(f"cannot read the resolution's inputs: {e}"))
    if role is not None and res.enabled:
        try:
            record(res)
        except OSError as e:                  # a prediction that cannot be recorded is no spawn
            return _fail(feature, TierError(f"cannot record the resolution: {e}"))
    if role is not None:
        print(f"model={res.roles[role].model}" if res.enabled else "model=off")
    elif json_output:
        print(json.dumps(res.to_json(), indent=2))
    elif not res.enabled:
        print("model tiering: off (set model_tiering.enabled in .claude/sprint-config.yaml)")
    else:
        _print_table(res)
    return 0


def _report_text(rep: dict) -> list[str]:
    lines = [] if rep["enabled"] else ["model tiering: off"]
    if not rep["sprints"]:
        lines.append("model tier report: no completed sprint has a tier prediction yet")
    for s in rep["sprints"]:
        lines.append(f"{s['feature']} {s['ts'][:10]}  lane {s['lane']}  "
                     f"stakes {s['stakes']} (score {s['score']})")
        for role, r in s["roles"].items():
            seen = ("unobserved" if r["observed"] is None else
                    ", ".join(f"{m}={n}" for m, n in r["observed"].items()))
            flags = [f for f in ("not_applied", "bounced_below_top") if r[f]]
            lines.append(f"  {role}  {r['tier_class']}  {r['model']}  observed {seen}  "
                         f"bounces {r['bounces']}" + (f"  {' '.join(flags)}" if flags else ""))
    by_class: dict[str, dict[str, int]] = {}
    for classes in rep["summary"].values():
        for cls, agg in classes.items():
            tot = by_class.setdefault(cls, {"sprints": 0, "bounces": 0, "tokens": 0})
            for k in tot:
                tot[k] += agg[k]
    lines.append("summary (role-sprints, bounces, tokens by class; per role in --json):")
    for cls in TIER_CLASSES:
        if cls in by_class:
            t = by_class[cls]
            lines.append(f"  {cls}  sprints {t['sprints']}  bounces {t['bounces']}  "
                         f"tokens {t['tokens']}")
    if rep["skipped"]:
        lines.append(f"skipped {len(rep['skipped'])} record(s) it could not use:")
        lines.extend(f"  {s}" for s in rep["skipped"])
    lines.append(rep["note"])
    return lines


def run_report(json_output: bool) -> int:
    """Read-only and advisory: its flags never fail it (rc 0). An input it cannot read is
    rc 2 with the cause — never reported as `off` or as an empty history."""
    try:
        enabled = tiering_enabled(phases.load_sprint_config())
    except (TierError, yaml.YAMLError, OSError) as e:
        print(f"[prusik-tier] model_tiering config unreadable: {e}", file=sys.stderr)
        return 2
    try:
        records = ledger.read_all()
    except (OSError, ValueError) as e:
        print(f"[prusik-tier] ledger unreadable: {e}", file=sys.stderr)
        return 2
    rep = calibrate(records, get_adapter(), enabled)
    print(json.dumps(rep, indent=2) if json_output else "\n".join(_report_text(rep)))
    return 0
