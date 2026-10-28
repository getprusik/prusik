"""A `## ` line inside a fenced code block is example text, not a section heading.

Surfaced dogfooding model-tier-policy (v0.231.0): a plan that documented the `## Model tiers`
grammar in a fenced example had the rest of its Interfaces read as a `## Model tiers` section.
Fences follow CommonMark, because every brief/scope/plan reader goes through parse_sections
and several default a missing section to empty: a fence rule that is too loose silently
swallows the real headings after a line that merely mentions backticks.
"""

from __future__ import annotations

import pytest

from prusik import schema


def test_headings_inside_fences_are_body_text():
    text = ("## Interfaces\nprose\n```\n## Model tiers\n- <role>: <class>\n```\n"
            "~~~md\n## Risks\n~~~\nmore\n## Risks\n- real\n")
    s = schema.parse_sections(text)
    assert list(s) == ["## Interfaces", "## Risks"]
    assert "## Model tiers" in s["## Interfaces"] and s["## Interfaces"].endswith("more")
    assert s["## Interfaces"].count("```") == 2          # fence lines stay in the body
    assert s["## Risks"] == "- real"


@pytest.mark.parametrize("prose", [
    "```x``` is an inline span at the start of a line",   # backtick info string has a backtick
    "    ```",                                            # 4-space indent: not a fence
    "\t```",                                              # tab indent: not a fence
    "``not a fence``",                                    # only two backticks
])
def test_lines_that_only_mention_backticks_do_not_open_a_fence(prose):
    s = schema.parse_sections(f"## A\n{prose}\n## B\n- kept\n")
    assert list(s) == ["## A", "## B"] and s["## B"] == "- kept"


@pytest.mark.parametrize("fence_open, inner, fence_close", [
    ("```", "```python", "```"),        # an info-string line inside is content, not a close
    ("````", "```", "````"),            # a shorter run does not close a longer fence
    ("~~~", "```", "~~~"),              # the other fence character does not close it
    ("```", "~~~", "```"),
    ("   ```", "## inside", "  ```"),   # up to 3 spaces of indent on either fence
])
def test_only_a_matching_fence_closes(fence_open, inner, fence_close):
    text = f"## A\n{fence_open}\n{inner}\n## hidden\n{fence_close}\n## B\n- kept\n"
    s = schema.parse_sections(text)
    assert list(s) == ["## A", "## B"], s
    assert "## hidden" in s["## A"]


def test_a_longer_closing_run_closes():
    s = schema.parse_sections("## A\n```\n## hidden\n`````\n## B\n- kept\n")
    assert list(s) == ["## A", "## B"]


def test_an_unclosed_fence_runs_to_the_end():
    s = schema.parse_sections("## A\n```\n## B\n- swallowed\n")
    assert list(s) == ["## A"]
    assert "## B" in s["## A"]


def test_documents_without_fences_parse_as_before():
    text = "# T\n\n## Goal\nx\n\n## Type\nnew_feature\n### sub\n- y\n## Risks\n- z\n"
    assert schema.parse_sections(text) == {
        "## Goal": "x", "## Type": "new_feature\n### sub\n- y", "## Risks": "- z"}
