"""SARIF 2.1.0 output for prusik findings.

SARIF (Static Analysis Results Interchange Format, OASIS standard) is what the
GitHub Security tab and most CI code-scanning ingests. Emitting it makes the
deterministic evidence prusik already produces legible in the format the
industry already reads — this is proof-plumbing, not a new detector. The
findings/verdicts are unchanged; SARIF is just another view of them.

Design notes:
  - Pure stdlib, deterministic: no timestamps, stable ordering, so the same
    repo state yields byte-identical SARIF (diffable, cacheable).
  - `build()` converts the canonical `Finding` objects (scan/detectors) into a
    SARIF run with one rule per (detector, class) and one result per finding.
  - `from_prove()` converts a `prusik prove` verdict: a PROVEN run emits a run
    with zero results (scanned, nothing wrong); a NOT-PROVEN run emits one
    error result — so the false-clean "tests pass ✅ but nothing ran" surfaces
    in the same dashboard as everything else.

Consumers: `prusik scan --sarif`, `prusik prove --sarif`. Aggregate reports
(`prusik metrics`) intentionally do NOT emit SARIF — SARIF is a per-finding,
per-location format and aggregate counts have neither; forcing them in would
misrepresent the data. Those stay `--json`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from prusik import __version__ as _VERSION

SCHEMA_URI = "https://json.schemastore.org/sarif-2.1.0.json"
SARIF_VERSION = "2.1.0"
_INFO_URI = "https://github.com/getprusik/prusik"

# prusik severities (lowercase) → SARIF result level. SARIF levels are exactly
# {none, note, warning, error}; anything unrecognized maps to warning (visible
# but not build-failing on its own — the exit code is the gate, not the level).
_LEVEL = {
    "info": "note",
    "note": "note",
    "low": "note",
    "medium": "warning",
    "med": "warning",
    "warning": "warning",
    "high": "error",
    "critical": "error",
    "crit": "error",
    "error": "error",
}


def _level_for(severity: str | None) -> str:
    return _LEVEL.get((severity or "").strip().lower(), "warning")


def _rel_uri(file: str | None, root: Path) -> str | None:
    """Repo-relative POSIX URI for SARIF artifactLocation. Absolute paths under
    `root` are relativized; anything else is passed through as-is (already
    relative, or outside the tree)."""
    if not file:
        return None
    p = Path(file)
    if p.is_absolute():
        try:
            p = p.relative_to(root)
        except ValueError:
            return p.as_posix()
    return p.as_posix()


def build(findings: list, root: Path | None = None,
          tool_name: str = "prusik") -> dict[str, Any]:
    """Build a SARIF 2.1.0 document from canonical `Finding` objects.

    One rule per unique (detector, class); one result per finding. A finding
    with a line >= 1 gets a physical region; otherwise a file-level or
    location-less result (both valid SARIF).
    """
    root = (root or Path.cwd()).resolve()

    rules: list[dict] = []
    rule_index: dict[str, int] = {}
    results: list[dict] = []

    for f in findings:
        detector = getattr(f, "detector", "unknown")
        cls = getattr(f, "cls", "finding")
        rule_id = f"prusik.{detector}.{cls}"
        if rule_id not in rule_index:
            rule_index[rule_id] = len(rules)
            rules.append({
                "id": rule_id,
                "name": rule_id.replace(".", "_"),
                "shortDescription": {"text": f"{detector}: {cls}"},
                "defaultConfiguration": {
                    "level": _level_for(getattr(f, "severity", None))
                },
            })

        result: dict[str, Any] = {
            "ruleId": rule_id,
            "ruleIndex": rule_index[rule_id],
            "level": _level_for(getattr(f, "severity", None)),
            "message": {"text": getattr(f, "message", "") or rule_id},
            "properties": {"severity": getattr(f, "severity", None)},
        }
        uri = _rel_uri(getattr(f, "file", None), root)
        if uri is not None:
            phys: dict[str, Any] = {"artifactLocation": {"uri": uri}}
            line = getattr(f, "line", None)
            if isinstance(line, int) and line >= 1:
                phys["region"] = {"startLine": line}
            result["locations"] = [{"physicalLocation": phys}]
        results.append(result)

    return _doc(tool_name, rules, results)


def from_prove(verdict: dict, root: Path | None = None,
               tool_name: str = "prusik") -> dict[str, Any]:
    """Build a SARIF document from a `prusik prove` verdict dict.

    PROVEN  → a run with zero results (the command was checked and is clean).
    NOT-PROVEN → one `error` result carrying the reason + the run context in
    properties, plus a logicalLocation naming the command (prove is
    command-scoped, not line-scoped, so there is no physical location).
    """
    rule_id = "prusik.prove.not-proven"
    rules = [{
        "id": rule_id,
        "name": "prusik_prove_not_proven",
        "shortDescription": {
            "text": "Command did not prove it actually ran clean"
        },
        "fullDescription": {
            "text": "Exit 0 alone does not prove a test/lint/type command ran. "
                    "This result fires when the command failed OR exited 0 with "
                    "no real work observed (the 'tests pass but nothing ran' "
                    "false-clean)."
        },
        "defaultConfiguration": {"level": "error"},
        "helpUri": _INFO_URI,
    }]

    results: list[dict] = []
    if not verdict.get("proven", False):
        results.append({
            "ruleId": rule_id,
            "ruleIndex": 0,
            "level": "error",
            "message": {"text": str(verdict.get("reason", "not proven"))},
            "locations": [{
                "logicalLocations": [{
                    "fullyQualifiedName": str(verdict.get("command", "")),
                    "kind": "namespace",
                }]
            }],
            "properties": {
                "kind": verdict.get("kind"),
                "exit_code": verdict.get("exit_code"),
                "executed": verdict.get("executed"),
                "min_executed": verdict.get("min_executed"),
            },
        })

    return _doc(tool_name, rules, results)


def _doc(tool_name: str, rules: list[dict],
         results: list[dict]) -> dict[str, Any]:
    return {
        "$schema": SCHEMA_URI,
        "version": SARIF_VERSION,
        "runs": [{
            "tool": {
                "driver": {
                    "name": tool_name,
                    "informationUri": _INFO_URI,
                    "version": _VERSION,
                    "rules": rules,
                }
            },
            "columnKind": "utf16CodeUnits",
            "results": results,
        }],
    }
