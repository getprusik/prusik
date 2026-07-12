"""init TS-calibration (v0.53.2) — detect a TS stack and hand the operator the
verified scope-emitting `prove` recipe, so a TS adopter is true-proven without
figuring out the incantation. Folds the recipe validated on the first TS adopter
into the adoption surface (`prusik init` detection)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from prusik import detect


def _proj(*files: str) -> Path:
    d = Path(tempfile.mkdtemp(prefix="kit-tsp-"))
    for f in files:
        p = d / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}\n")
    return d


def _ts_snippet(d: Path) -> str | None:
    det = detect.detect_project(d)
    return next((s for s in detect.format_snippets(det) if "prove" in s), None)


def test_ts_turbo_monorepo_detected_with_recipe():
    d = _proj("tsconfig.json", ".eslintrc.json", "turbo.json",
              "pnpm-workspace.yaml", "package.json")
    det = detect.detect_project(d)
    assert det["ts_prove"] == {"tsc": True, "eslint": True, "wrapper": "turbo"}
    s = _ts_snippet(d)
    assert s and "type-check:prove" in s and "extendedDiagnostics" in s
    assert "lint:prove" in s and "-f json" in s
    # the three wrinkle-dodges for a turbo monorepo
    assert "DIRECTLY" in s and "cache replay" in s and "--filter" in s


def test_single_package_ts_no_wrapper_no_monorepo_notes():
    d = _proj("tsconfig.json", ".eslintrc.json", "package.json")
    det = detect.detect_project(d)
    assert det["ts_prove"]["wrapper"] is None
    s = _ts_snippet(d)
    assert "tsc --noEmit --extendedDiagnostics" in s
    assert "pnpm --filter" not in s            # no monorepo exec prefix
    assert "cache replay" not in s             # no turbo notes


def test_eslint_only_emits_lint_not_typecheck():
    d = _proj(".eslintrc.json", "package.json")   # no tsconfig
    det = detect.detect_project(d)
    assert det["ts_prove"] == {"tsc": False, "eslint": True, "wrapper": None}
    s = _ts_snippet(d)
    assert "lint:prove" in s
    assert "type-check:prove" not in s


def test_eslint_detected_via_package_json_dependency():
    d = Path(tempfile.mkdtemp(prefix="kit-tsp-"))
    (d / "package.json").write_text('{"devDependencies": {"eslint": "^9"}}\n')
    (d / "tsconfig.json").write_text("{}\n")
    assert detect.detect_project(d)["ts_prove"]["eslint"] is True


def test_non_ts_project_has_no_recipe():
    d = _proj("pyproject.toml")
    det = detect.detect_project(d)
    assert det["ts_prove"] == {}
    assert _ts_snippet(d) is None
