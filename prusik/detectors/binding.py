"""Built-in detector: caller↔callee contract drift.

Thin adapter over the existing `binding_check.find_unbinding_pairs` engine
(no detection logic here) — maps its dicts onto the normalized Finding.
"""

from __future__ import annotations

from prusik.detectors.base import Finding, ScanContext

NAME = "binding"
DESCRIPTION = ("caller↔callee contract drift — fetch-URL↔route-path and "
               "form-name↔handler-key mismatches")
DEFAULT_SEVERITY = "medium"

# legacy keys promoted to normalized Finding fields (everything else → meta)
_PROMOTED = {"class", "severity", "msg", "template", "template_line",
             "expected", "suggested_test"}


def _to_finding(d: dict) -> Finding:
    return Finding(
        detector=NAME,
        cls=d.get("class", "?"),
        severity=d.get("severity", DEFAULT_SEVERITY),
        message=d.get("msg", ""),
        file=d.get("template"),
        line=d.get("template_line"),
        expected=list(d.get("expected", []) or []),
        suggested_test=d.get("suggested_test"),
        meta={k: v for k, v in d.items() if k not in _PROMOTED},
    )


def detect(ctx: ScanContext) -> list[Finding]:
    from prusik.binding_check import find_unbinding_pairs
    return [_to_finding(d) for d in find_unbinding_pairs(ctx.files, ctx.root)]
