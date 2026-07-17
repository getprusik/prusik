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


def test_moat_closures_maps_id_to_earliest_version():
    # only moat-MARKED closures are transferable proofs; a bare Closes (no moat test)
    # is excluded, and the earliest release that carries the moat marker wins.
    text = (
        "## [0.171.0] — 2026-06-09\nfix. moat-finding: fb-a1753e4a729d.\n\n"
        "## [0.170.0] — 2026-06-08\nCloses fb-deadbeefdead (no moat test here).\n")
    m = changelog.moat_closures(text)
    assert m == {"fb-a1753e4a729d": "0.171.0"}          # moat only; not the bare Closes


def test_moat_closures_earliest_version_wins():
    text = (
        "## [0.180.0] — 2026-06-20\nmoat-finding: fb-a1753e4a729d.\n\n"
        "## [0.171.0] — 2026-06-09\nmoat-finding: fb-a1753e4a729d.\n")
    assert changelog.moat_closures(text)["fb-a1753e4a729d"] == "0.171.0"
