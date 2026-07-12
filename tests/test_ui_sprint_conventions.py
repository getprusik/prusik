"""Template conventions for UI sprints (v0.85.0, field finding #18/#19) — pinned so they
can't silently regress."""

from __future__ import annotations

from pathlib import Path

_TPL = Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
_AGENTS = _TPL / "agents"
_CFG = _TPL / "sprint-config.yaml"


def test_friction_filing_guidance_present():
    """C2 (v0.97.0): the orchestrator and the friction-heavy agents must guide
    filing prusik friction via `prusik feedback` — the always-on scale capture, so
    nothing is lost when no author is live (bridge OFF)."""
    run = (_TPL / "commands" / "sprint-run.md").read_text()
    assert "prusik feedback" in run and "always-on" in run.lower()
    for agent in ("backend-builder", "frontend-builder", "regression-sentinel"):
        s = (_AGENTS / f"{agent}.md").read_text()
        assert "prusik feedback" in s, f"{agent} missing feedback guidance"


def test_sprint_config_declares_reviewing_defer_markers():
    import yaml
    cfg = yaml.safe_load(_CFG.read_text())
    assert "reviewing_defer_markers" in cfg
    assert "browser_smoke" in cfg["reviewing_defer_markers"]


def test_sentinel_defers_browser_markers_pre_integration():
    s = (_AGENTS / "regression-sentinel.md").read_text()
    assert "reviewing_defer_markers" in s
    assert "STALE-SERVER" in s and "sprint-complete" in s


def test_sentinel_neutralizes_package_cov_fail_under_for_scoped_runs():
    """field bridge #5: a scoped/per-module coverage proof must neutralize the
    package-wide --cov-fail-under (false-failure on a narrow set) while keeping it
    armed for the full suite (a full-suite coverage drop IS a regression)."""
    s = (_AGENTS / "regression-sentinel.md").read_text()
    assert "--cov-fail-under=0" in s            # neutralize for scoped runs
    assert "cov-fail-under" in s and "scoped" in s.lower()
    # the scoped-vs-full distinction must be explicit (don't neutralize globally)
    assert "full suite" in s.lower() and "armed" in s.lower()


def test_conventions_enforcer_proves_preexisting_against_head():
    c = (_AGENTS / "conventions-enforcer.md").read_text()
    assert "HEAD" in c and "pre-existing" in c
    assert "git grep" in c or "git show HEAD" in c


def test_builders_exclude_browser_markers_in_full_suite_proof():
    for a in ("backend-builder.md", "frontend-builder.md"):
        t = (_AGENTS / a).read_text()
        assert "reviewing_defer_markers" in t and "browser_smoke" in t
