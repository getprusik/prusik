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

import re

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


_SECTION = re.compile(r"^##\s+\[(\d+\.\d+\.\d+)\]", re.M)


def _vkey(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0, 0, 0)


def moat_closures(text: str) -> dict[str, str]:
    """{finding_id: earliest_fix_version} for findings closed with a `moat-finding: fb-X`
    marker — i.e. backed by a CAPTURED regression test (the transferable proof). The
    version is the release that shipped that test. Only moat-marked closures qualify: a
    bare `Closes fb-X` without a moat test isn't a transferable proof, so it's excluded.
    Enables proof-transfer closure of engine findings (the fix is proven green in prusik's
    own CI; an adopter on >= that version inherits the proof)."""
    out: dict[str, str] = {}
    secs = list(_SECTION.finditer(text))
    for i, m in enumerate(secs):
        ver = m.group(1)
        body = text[m.end():(secs[i + 1].start() if i + 1 < len(secs) else len(text))]
        for fid in _MOAT_MARK.findall(body):
            if fid not in out or _vkey(ver) < _vkey(out[fid]):
                out[fid] = ver
    return out
