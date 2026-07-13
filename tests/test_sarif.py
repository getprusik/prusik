"""SARIF 2.1.0 output contract — the guard so the format can't silently break.

SARIF is prusik's interop surface with GitHub code-scanning / CI. These tests
pin the structural contract (schema/version/driver, rule dedup, severity→level
mapping, location shape) and the two entry points (scan findings, prove verdict)
so a refactor that mangles the output fails here, not in an adopter's CI.
"""

from __future__ import annotations

import json

from prusik import __version__, sarif
from prusik.detectors.base import Finding


def _run(doc):
    assert doc["version"] == "2.1.0"
    assert doc["$schema"].endswith("sarif-2.1.0.json")
    assert len(doc["runs"]) == 1
    return doc["runs"][0]


def test_empty_findings_is_valid_clean_run():
    run = _run(sarif.build([]))
    assert run["tool"]["driver"]["name"] == "prusik"
    assert run["tool"]["driver"]["version"] == __version__
    assert run["results"] == []
    assert run["tool"]["driver"]["rules"] == []


def test_finding_becomes_result_with_location_and_region():
    f = Finding(detector="binding", cls="fetch_url", severity="medium",
                message="fetch URL has no matching route", file="src/api.py",
                line=42)
    run = sarif.build([f], root=None)["runs"][0]
    (result,) = run["results"]
    assert result["ruleId"] == "prusik.binding.fetch_url"
    assert result["level"] == "warning"            # medium → warning
    assert result["message"]["text"] == "fetch URL has no matching route"
    loc = result["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "src/api.py"
    assert loc["region"]["startLine"] == 42
    # rule is registered exactly once and the result indexes into it
    rules = run["tool"]["driver"]["rules"]
    assert len(rules) == 1
    assert rules[result["ruleIndex"]]["id"] == "prusik.binding.fetch_url"


def test_severity_maps_to_sarif_levels():
    cases = {"info": "note", "low": "note", "medium": "warning",
             "high": "error", "critical": "error", "bogus": "warning"}
    for sev, expected in cases.items():
        f = Finding(detector="d", cls="c", severity=sev, message="m")
        assert sarif.build([f])["runs"][0]["results"][0]["level"] == expected


def test_rules_deduped_across_findings():
    fs = [Finding(detector="binding", cls="fetch_url", severity="medium", message="a"),
          Finding(detector="binding", cls="fetch_url", severity="medium", message="b"),
          Finding(detector="binding", cls="form_name", severity="medium", message="c")]
    run = sarif.build(fs)["runs"][0]
    assert len(run["results"]) == 3
    assert len(run["tool"]["driver"]["rules"]) == 2   # two distinct (detector,class)


def test_finding_without_line_has_no_region():
    f = Finding(detector="d", cls="c", severity="info", message="m", file="a.py")
    loc = sarif.build([f])["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
    assert "region" not in loc
    assert loc["artifactLocation"]["uri"] == "a.py"


def test_finding_without_file_has_no_location():
    f = Finding(detector="d", cls="c", severity="info", message="m")
    result = sarif.build([f])["runs"][0]["results"][0]
    assert "locations" not in result


def test_prove_proven_is_zero_results():
    verdict = {"command": "pytest -q", "kind": "tests", "exit_code": 0,
               "executed": 12, "min_executed": 1, "proven": True, "reason": "ok"}
    run = _run(sarif.from_prove(verdict))
    assert run["results"] == []
    # the rule is still declared so consumers know what was checked
    assert run["tool"]["driver"]["rules"][0]["id"] == "prusik.prove.not-proven"


def test_prove_not_proven_is_one_error_result():
    verdict = {"command": "pytest -q", "kind": "tests", "exit_code": 0,
               "executed": 0, "min_executed": 1, "proven": False,
               "reason": "exit 0 but only 0 test(s) executed"}
    (result,) = sarif.from_prove(verdict)["runs"][0]["results"]
    assert result["ruleId"] == "prusik.prove.not-proven"
    assert result["level"] == "error"
    assert "0 test" in result["message"]["text"]
    # command captured as a logical location (prove is command-scoped, no file)
    ll = result["locations"][0]["logicalLocations"][0]
    assert ll["fullyQualifiedName"] == "pytest -q"
    assert result["properties"]["executed"] == 0


def test_output_is_deterministic():
    """Same input → byte-identical SARIF (no timestamps / ordering wobble),
    so the output is diffable and cacheable."""
    fs = [Finding(detector="binding", cls="fetch_url", severity="high",
                  message="m", file="x.py", line=3)]
    assert json.dumps(sarif.build(fs)) == json.dumps(sarif.build(fs))
