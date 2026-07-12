"""Full-suite test-count baseline (v0.82.0, field finding #14 hard-gate v2).

The building-exit full-suite gate must tell a real FULL-suite proof from a
"just my new tests" subset — otherwise a builder greens 5 new tests and claims
done while 28 existing ones are red. The discriminator is a COUNT: the baseline
is the largest green tests-executed count this project has produced, learned
automatically from `prove --kind tests` / `gate capture --kind tests` runs (it
only ever rises, via max, so a subset run never lowers it). A building-phase
proof whose count is well under the baseline is a subset → the gate blocks.

Stored at `.sprint/test-suite-size.json`. Self-seeding: the first green full run
(builder prove or the reviewing sentinel's capture) establishes it; after that,
subsets are caught.
"""

from __future__ import annotations

import json
from pathlib import Path

# A proof must reach this fraction of the baseline to count as "the full suite"
# (tolerance for a deselected flake / a skip or two).
FULL_FRACTION = 0.9


def _path(root: Path) -> Path:
    return root / ".sprint" / "test-suite-size.json"


def load(root: Path) -> int:
    p = _path(root)
    if not p.exists():
        return 0
    try:
        return int(json.loads(p.read_text()).get("count", 0))
    except (OSError, ValueError, TypeError):
        return 0


def update(root: Path, count: int) -> int:
    """Raise the baseline to `count` if higher (only inside an initialized
    project). Returns the resulting baseline."""
    if count <= 0 or not (root / ".sprint").exists():
        return load(root)
    cur = load(root)
    if count > cur:
        _path(root).write_text(json.dumps({"count": int(count)}) + "\n")
        return count
    return cur


def looks_full(count: int, baseline: int) -> bool:
    """Is a green run of `count` tests plausibly the FULL suite? True when there's
    no baseline yet (can't judge — the run seeds it) or count is within tolerance."""
    return baseline <= 0 or count >= baseline * FULL_FRACTION
