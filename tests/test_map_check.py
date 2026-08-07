"""Map content re-verify (fb-cda76f07f7e8).

moat-finding:fb-cda76f07f7e8 — a "refreshed-map" commit left the auth
section describing the pre-refactor storage model as current: age and
fingerprint freshness passed while the content lied about the exact
subsystem the sprint touched. Sections that NAME touched files yet stayed
byte-identical get flagged for re-verification.
"""

import json

from prusik import map_check

MAP = """# Map

## Auth
Token storage lives in src/auth/token-store.ts using localStorage.

## Billing
Charges flow through src/billing/charge.ts.

## Overview
No file references here.
"""


def _repo(tmp_path, map_text=MAP):
    (tmp_path / ".sprint").mkdir(parents=True)
    d = tmp_path / "design"
    d.mkdir(parents=True)
    if map_text is not None:
        (d / "map.md").write_text(map_text)
    return tmp_path


def test_snapshot_records_section_hashes(tmp_path):
    root = _repo(tmp_path)
    map_check.snapshot(root)
    data = json.loads((root / ".sprint" / "map-sections.json").read_text())
    assert set(data) == {"Auth", "Billing", "Overview"}


def test_unchanged_referencing_section_flagged_by_name(tmp_path):
    root = _repo(tmp_path)
    map_check.snapshot(root)
    flags = map_check.stale_sections(root, {"src/auth/token-store.ts"})
    (f,) = flags
    assert f["section"] == "Auth"
    assert "src/auth/token-store.ts" in f["references"]


def test_edited_referencing_section_not_flagged(tmp_path):
    root = _repo(tmp_path)
    map_check.snapshot(root)
    (root / "design" / "map.md").write_text(
        MAP.replace("using localStorage", "using httpOnly cookies"))
    assert map_check.stale_sections(root, {"src/auth/token-store.ts"}) == []


def test_section_referencing_only_untouched_files_not_flagged(tmp_path):
    root = _repo(tmp_path)
    map_check.snapshot(root)
    flags = map_check.stale_sections(root, {"src/billing/charge.ts"})
    assert [f["section"] for f in flags] == ["Billing"]   # Auth untouched-ref: silent


def test_basename_reference_also_matches(tmp_path):
    root = _repo(tmp_path)
    map_check.snapshot(root)
    flags = map_check.stale_sections(root, {"packages/x/src/token-store.ts"})
    assert [f["section"] for f in flags] == ["Auth"]


def test_dormant_without_map_or_snapshot(tmp_path):
    root = _repo(tmp_path, map_text=None)
    map_check.snapshot(root)                              # no map: no file
    assert not (root / ".sprint" / "map-sections.json").exists()
    assert map_check.stale_sections(root, {"src/auth/token-store.ts"}) == []
    root2 = _repo(tmp_path / "b")                         # map, no snapshot
    assert map_check.stale_sections(root2, {"src/auth/token-store.ts"}) == []
