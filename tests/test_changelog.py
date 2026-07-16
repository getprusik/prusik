"""Shared closure-convention parser (prusik.changelog) — the single source of truth
both `prusik update` (adopter) and HQ use to decide what a release CLOSED, so they
can't drift. A comma/and list co-closes every id; prose/mentions never close."""

from __future__ import annotations

from prusik import changelog


def test_closed_ids_covers_every_closure_convention():
    text = (
        "Closes fb-111111111111.\n"                                   # single
        "Closes fb-222222222222, fb-333333333333, fb-444444444444.\n"  # comma list
        "Fixes fb-555555555555 and fb-666666666666.\n"                # and-list
        "moat-finding: fb-777777777777.\n"                            # moat marker
        "Closes the analysis in fb-888888888888; see (fb-999999999999).\n")  # prose/mention
    assert changelog.closed_ids_in(text) == {
        "fb-111111111111", "fb-222222222222", "fb-333333333333",
        "fb-444444444444", "fb-555555555555", "fb-666666666666",
        "fb-777777777777"}


def test_bare_mention_is_not_a_closure():
    assert changelog.closed_ids_in("saw (fb-abcabcabcabc) discussed, still open") == set()
