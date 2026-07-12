"""Closed-loop verification — v0.28.0.

Substantiates the v0.26+v0.27 claim with falsifiable evidence. Before
v0.28.0: prusik emits findings + suggested tests; the agent reads them
via the JSON contract; the agent applies them. **Prusik never
verified the loop actually closed.** On paper, it did. Empirically,
unverified.

v0.28.0 ships the verification:

    prusik verify-loop record [--feature K]   # snapshot at T0
    # ... agent does work ...
    prusik verify-loop check  [--feature K]   # compare T0 vs T1

The check reports per-finding:

    - "resolved":             finding gone AND suggested test in suite
    - "fixed-but-no-test":    finding gone, test NOT added (partial)
    - "test-added-but-fails": test in suite, but it FAILS (suspicious — agent
                              added the test as scaffold but didn't fix the bug)
    - "still-present":        finding still flags at T1 (agent didn't fix)

Mission boundary preserved: prusik MECHANIZES the verification; the
operator decides what each per-finding status means for their sprint.
The "loop closed" assertion is FALSIFIABLE — if it's not true, the
check says so, with rc=1.

Honest scope:
  - `--run-tests` is opt-in for v1. The default is grep-based "did the
    test name appear in any test file?" Running tests requires a
    project-specific runner; emitting the suggested command is safer
    than autodetecting wrong.
  - The checkpoint is keyed by --feature (default: "default"). The
    operator can have multiple parallel loops if needed.
  - Only binding-mismatch findings are tracked through the loop today.
    Test-reach findings can be added when the feedback model is clear.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from prusik import ledger


SCHEMA_VERSION = "1.0"


def _checkpoint_dir(root: Path | None = None) -> Path:
    """Where verify-loop checkpoints live. .sprint/verify-loop/ co-locates
    with other prusik sprint state — operator who already runs `prusik status`
    can find these via `ls .sprint/`."""
    root = root or ledger.project_root()
    return root / ".sprint" / "verify-loop"


def _checkpoint_path(feature: str, root: Path | None = None) -> Path:
    return _checkpoint_dir(root) / f"{feature}.json"


def record(feature: str = "default") -> int:
    """Snapshot current findings + suggested tests as T0. Subsequent
    check() compares against this checkpoint.

    Returns rc=0 on success, rc=1 if no findings exist at T0 (loop
    has nothing to verify — caller should investigate before agent
    work or skip verify-loop for this sprint).
    """
    root = ledger.project_root()
    from prusik import scan as kit_scan
    files, _stats = kit_scan._collect_files(
        root, file_limit=5000, include_test_reach=False)
    if not files:
        print(f"[prusik-verify-loop] no scannable files under {root}",
              file=sys.stderr)
        return 1

    from prusik.binding_check import find_unbinding_pairs
    raw_findings = find_unbinding_pairs(files, root)

    # Project to the verify-loop shape — only fields we need for compare
    snapshot_findings = []
    for f in raw_findings:
        snapshot_findings.append({
            "id": _finding_key(f),
            "class": f.get("class"),
            "template": f.get("template", ""),
            "url": f.get("url"),
            "name": f.get("name"),
            "expected": f.get("expected", []),
            "summary": f.get("msg", "")[:200],
            "suggested_test": f.get("suggested_test"),
        })

    cp = {
        "schema_version": SCHEMA_VERSION,
        "feature": feature,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "findings": snapshot_findings,
        "stats": {"count": len(snapshot_findings)},
    }
    path = _checkpoint_path(feature, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cp, indent=2))

    ledger.append("verify_loop_recorded", feature=feature,
                   finding_count=len(snapshot_findings))

    print(f"[prusik-verify-loop] T0 recorded: {len(snapshot_findings)} "
          f"finding(s) under feature {feature!r}")
    print(f"  checkpoint: {path.relative_to(root)}")
    if not snapshot_findings:
        print("  (no findings to verify — agent has nothing to fix; "
              "running check later will report all-clear by default)")
        return 1
    for f in snapshot_findings:
        suggested = "yes" if f.get("suggested_test") else "no"
        print(f"  • {f['id']}  (suggested test: {suggested})")
    return 0


def _finding_key(finding: dict) -> str:
    """Stable id per finding shape — matches prusik/findings.py convention
    so verify-loop and findings agree on what counts as 'the same
    finding'."""
    cls = finding.get("class") or finding.get("binding_class", "?")
    template = finding.get("template", "")
    url = finding.get("url") or ""
    name = finding.get("name") or finding.get("form_name") or ""
    return f"{cls}:{template}|{url}|{name}"


