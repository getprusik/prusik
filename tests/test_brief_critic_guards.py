"""brief-critic template guards (fb-20998b52493a, fb-255e234815a6): the critic
runs the mechanical check UPFRONT (so goal-length / priority-enum violations surface
together, not one-at-a-time after a PASS at the sprint-start gate), and it must NOT flag
the described-but-unbuilt implementation work as a brief 'gap' — describing the work to
build is a brief's job, not a defect.

moat-finding: fb-20998b52493a
moat-finding: fb-255e234815a6
"""

from __future__ import annotations

from pathlib import Path

_TEMPLATE = (Path(__file__).resolve().parents[1]
             / "prusik/templates/.claude/agents/brief-critic.md")


def test_runs_mechanical_check_upfront():
    t = _TEMPLATE.read_text().lower()
    assert "prusik gate brief" in t                  # the mechanical validator
    assert "every mechanical violation together" in t  # all-at-once, not one-at-a-time
    assert "before assessing consistency" in t        # upfront, before the critic review


def test_does_not_flag_described_implementation_as_a_gap():
    t = _TEMPLATE.read_text().lower()
    assert "describes work to be built" in t
    assert "not broken because the feature doesn't exist yet" in t
    assert "fail only for genuine brief defects" in t
