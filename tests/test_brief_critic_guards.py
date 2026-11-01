"""brief-critic guards (fb-20998b52493a, fb-255e234815a6): the mechanical check runs
UPFRONT, before the critic spawn, so goal-length / priority-enum violations surface
together, not one-at-a-time after a PASS at the sprint-start gate. The orchestrator runs it,
because the role has no shell (fb-02d01d7a2a59). And the critic must NOT flag the
described-but-unbuilt implementation work as a brief 'gap' — describing the work to build
is a brief's job, not a defect.

moat-finding: fb-20998b52493a
moat-finding: fb-255e234815a6
"""

from __future__ import annotations

from pathlib import Path

_TEMPLATES = Path(__file__).resolve().parents[1] / "prusik/templates/.claude"
_TEMPLATE = _TEMPLATES / "agents/brief-critic.md"


def test_runs_mechanical_check_upfront():
    for command in ("sprint-start.md", "sprint-run.md"):
        t = (_TEMPLATES / "commands" / command).read_text().lower()
        check = t.index("prusik gate brief")
        assert "every mechanical violation together" in t[check:]  # all-at-once
        assert check < t.index("non-zero exit", check)             # a failure stops here


def test_does_not_flag_described_implementation_as_a_gap():
    t = _TEMPLATE.read_text().lower()
    assert "describes work to be built" in t
    assert "not broken because the feature doesn't exist yet" in t
    assert "fail only for genuine brief defects" in t
