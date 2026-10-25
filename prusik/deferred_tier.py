"""Deferred-tier destination gate (fb-afbe9deead21).

Reviewing EXCLUDES the project's `reviewing_defer_markers` (live-server/browser
smokes): pre-integration the running server is still the OLD binary, so those tests
false-FAIL. The design deferred them to sprint-complete — but only a brief's own
criteria ever ran them there, so a sprint whose criteria never selected the marker ran
that tier NOWHERE before done. A real regression shipped exactly that way
(fb-afbe9deead21: integration-only criteria; caught only by post-merge live smoke).

This makes the deferral a hand-off with a guaranteed destination: when tracked tests
APPLY a deferred marker (system-detected from code, never claimed), sprint-complete
requires prove-wrapped green evidence that each such tier ran on the integrated tree.
Proof is a passed success criterion that selects the marker (criteria run at
sprint-complete, on the current tree), or a proven `prusik prove` run recorded for THIS
feature after it entered integrating. No integrating window → only criteria can prove
(fail closed: an old or feature-less run never satisfies the gate). An all-skip run
(server down) is NOT PROVEN by construction — prove's own evidence rule.

Blocking by default, unlike the advisory-default terminal gates (ci_observe,
push_or_park): those ADD checks, while this restores one prusik itself moved out of
reviewing — an advisory default would make prusik the party that loosened the
adopter's declared floor. `deferred_tier: {require: false}` is the explicit operator
opt-out: it proceeds, but prints the unproven tier and records the event.
"""

from __future__ import annotations

import ast
import os
import shlex
import subprocess
import sys
from pathlib import Path

from prusik import gate_class, ledger, phases, schema


def _is_test_file(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return (name.startswith("test_") or name.endswith("_test.py")
            or name == "conftest.py")


_PRUNE_DIRS = frozenset((".git", ".hg", ".svn", ".venv", "venv", "node_modules",
                         "worktrees", "__pycache__", ".tox", ".nox", ".sprint",
                         ".mypy_cache", ".pytest_cache", ".ruff_cache", "build", "dist"))


def _test_files(root: Path) -> tuple[list[Path], str]:
    """(test files, source). The git index when it answers — untracked scratch never
    counts, and sprint worktrees (a mirror of the tree) are skipped. Otherwise — a
    non-git project (integrator Mode B), git missing, or git erroring — a walk of the
    working tree. The walk is a SUPERSET of the index, so the switch can only make the
    gate stricter, never quieter, and the source is reported, never silent."""
    try:
        proc = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--", "*.py"],
                              capture_output=True, text=True, check=False, timeout=60)
        if proc.returncode == 0:
            return ([root / rel for rel in proc.stdout.split("\0")
                     if rel and _is_test_file(rel) and not rel.startswith("worktrees/")],
                    "git index")
        why = (proc.stderr or "").strip().splitlines()[:1]
        source = ("working tree (git ls-files: "
                  f"{why[0] if why else f'rc {proc.returncode}'})")
    except (OSError, subprocess.SubprocessError) as e:
        source = f"working tree (git unavailable: {e.__class__.__name__})"
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE_DIRS]
        files.extend(Path(dirpath) / n for n in filenames
                     if n.endswith(".py") and _is_test_file(n))
    return files, source


def _mark_name(node: ast.AST) -> str | None:
    """`pytest.mark.<name>` (optionally called, `pytest.mark.<name>(...)`) → name."""
    if isinstance(node, ast.Call):
        node = node.func
    if (isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute) and node.value.attr == "mark"
            and isinstance(node.value.value, ast.Name) and node.value.value.id == "pytest"):
        return node.attr
    return None