def _find_test_in_suite(test_name: str, root: Path) -> list[str]:
    """Grep the project for `def {test_name}(` or `test('{name}'`
    occurrences. Returns the list of files containing matches."""
    if not test_name:
        return []
    # Python: def test_xxx(
    py_pattern = f"def {test_name}("
    # JS/TS: test('xxx', / test("xxx",
    js_pattern_dq = f'test("{test_name}"'
    js_pattern_sq = f"test('{test_name}'"
    matches: list[str] = []

    # Walk likely test dirs; bound the search
    skip = {".git", "node_modules", ".venv", "venv", "__pycache__",
             "dist", "build", ".pytest_cache", ".next", "target",
             ".mypy_cache", ".ruff_cache"}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in skip for part in p.parts):
            continue
        if p.suffix not in (".py", ".js", ".jsx", ".ts", ".tsx"):
            continue
        try:
            content = p.read_text(errors="ignore")
        except OSError:
            continue
        if (py_pattern in content or
            js_pattern_dq in content or
            js_pattern_sq in content):
            try:
                matches.append(str(p.relative_to(root)))
            except ValueError:
                matches.append(str(p))
    return matches


def _run_test(test_name: str, root: Path, stack: str) -> tuple[bool, str]:
    """Best-effort: run the suggested test by name. Returns (passed, output).

    Python: pytest -k <name>
    JS: jest -t <name> if available (best-effort detection)
    """
    if stack == "python":
        # Prefer uv run pytest if available, else plain pytest
        cmd = ["uv", "run", "pytest", "-k", test_name, "--tb=short", "-q"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                                cwd=root, timeout=120, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            cmd = ["pytest", "-k", test_name, "--tb=short", "-q"]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True,
                                    cwd=root, timeout=120, check=False)
            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                return False, f"could not run pytest: {e}"
        passed = (r.returncode == 0)
        return passed, (r.stdout + r.stderr)[-500:]
    if stack == "js":
        # Try common JS runners
        for runner in (["npx", "jest", "-t", test_name],
                        ["npx", "vitest", "run", "-t", test_name]):
            try:
                r = subprocess.run(runner, capture_output=True, text=True,
                                    cwd=root, timeout=120, check=False)
                return (r.returncode == 0), (r.stdout + r.stderr)[-500:]
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        return False, "no JS test runner found (tried jest, vitest)"
    return False, f"unknown stack: {stack}"


