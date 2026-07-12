"""`prusik update` — "what's new" delta + resolved-findings loop-closer (B4, v0.114.0)."""

from __future__ import annotations

import shutil

from tests._common import _mktmp_project  # noqa: F401
from prusik import feedback, update_cmd

_CHANGELOG = """# Changelog

## [0.114.0] — 2026-06-06

**Latest thing shipped.** body here. Closes fb-aaaaaaaaaaaa.

## [0.113.0] — 2026-06-06

**Middle thing.** more body.

## [0.112.0] — 2026-06-06

**Already-installed thing.** should not appear.
"""


def test_whats_new_returns_sections_newer_than_installed():
    tmp = _mktmp_project()
    try:
        new, resolved = update_cmd._whats_new(_CHANGELOG, "0.112.0", "0.114.0", tmp)
        vers = [v for v, _ in new]
        assert vers == ["0.114.0", "0.113.0"]          # >installed, ≤latest
        assert "Latest thing shipped." in new[0][1]    # headline = the bold lead
        assert resolved == []                          # no local findings filed
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_resolved_findings_cross_reference_closes_the_loop():
    """If a newer release's CHANGELOG cites a finding the adopter filed locally,
    `update` tells them their reported issue shipped (the loop closing in-product)."""
    tmp = _mktmp_project()
    try:
        # the adopter filed a finding whose id is fb-aaaaaaaaaaaa
        from prusik.feedback import build_record
        rec = build_record("bug", "the thing I reported", ts="2026-06-01T00:00:00")
        # force the id to match the changelog's Closes reference
        rec["id"] = "fb-aaaaaaaaaaaa"
        rec["content_hash"] = "aaaaaaaaaaaa"
        feedback.append(tmp, rec)
        new, resolved = update_cmd._whats_new(_CHANGELOG, "0.113.0", "0.114.0", tmp)
        assert [v for v, _ in new] == ["0.114.0"]
        assert resolved == [("the thing I reported", "0.114.0")]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_whats_new_empty_when_current():
    tmp = _mktmp_project()
    try:
        new, resolved = update_cmd._whats_new(_CHANGELOG, "0.114.0", "0.114.0", tmp)
        assert new == [] and resolved == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
