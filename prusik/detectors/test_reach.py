"""Built-in detector: test-reach — contract surfaces only exercised by tests
OUTSIDE the touched set.

Thin adapter over the existing `test_reach.find_test_reach` engine.
"""

from __future__ import annotations

from prusik.detectors.base import Finding, ScanContext

NAME = "test-reach"
DESCRIPTION = ("contract surfaces (routes, templates, form/handler keys) "
               "referenced only by tests outside the touched set")
DEFAULT_SEVERITY = "info"

_PROMOTED = {"class", "file_hint"}


def _to_finding(d: dict) -> Finding:
    cid = d.get("contract_id", "?")
    kind = d.get("contract_kind", "?")
    return Finding(
        detector=NAME,
        cls=d.get("class", "?"),
        severity=DEFAULT_SEVERITY,
        message=f"{kind} {cid!r} is referenced by tests outside the touched set",
        file=(d.get("file_hint") or None),
        line=None,
        meta={k: v for k, v in d.items() if k not in _PROMOTED},
    )


def detect(ctx: ScanContext) -> list[Finding]:
    from prusik.test_reach import find_test_reach
    return [_to_finding(d) for d in find_test_reach(ctx.files, ctx.root)]
