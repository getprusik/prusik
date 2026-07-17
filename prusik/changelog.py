"""Parse genuine finding CLOSURES from a CHANGELOG — the shared close-the-loop
convention, owned by the shipped engine so `prusik update` (adopter-side) and the
private HQ views never drift on what counts as "closed".

A closure is a `Closes/Fixes/Resolves fb-…` marker (a comma/and-separated LIST
co-closes all its ids: "Closes fb-a, fb-b, fb-c") or a `moat-finding: fb-…` test
marker — the release genuinely FIXED the finding. A bare mention "(fb-…)" or prose
like "Closes the analysis in fb-…" (the id does not immediately follow the verb) is
NOT a closure: a release can discuss an open finding without closing it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# `Closes` (or Fixes/Resolves/Closed) immediately followed by one or more fb-ids,
# comma/and-separated. The id must follow the verb directly — "Closes the analysis
# in fb-…" does not match, so prose can't masquerade as a closure.
_CLOSURE_LEAD = re.compile(
    r"(?:Closes|Fixes|Resolves|Closed)\s+"
    r"((?:fb-[0-9a-f]{12}(?:\s*,\s*|\s+and\s+)?)+)", re.I)
_MOAT_MARK = re.compile(r"moat-finding:\s*(fb-[0-9a-f]{12})", re.I)
_FB_ANY = re.compile(r"fb-[0-9a-f]{12}")


def closed_ids_in(text: str) -> set[str]:
    """Every finding id `text` genuinely CLOSES — expanding the full comma/and list
    after a closure verb, plus each `moat-finding:` id. Not bare mentions/prose."""
    ids: set[str] = set(_MOAT_MARK.findall(text))
    for m in _CLOSURE_LEAD.finditer(text):
        ids.update(_FB_ANY.findall(m.group(1)))
    return ids


def scan_test_moat_markers(root: Path) -> frozenset[str]:
    """Every `fb-…` id carried by a `moat-finding:` marker in a test/benchmark file
    under `root` — the PUBLIC ground truth of regression coverage (the same markers the
    moat metric credits), and the sole source of the closure manifest's membership.
    Test files must use this marker ONLY for the finding they guard, never as literal
    parser test-data (build such inputs at runtime), or they pollute this scan."""
    pat = re.compile(r"moat-finding:\s*(fb-[0-9a-f]{12})")
    found: set[str] = set()
    for sub in ("tests", "benchmarks/cases"):
        base = root / sub
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            if f.is_file() and f.suffix in (".py", ".md", ".txt"):
                found.update(pat.findall(f.read_text(encoding="utf-8", errors="ignore")))
    return frozenset(found)


_CLOSURES_PATH = Path(__file__).resolve().parent / "_closures.json"


def reconcile_closures(existing: dict[str, str], test_moat_ids: frozenset[str],
                       version: str) -> dict[str, str]:
    """The moat-only closure manifest, maintained from GROUND TRUTH — no CHANGELOG:
    it is EXACTLY the findings carrying a `moat-finding:` test marker (a captured
    regression test = a transferable proof). A newly-marked finding stamps `version`
    (the release the test lands in = when the fix ships); a known one keeps its
    recorded version. This is the whole maintenance step: `_closures.json =
    reconcile_closures(load, scan_test_moat_markers(root), __version__)` at release.
    Sound by construction — one public source for membership (test markers), one
    stamped fact for version, nothing derived from a file that goes private."""
    return {fid: existing.get(fid, version) for fid in sorted(test_moat_ids)}


def installed_closures() -> dict[str, str]:
    """The moat-only closure manifest SHIPPED with this engine (`_closures.json`):
    `{fb-id: version}` for every finding with a captured regression test (a
    transferable proof) and the release that shipped its fix. Authoritative for the
    adopter-side closer — version-bound to the wheel, no network, no CHANGELOG. {} if
    absent (an engine predating the shipped map)."""
    try:
        data = json.loads(_CLOSURES_PATH.read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)}


def installed_closed_ids() -> set[str]:
    return set(installed_closures())


def installed_moat_closures() -> dict[str, str]:
    # every entry in the manifest is moat-backed (that is its definition).
    return dict(installed_closures())
