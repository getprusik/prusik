"""Detector contract — the normalized Finding + the ScanContext detectors get.

`Finding` is the single,
canonical representation every detector emits and every consumer reads —
scan output, the findings contract, ci-comment, and metrics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ScanContext:
    """Everything a detector needs to inspect a repo. Passed as one object so
    new detectors can reach for inventory/dep-graph/touched-set without every
    detector signature changing later."""
    root: Path
    files: list[Path]
    inventory: dict | None = None
    dep_graph: dict | None = None
    touched_set: set | None = None
    config: dict = field(default_factory=dict)


@dataclass
class Finding:
    """One normalized detector result. Every detector (built-in or
    third-party) emits these; every consumer (scan/findings/ci-comment/metrics)
    reads them."""
    detector: str
    cls: str
    severity: str
    message: str
    file: str | None = None
    line: int | None = None
    expected: list = field(default_factory=list)
    suggested_test: dict | None = None
    meta: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        """The canonical wire shape — what scan output, the findings contract,
        ci-comment, and metrics all read."""
        return {
            "detector": self.detector,
            "class": self.cls,
            "severity": self.severity,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "expected": list(self.expected),
            "suggested_test": self.suggested_test,
            "meta": dict(self.meta),
        }
