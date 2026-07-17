"""The shipped closure manifest (`prusik/_closures.json`) — the source of truth for
what an adopter's `prusik update` can proof-transfer close.

Sound by construction:
- it is MOAT-ONLY: `{fb-id: version}` for every finding with a captured regression
  test (a transferable proof) and the release that shipped the fix;
- its MEMBERSHIP is the PUBLIC `moat-finding:` test markers (the ground truth of
  regression coverage) — so this guard holds in the public repo too, with NO
  dependence on the (private) CHANGELOG;
- VERSION is stamped once at release and preserved thereafter.

Regenerate at release: `_closures.json = reconcile_closures(load, scan_test_moat_markers(root), __version__)`.
"""

from __future__ import annotations

import json
from pathlib import Path

from prusik import changelog

_ROOT = Path(__file__).resolve().parents[1]


def test_manifest_is_exactly_the_moat_tested_findings():
    # the one invariant: the manifest == the findings that carry a regression-test
    # marker. Derived from PUBLIC test markers, so it can't drift silently and needs
    # no private CHANGELOG. (This is what caught 14 under-counted findings.)
    manifest = set(json.loads((_ROOT / "prusik" / "_closures.json").read_text()))
    markers = set(changelog.scan_test_moat_markers(_ROOT))
    missing = sorted(markers - manifest)   # tested but unrecorded → can't proof-transfer
    stale = sorted(manifest - markers)     # recorded but its test vanished
    assert not missing, (
        f"regression-tested findings absent from _closures.json: {missing} — "
        f"regenerate: reconcile_closures(load, scan_test_moat_markers(root), __version__)")
    assert not stale, f"_closures.json carries findings with no test marker: {stale}"


def test_every_entry_has_a_version_string():
    for fid, ver in changelog.installed_closures().items():
        assert isinstance(ver, str) and ver, f"{fid} has no version"


def test_reconcile_stamps_new_and_preserves_known_and_drops_removed():
    existing = {"fb-aaaaaaaaaaaa": "0.100.0"}
    out = changelog.reconcile_closures(
        existing, frozenset({"fb-aaaaaaaaaaaa", "fb-bbbbbbbbbbbb"}), "0.200.0")
    assert out == {"fb-aaaaaaaaaaaa": "0.100.0",   # known version preserved
                   "fb-bbbbbbbbbbbb": "0.200.0"}    # new marker stamped this release
    # membership follows the markers: a removed marker drops from the manifest
    assert changelog.reconcile_closures(
        out, frozenset({"fb-aaaaaaaaaaaa"}), "0.200.0") == {"fb-aaaaaaaaaaaa": "0.100.0"}


def test_known_moat_finding_is_transfer_eligible():
    # a finding closed this session with a moat test must be in the manifest.
    assert "fb-f02412bdfd4d" in changelog.installed_moat_closures()
    assert changelog.installed_closed_ids() == set(changelog.installed_moat_closures())
