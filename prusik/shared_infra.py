"""Detect when a sprint's diff touches SHARED TEST-INFRA — a surface whose blast
radius is every test in every tier (fb-87c46ae9348a).

The reviewer's touched-set + reverse-dep scope under-models this: a `conftest.py`
is imported by pytest, not by `src/`, so its PRODUCTION reverse-dep set is empty
and the change looks low-blast — while it silently alters behavior for every tier.
A touched-set-green then HIDES cross-tier breakage.

Live precedent (test-infra-fs-isolation): a session-scoped autouse fixture in
`tests/conftest.py` was changed, self-verified on unit+integration only, and
advanced — a cross-tier behavior regression (session-order pollution in 5
`tests/behavior/` tests) shipped uncaught. When such a surface is touched, the
full-suite execution-evidence requirement escalates from advisory to MANDATORY at
build/review exit (`gate._full_suite_gate`).
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

# A fixture whose blast crosses tests: autouse (applied without being requested) or
# a scope broader than the default `function` (session/package/module ⇒ state shared
# across tests, so a change ripples beyond the touched tier).
_BROAD_FIXTURE = re.compile(
    r"""autouse\s*=\s*True|scope\s*=\s*['"](?:session|package|module)['"]""")


def _read_touched(root: Path, rel: str) -> str | None:
    """The sprint's version of a touched file — prefer a builder worktree copy (the
    new content the sprint authored), fall back to the assembled root copy."""
    bases = [*sorted((root / "worktrees").glob("*")), root]
    for base in bases:
        p = base / rel
        try:
            if p.is_file():
                return p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
    return None


def _is_tier_spanning_conftest(rel: str) -> bool:
    """A `conftest.py` at the repo root or the `tests/` root governs EVERY tier
    below it — a change there is all-tier by construction, independent of content."""
    parts = Path(rel).parts
    return rel.endswith("conftest.py") and (
        len(parts) == 1 or (len(parts) == 2 and parts[0] in ("tests", "test")))


def touched_shared_test_infra(root: Path, config: dict | None = None,
                              changed: set[str] | None = None) -> list[dict]:
    """`[{file, reason}]` for each touched file that is shared test-infra; empty when
    the sprint touched no such surface (the common case — zero cost then).

    Three tells, most-specific first:
      1. a root/`tests`-level `conftest.py` (tier-spanning by location);
      2. any `conftest.py` defining an autouse or session/package/module-scoped
         fixture (shared state across tests);
      3. a path matching a project-declared `shared_test_infra` glob in
         sprint-config — the escape hatch for an all-tier import-closure module
         that static analysis can't cheaply prove (e.g. a base test-case class).
    """
    from prusik import consistency
    files = changed if changed is not None else consistency.sprint_changed_files(root)
    globs = (config or {}).get("shared_test_infra") or []
    out: list[dict] = []
    for rel in sorted(files):
        reason: str | None = None
        if _is_tier_spanning_conftest(rel):
            reason = "a root/tests-level conftest.py governs every test tier below it"
        elif rel.endswith("conftest.py"):
            text = _read_touched(root, rel)
            if text and _BROAD_FIXTURE.search(text):
                reason = ("a conftest.py defining an autouse or session/package/module-"
                          "scoped fixture shares state across tests")
        if reason is None:
            hit = next((g for g in globs if fnmatch.fnmatch(rel, g)), None)
            if hit:
                reason = f"declared shared test-infra (matches `{hit}`)"
        if reason:
            out.append({"file": rel, "reason": reason})
    return out