def check(feature: str = "default", run_tests: bool = False,
          json_output: bool = False) -> int:
    """Compare T1 (current state) against the T0 checkpoint.

    For each T0 finding, classify:
      - resolved              (gone + test in suite [+ test passes if run_tests])
      - fixed-but-no-test     (gone, but suggested test not in suite)
      - test-added-but-fails  (test in suite but fails — bug not actually fixed)
      - still-present         (finding still flags at T1)

    rc=0 ONLY when all T0 findings are "resolved". Any other status → rc=1.
    """
    root = ledger.project_root()
    cp_path = _checkpoint_path(feature, root)
    if not cp_path.exists():
        msg = (f"[prusik-verify-loop] no checkpoint at {cp_path}; "
               f"run `prusik verify-loop record` first")
        if json_output:
            print(json.dumps({"error": msg}))
        else:
            print(msg, file=sys.stderr)
        return 2

    try:
        cp = json.loads(cp_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        msg = f"[prusik-verify-loop] cannot read checkpoint: {e}"
        if json_output:
            print(json.dumps({"error": msg}))
        else:
            print(msg, file=sys.stderr)
        return 2

    t0_findings = cp.get("findings", [])

    # T1 scan
    from prusik import scan as kit_scan
    files, _stats = kit_scan._collect_files(
        root, file_limit=5000, include_test_reach=False)
    from prusik.binding_check import find_unbinding_pairs
    t1_raw = find_unbinding_pairs(files, root)
    t1_ids = {_finding_key(f) for f in t1_raw}

    per_finding: list[dict] = []
    resolved_count = 0
    for f in t0_findings:
        fid = f["id"]
        sug = f.get("suggested_test")
        test_name = sug["name"] if sug else None
        stack = sug["stack"] if sug else None

        # Status determination
        if fid in t1_ids:
            status = "still-present"
            test_in_suite: list[str] = []
            test_result = None
        else:
            # Finding is gone — check test
            test_in_suite = (_find_test_in_suite(test_name, root)
                              if test_name else [])
            if not test_name:
                status = "resolved"  # no suggestion to apply
                test_result = None
            elif not test_in_suite:
                status = "fixed-but-no-test"
                test_result = None
            elif run_tests:
                passed, output = _run_test(test_name, root, stack or "python")
                if passed:
                    status = "resolved"
                else:
                    status = "test-added-but-fails"
                test_result = {"passed": passed, "output": output}
            else:
                status = "resolved"  # test in suite; we didn't run it
                test_result = None

        if status == "resolved":
            resolved_count += 1
        per_finding.append({
            "id": fid,
            "status": status,
            "suggested_test_name": test_name,
            "test_in_suite_files": test_in_suite,
            "test_result": test_result,
            "summary": f.get("summary", ""),
        })

    total = len(t0_findings)
    loop_closed = (resolved_count == total) and total > 0
    rc = 0 if loop_closed else (1 if total > 0 else 0)

    ledger.append("verify_loop_checked", feature=feature,
                   t0_count=total, resolved=resolved_count,
                   loop_closed=loop_closed)

    if json_output:
        out = {
            "schema_version": SCHEMA_VERSION,
            "feature": feature,
            "t0_recorded_at": cp.get("recorded_at"),
            "t1_checked_at": datetime.now(timezone.utc).isoformat(),
            "aggregate": {
                "t0_findings": total,
                "resolved": resolved_count,
                "loop_closed": loop_closed,
            },
            "per_finding": per_finding,
        }
        print(json.dumps(out, indent=2))
        return rc

    print(f"[prusik-verify-loop] T0 → T1 comparison (feature: {feature!r}):\n")
    print(f"  T0 ({cp.get('recorded_at', '?')}): {total} finding(s)")
    print(f"  T1 ({datetime.now(timezone.utc).isoformat()}): "
          f"{len(t1_ids)} finding(s) currently\n")
    for r in per_finding:
        mark = "✓" if r["status"] == "resolved" else "⚠"
        print(f"  {mark} {r['id']}")
        print(f"      status: {r['status']}")
        if r["suggested_test_name"]:
            print(f"      suggested test: {r['suggested_test_name']}")
            if r["test_in_suite_files"]:
                print(f"      in test suite: {r['test_in_suite_files']}")
            else:
                print("      in test suite: (NOT FOUND)")
            if r["test_result"]:
                tr = r["test_result"]
                print(f"      test run: "
                      f"{'PASS' if tr['passed'] else 'FAIL'}")
        print()

    if loop_closed:
        print(f"[prusik-verify-loop] loop closed: ✓ "
              f"{resolved_count}/{total} T0 findings resolved end-to-end")
    elif total == 0:
        print("[prusik-verify-loop] no T0 findings to verify (T0 was clean)")
    else:
        print(f"[prusik-verify-loop] loop NOT closed: "
              f"{resolved_count}/{total} resolved")
        print("  Investigate the ⚠ entries above. rc=1.")
        if not run_tests:
            print("  Tip: re-run with --run-tests to actually invoke "
                  "pytest/jest on the suggested tests.")
    return rc