def _applied_marks(tree: ast.Module) -> set[str]:
    """Markers a module APPLIES: decorators on functions/classes, and a module-level
    `pytestmark` assignment. Strings, comments and docstrings are not code."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for dec in node.decorator_list:
                if (name := _mark_name(dec)):
                    found.add(name)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets):
            for sub in ast.walk(value):
                if (name := _mark_name(sub)):
                    found.add(name)
    return found


def scan(root: Path, markers: tuple[str, ...]) -> dict:
    """{'present', 'source', 'unparsed', 'unreadable'} — the deferred markers some test
    file applies. A file this interpreter can't parse (e.g. newer syntax than the global
    CLI's Python) is judged by TEXT: mentioning `mark.<marker>` counts as applying it —
    conservative, so it can make the gate block but never makes it pass. A file that
    can't be READ (a dangling symlink) can't run under pytest either, so it holds no
    applied marker — it is reported, not silently skipped."""
    want = set(markers)
    files, source = _test_files(root) if want else ([], "no markers configured")
    seen: set[str] = set()
    unparsed: list[str] = []
    unreadable: list[str] = []
    for f in files:
        try:
            text = f.read_text(errors="replace")
        except OSError:
            unreadable.append(str(f.relative_to(root)))
            continue
        try:
            seen |= _applied_marks(ast.parse(text)) & want
        except (SyntaxError, ValueError):
            hits = {m for m in want if f"mark.{m}" in text}
            if hits:
                seen |= hits
                unparsed.append(str(f.relative_to(root)))
    return {"present": sorted(seen), "source": source, "unparsed": sorted(unparsed),
            "unreadable": sorted(unreadable)}


def present_markers(root: Path, markers: tuple[str, ...]) -> list[str]:
    """The deferred markers some test file actually applies."""
    return scan(root, markers)["present"]


def _argv(command: str) -> list[str] | None:
    try:
        return shlex.split(command or "")
    except ValueError:
        return None


def _runs_tier(command: str, marker: str, declared: list[str] | None) -> bool:
    if declared and _argv(command) == declared:
        return True
    return schema.command_selects_marker(command, marker)


def _integrating_entered(records: list[dict], feature: str) -> str | None:
    """When THIS feature last entered integrating — the integrated-tree window."""
    ts = [r.get("ts", "") for r in records
          if r.get("event") == "phase_advance" and r.get("to_phase") == "integrating"
          and r.get("feature") == feature]
    return max(ts) if ts else None


def proofs(feature: str, markers: list[str], declared: list[str] | None,
           criteria_results: list[dict], records: list[dict]) -> dict[str, str]:
    """marker → description of its proof, for each marker that HAS one."""
    out: dict[str, str] = {}
    for r in criteria_results or []:
        if (r.get("passed") is True and r.get("kind") == "tests"
                and int(r.get("executed") or 0) >= 1):
            for mk in markers:
                if mk not in out and _runs_tier(r.get("verify_command") or "", mk, declared):
                    out[mk] = f"criterion {r.get('id')!r} ({r.get('executed')} executed)"
    since = _integrating_entered(records, feature)
    if since is None:
        return out
    for e in records:
        if (e.get("event") == "prove_run" and e.get("kind") == "tests"
                and e.get("proven") is True and e.get("feature") == feature
                and e.get("ts", "") >= since and int(e.get("executed") or 0) >= 1):
            for mk in markers:
                if mk not in out and _runs_tier(e.get("command") or "", mk, declared):
                    out[mk] = f"prusik prove ({e.get('executed')} executed at {e.get('ts')})"
    return out


def gate_check(root: Path, config: dict, feature: str,
               criteria_results: list[dict]) -> bool:
    """Sprint-complete check; True to proceed. A variant of the push_guard /
    ci_deliverable terminal-gate shape that takes the criteria results sprint_complete
    already computed (they are one of the two proof sources)."""
    result = scan(root, schema.defer_markers(config))
    present = result["present"]
    off_index = present and result["source"] != "git index"
    if off_index or result["unparsed"] or result["unreadable"]:
        # never a silent change of basis or a silently skipped file — but a markerless
        # project scanned off-index has nothing to report and stays quiet
        note = f"scanned {result['source']}"
        if result["unparsed"]:
            note += (f"; {len(result['unparsed'])} test file(s) unparseable by this "
                     f"interpreter, judged by text: {', '.join(result['unparsed'][:5])}")
        if result["unreadable"]:
            note += (f"; {len(result['unreadable'])} test file(s) unreadable, skipped: "
                     f"{', '.join(result['unreadable'][:5])}")
        print(f"[prusik-gate] deferred-tier: {note}.", file=sys.stderr)
        ledger.append("deferred_tier_scan", feature=feature, source=result["source"],
                      unparsed=result["unparsed"], unreadable=result["unreadable"],
                      present=present)
    if not present:
        return True
    dt_cfg = config.get("deferred_tier")
    if dt_cfg is not None and not isinstance(dt_cfg, dict):
        print(f"[prusik-gate] deferred-tier: `deferred_tier:` must be a mapping "
              f"(command/require), got {dt_cfg!r} — ignored; the gate stays at its "
              f"default (require).", file=sys.stderr)
        dt_cfg = None
    dt_cfg = dt_cfg or {}
    declared = _argv(str(dt_cfg["command"])) if dt_cfg.get("command") else None
    found = proofs(feature, present, declared, criteria_results, ledger.read_all())
    if found:
        ledger.append("deferred_tier_proven", feature=feature, markers=sorted(found),
                      via=[found[m] for m in sorted(found)])
    unproven = [m for m in present if m not in found]
    if not unproven:
        return True

    require = dt_cfg.get("require", True) is not False
    ledger.append("deferred_tier_unproven", feature=feature, markers=unproven,
                  require=require)
    tone = "BLOCKED" if require else "ADVISORY (deferred_tier.require: false)"
    run = (shlex.join(declared) if declared
           else f'<your test command> -m "{" or ".join(unproven)}"')
    print(f"[prusik-gate] deferred-tier {tone} — deferred test tier never ran: "
          f"{', '.join(unproven)}. "
          f"Reviewing excluded these markers (they need the live server on integrated "
          f"code), so sprint-complete is their destination, and nothing proves they ran: "
          f"no passed criterion selects them and no proven `prusik prove` run for "
          f"'{feature}' after it entered integrating does (fb-afbe9deead21 — a real "
          f"regression shipped exactly this way). Restart the server on the integrated "
          f"code, run `prusik prove -- {run}`, then retry sprint-complete. An all-skip "
          f"run (server down) is NOT PROVEN by design.", file=sys.stderr)
    if not require:
        return True
    state = phases.current_sprint_state() or {}
    ledger.append("advance_blocked", from_phase=state.get("phase"),
                  to_phase="sprint_complete", feature=feature,
                  gate_class=gate_class.DEFERRED_TIER,
                  reason=f"deferred tier unproven: {', '.join(unproven)}")
    return False
