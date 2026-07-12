"""The brief success-measurability structural gate used a prose-token whitelist that
rejected genuinely-measurable NUMERIC success ("the suite exits 0", "0 new failures",
"run it 100 times with 0 failures") — while brief-critic PASSED them, so the two gates
disagreed and authors inserted filler tokens ("at least") that added no information. The
gate now also accepts a numeric / exit-code / N-of-N / duration pattern.

moat-finding: fb-acdc57e48ea9
"""

from __future__ import annotations

import pytest

from prusik import schema

_SUCCESS_SPEC = schema.load_schema("brief")["required_fields"]["success"]


def _measurable(body: str) -> bool:
    return schema._validate_field("success", body, _SUCCESS_SPEC) == []


@pytest.mark.parametrize("body", [
    "the suite exits 0 on the dev host",
    "0 new failures in the regression suite",
    "run the test 100 times with 0 failures",
    "the command returns 0",
    "100/100 stress runs pass cleanly",
    "no regressions across the affected tests",
    "the page renders in 200ms",
    "the operation completes in 5s",
])
def test_numeric_and_exitcode_success_is_measurable(body):
    assert _measurable(body), f"should be accepted as measurable: {body!r}"


@pytest.mark.parametrize("body", [
    "the endpoint responds within 5s",   # original prose token still works
    "at least 95% of requests succeed",
    "no more than 3 errors",
])
def test_prose_token_path_still_works(body):
    assert _measurable(body)


@pytest.mark.parametrize("body", [
    "the feature works well and users are happy",
    "it should feel fast and look clean",
    "the dashboard is nicer to use",
])
def test_genuinely_unmeasurable_success_still_fails(body):
    # ADVERSARIAL: a vague success with no number/threshold/observable must still FAIL —
    # the fix widens acceptance for real measurability, it does not gut the gate.
    assert not _measurable(body), f"should be rejected as unmeasurable: {body!r}"


def test_bare_unrelated_number_is_not_a_false_pass():
    # "epic 3" / "phase 2" carry a digit but no measurable contract — must still fail
    assert not _measurable("part of the milestone 2 dashboard epic work")
