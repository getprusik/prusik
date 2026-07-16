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
