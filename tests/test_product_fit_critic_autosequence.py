"""fb-8295dd5d4859 — the sprint-start flow ran the product-fit form check, passed, then
told the human to run the product-fit-critic SEPARATELY and re-run — a 3-4 round-trip
dance that recurred every sprint (corroborating the 67% sprint_start_gate rebounce).
brief-critic/scope-critic/plan-critic were already auto-sequenced in the command
templates; product-fit-critic was not. Both sprint-start and sprint-run now auto-invoke
it, collapsing the two-step. Same rigor (the critic still judges soundness), one pass.

moat-finding: fb-8295dd5d4859
"""

from __future__ import annotations

from pathlib import Path

_CMDS = Path(__file__).resolve().parents[1] / "prusik" / "templates" / ".claude" / "commands"


def _read(name):
    return (_CMDS / name).read_text()


def test_sprint_start_auto_invokes_product_fit_critic():
    t = _read("sprint-start.md")
    assert "subagent_type=product-fit-critic" in t or "product-fit-critic" in t
    # it must sequence the critic and carry the artifact fallback, like brief-critic
    assert "product-fit-critique.txt" in t
    assert "mark-fallback --role product-fit-critic" in t


def test_sprint_run_auto_invokes_product_fit_critic():
    t = _read("sprint-run.md")
    assert "product-fit-critic" in t
    assert "product-fit-critique.txt" in t
    assert "mark-fallback --role product-fit-critic" in t


def test_all_pre_sprint_critics_are_sequenced_not_left_manual():
    # the point of the fix: product-fit-critic is sequenced ALONGSIDE the others, not
    # the odd one out that bounces the human off `prusik gate sprint-start`.
    for cmd in ("sprint-start.md", "sprint-run.md"):
        t = _read(cmd)
        for critic in ("brief-critic", "product-fit-critic"):
            assert critic in t, f"{critic} not sequenced in {cmd}"
