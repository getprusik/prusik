"""The shipped closure map (`prusik/_closures.json`) must stay in lockstep with the
CHANGELOG — it's what an adopter's `prusik update` closer reads (the public CHANGELOG
is stubbed by the sync, so the map ships in the wheel instead). If it drifts, shipped
fixes stop draining in the field silently. This is the drift guard: regenerate with
`python -c "import json,pathlib; from prusik import changelog as c;
pathlib.Path('prusik/_closures.json').write_text(json.dumps(
c.build_closures(pathlib.Path('CHANGELOG.md').read_text()),indent=2,sort_keys=True)+chr(10))"`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prusik import changelog


def test_shipped_closure_map_matches_changelog():
    expected = changelog.build_closures(Path("CHANGELOG.md").read_text())
    shipped = json.loads(Path("prusik/_closures.json").read_text())
    # In the public projection the CHANGELOG is a stub (the sync replaces it), so the
    # shipped map legitimately carries ids the local CHANGELOG doesn't — the map is
    # authoritative there. Only enforce the drift guard where the CHANGELOG is
    # canonical (contains every shipped id); a MISSING regen in the canonical repo
    # still fails (shipped ⊆ expected, so no skip).
    if set(shipped) - set(expected):
        pytest.skip("CHANGELOG is a projection/stub — shipped map is authoritative here")
    assert shipped == expected, (
        "prusik/_closures.json is stale vs CHANGELOG.md — regenerate it (see this "
        "test's docstring) so the update closer sees the latest closures.")


def test_shipped_map_is_readable_and_non_empty():
    # the closer relies on the packaged reader; it must return real data.
    assert changelog.installed_closed_ids()                     # non-empty
    assert changelog.installed_moat_closures()                  # some moat-backed
    # every moat closure is also a closed id
    assert set(changelog.installed_moat_closures()) <= changelog.installed_closed_ids()


def test_known_moat_finding_is_in_the_shipped_map():
    # a finding closed this session with a moat test must be transfer-eligible.
    assert "fb-f02412bdfd4d" in changelog.installed_moat_closures()
