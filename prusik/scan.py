"""Day-1 catch: scan an existing codebase for binding-mismatch + test-reach
risks WITHOUT requiring prusik FSM adoption.

Adoption-funnel close-the-gap (v0.24.0). The v0.19.0 binding-mismatch
detector + v0.20.0 test-reach detector only fired during reviewer-phase
on the worktree-partial-mirror. A new adopter who isn't yet running
sprints sees nothing. `prusik scan` is the entry point that demonstrates
value BEFORE adoption:

  - No `.claude/`, no `.sprint/`, no FSM state required
  - No worktrees/ subtree required (touched-set = whole repo by default)
  - Read-only — no ledger writes, no manifest mutations
  - Run against any directory: `prusik scan /path/to/repo` or just `prusik scan`

Output: triage list of likely binding bugs + cross-touch-set test risks,
with severity + suggested fixes (same format as `prusik gate check-bindings`
+ `prusik gate check-test-reach`). JSON mode for CI/automation.

The detection code is shared with the phase-time checks (gate.check_bindings
and gate.check_test_reach call into the same prusik/binding_check.py and
prusik/test_reach.py). The novelty of scan-mode is the *entry point* and
the *implicit touched-set = everything* policy, not the detection logic.

Mission boundary preserved: prusik MECHANIZES the flag; adjudicating whether
a flagged binding is a real bug vs. an intentional cross-module call is
operator territory. Scan-mode shows the operator candidate findings;
they decide.

Honest scope:
  - Repo-wide scans take longer than touched-set scans (linear in file
    count). On large repos, --limit can cap.
  - The false-positive rate is HIGHER than phase-time checks because
    the touched-set policy can't filter "intentional cross-module call"
    from "actual binding bug." Scan output is decision-support, not
    actionable-as-is.
  - Test-reach in scan-mode degenerates: with touched-set = whole repo,
    "tests outside touched-set" is empty by definition. Scan-mode skips
    test-reach by default; --include-test-reach forces it but the
    results are usually trivially-zero.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


# Reasonable file-count cap before scan refuses without --force. A large
# enterprise monorepo can have 50k+ files; running detection across all
# of them takes too long to be a useful day-1 catch. The user can raise
# the cap explicitly via --limit or --force.
_DEFAULT_FILE_LIMIT = 5000

# Directories that scan-mode skips by default — generated code, vendored
# deps, build output, cache. These are universally true-noise; if a real
# project's actual source is under one of these names, --include-dir
# can override.
_SKIP_DIR_NAMES = frozenset({
    "node_modules", ".git", ".venv", "venv", "__pycache__",
    "dist", "build", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".runtime", ".next", ".nuxt", "target", "out", "coverage",
    ".coverage", "htmlcov", ".sprint", "worktrees", "reports",
    "briefs",  # ignore prusik's own artifact dir
    ".cache", "vendor",
})


def _collect_files(root: Path, file_limit: int,
                    include_test_reach: bool) -> tuple[list[Path], dict]:
    """Walk `root` and collect files candidate for scanning.

    Skips _SKIP_DIR_NAMES dirs. Stops at file_limit; reports actual
    count + truncation status in the returned stats dict.

    Returns (files, stats_dict) where stats_dict has keys:
      total_files: int (count actually collected, may be < walked due to limit)
      truncated: bool (True if limit hit)
      skipped_dirs: list[str] (names that were pruned)
    """
    files: list[Path] = []
    truncated = False
    skipped: set[str] = set()
    for d in root.rglob("*"):
        if not d.is_file():
            continue
        # Prune by ancestor-dir name
        if any(part in _SKIP_DIR_NAMES for part in d.parts):
            for part in d.parts:
                if part in _SKIP_DIR_NAMES:
                    skipped.add(part)
            continue
        files.append(d)
        if len(files) >= file_limit:
            truncated = True
            break
    return files, {
        "total_files": len(files),
        "truncated": truncated,
        "skipped_dirs": sorted(skipped),
        "file_limit": file_limit,
    }


def _detector_config(root: Path) -> dict:
    """Read the optional `detectors:` block from .claude/sprint-config.yaml.
    Returns {} if absent — scan stays config-free by default."""
    sc = root / ".claude" / "sprint-config.yaml"
    if not sc.exists():
        return {}
    try:
        import yaml
        data = yaml.safe_load(sc.read_text()) or {}
    except Exception:
        return {}
    block = data.get("detectors")
    return dict(block) if isinstance(block, dict) else {}


def _scan_rc(all_findings: list, fail_on, total: int) -> int:
    """rc=2 reserved for errors. With `fail_on` configured, rc=1 only when a
    finding's severity is in that set; otherwise the historical convention
    (rc=1 if any finding at all)."""
    if fail_on:
        sev = set(fail_on)
        return 1 if any(f.severity in sev for f in all_findings) else 0
    return 0 if total == 0 else 1


def scan(root: Path | None = None,
         file_limit: int = _DEFAULT_FILE_LIMIT,
         json_output: bool = False,
         sarif_output: bool = False,
         include_test_reach: bool = False,
         detector_names: list[str] | None = None,
         allow_local: bool = True) -> int:
    """Run binding-mismatch detection (and optionally test-reach) across
    the whole repo. Returns rc=0 if no findings, rc=1 if any.

    The rc convention treats findings as a SIGNAL (worth attention) but
    not necessarily an error — CI can decide what to do with rc=1. For
    a pure "is there anything to look at" check, rc IS the answer.
    """
    if root is None:
        root = Path.cwd()
    root = root.resolve()
    if not root.is_dir():
        print(f"[prusik-scan] ERROR: {root} is not a directory", file=sys.stderr)
        return 2

    files, stats = _collect_files(root, file_limit, include_test_reach)
    if not files:
        msg = f"[prusik-scan] no scannable files under {root}"
        if sarif_output:
            from prusik import sarif as _sarif
            print(json.dumps(_sarif.build([], root), indent=2))
        elif json_output:
            print(json.dumps({"root": str(root), "findings": [],
                              "stats": stats, "message": msg}))
        else:
            print(msg)
        return 0

    # Run detectors through the pluggable registry (built-ins + opt-in
    # project-local .claude/detectors/*.py). `findings` (normalized Finding
    # objects) is the single canonical representation — there is no legacy
    # dict shape.
    from prusik import detectors as _detreg
    from prusik.detectors.base import ScanContext
    cfg = _detector_config(root)
    if detector_names:                       # CLI --detectors overrides config
        cfg["enabled"] = detector_names
    registry = _detreg.load(root, cfg, allow_local=allow_local)
    # Test-reach is opt-in in scan mode: the whole repo is the touched set, so
    # by construction nothing reaches OUTSIDE it. Gated by --include-test-reach.
    if not include_test_reach:
        registry.pop("test-reach", None)

    ctx = ScanContext(root=root, files=files, config=cfg)
    all_findings = []  # list[Finding]
    for name in sorted(registry):
        try:
            all_findings.extend(registry[name].detect(ctx))
        except Exception as e:  # a detector must never crash the whole scan
            print(f"[prusik-scan] detector {name!r} errored: {e}", file=sys.stderr)

    total_findings = len(all_findings)
    fail_on = cfg.get("fail_on")

    if sarif_output:
        from prusik import sarif as _sarif
        print(json.dumps(_sarif.build(all_findings, root), indent=2))
        return _scan_rc(all_findings, fail_on, total_findings)

    if json_output:
        out: dict[str, Any] = {
            "root": str(root),
            "stats": stats,
            "findings": [f.to_json() for f in all_findings],
            "detectors": sorted(registry),
            "total": total_findings,
        }
        print(json.dumps(out, indent=2, default=str))
        return _scan_rc(all_findings, fail_on, total_findings)

    # Human-readable
    print(f"[prusik-scan] scanned {stats['total_files']} file(s) under {root}")
    if stats["truncated"]:
        print(f"          (truncated at --limit {stats['file_limit']}; "
              f"raise --limit or narrow root to scan more)")
    if stats["skipped_dirs"]:
        print(f"          (skipped: {', '.join(stats['skipped_dirs'])})")
    print()

    if not total_findings:
        print("[prusik-scan] no binding-mismatch or test-reach flags. "
              "Either prusik's detectors don't apply to your stack yet "
              "(currently Python+FastAPI / JS+Express / Next.js — see "
              "`prusik eval list` for the surface) OR your codebase passes "
              "the static checks. Run with --json for machine output, or "
              "narrow with `prusik scan path/to/subdir`.")
        return 0

    print(f"[prusik-scan] {total_findings} flag(s) — these are "
          f"DECISION-SUPPORT, not gating:\n")
    print("  (each flag is a CANDIDATE binding issue; adjudicate "
          "whether it's a real bug vs. an intentional cross-module "
          "call. Mission boundary: prusik mechanizes the detection, "
          "the operator decides the verdict.)\n")

    def _sug(f):
        if f.suggested_test:
            print("        ── suggested test (prusik v0.27.0) ──")
            for line in f.suggested_test["code"].splitlines():
                print(f"        {line}")

    fetch_url = [f for f in all_findings
                 if f.detector == "binding" and f.cls == "fetch_url"]
    form_name = [f for f in all_findings
                 if f.detector == "binding" and f.cls == "form_name"]
    reach = [f for f in all_findings if f.detector == "test-reach"]
    other = [f for f in all_findings
             if f.detector not in ("binding", "test-reach")]

    if fetch_url:
        print(f"  ── fetch-URL ↔ route-path mismatches ({len(fetch_url)}): ──")
        for f in fetch_url:
            print(f"    ⚠ {f.file}:{f.line}  "
                  f"({f.meta.get('kind')}) {f.meta.get('url')!r}")
            if f.expected:
                print(f"        suggested (touched routes): {f.expected}")
            _sug(f)
        print()
    if form_name:
        print(f"  ── form-name ↔ handler-key dropthroughs ({len(form_name)}): ──")
        for f in form_name:
            print(f"    ⚠ {f.file}:{f.line}  "
                  f"<input name={f.meta.get('name')!r}>")
            print(f"        handler keys touched: {f.expected}")
            _sug(f)
        print()

    if reach:
        print(f"  ── test-reach cross-touch-set ({len(reach)}): ──")
        for f in reach:
            print(f"    ⚠ {f.meta.get('contract_kind')}: {f.meta.get('contract_id')}")
            for ref in f.meta.get("references", [])[:3]:
                print(f"        ↪ referenced by: {ref}")
        print()

    # Generic section for any other (custom / future) detectors.
    if other:
        by_det: dict = {}
        for f in other:
            by_det.setdefault(f.detector, []).append(f)
        for det, fs in sorted(by_det.items()):
            print(f"  ── {det} ({len(fs)}): ──")
            for f in fs:
                loc = (f"{f.file}:{f.line}" if f.line else (f.file or "")).strip()
                head = f"[{f.severity}] {loc}".strip()
                print(f"    ⚠ {head}  {f.message}")
            print()

    rc = _scan_rc(all_findings, fail_on, total_findings)
    note = (f"rc={rc} (fail-on={fail_on})" if fail_on
            else f"rc={rc} (signals present — adjudicate before acting)")
    print(f"[prusik-scan] done. {total_findings} flag(s). {note}.")
    return rc
