"""CI execution truth — what does this repo's CI actually run?

fb-39bd12ff439b: a green CI job credited criteria whose specs run in NO
job — 12 of 22 e2e specs (all auth flows) executed nowhere. The fix's one
rule: the executed set comes from the RUNNER'S OWN resolver (`playwright
--list`, `pytest --collect-only`), never from reading the workflow's arg
list as truth — an arg list can hide globs, configs, projects, or nothing.

Honesty contract: an invocation we cannot resolve (dynamic `${{ }}` args,
resolver not installed on this host) is UNRESOLVED — reported loudly and
counted as covering NOTHING. Unproven proves nothing, and a sweep that
can't see must never say "no orphans". The authoritative place to run the
sweep is where the runners live (dev host with deps, or CI via the Action).

Membership is suffix-based on purpose: playwright reports files relative to
its testDir (`login.spec.ts`), workflows reference package-relative paths
(`tests/e2e/login.spec.ts`) — the basename chain is the stable join.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_RESOLVE_TIMEOUT_SEC = 180
_RUNNER_RE = re.compile(r"\b(playwright\s+test|pytest)\b")
_DYNAMIC_RE = re.compile(r"\$\{\{.*?\}\}")
# A test-file token inside a command / on disk (reuses the ui_coverage shape).
_SPEC_TOKEN_RE = re.compile(
    r"(?:[\w.-]+/)*[\w.-]+\.(?:spec|test|cy|e2e)\.[jt]sx?\b")
# playwright --list line: `  [Project] › file.spec.ts:12:5 › title`
_PW_LIST_RE = re.compile(r"›\s+([^\s:›]+?\.[jt]sx?):\d+:\d+\s+›")
# pytest --collect-only -q line: `path/test_x.py::test_name`
_PY_COLLECT_RE = re.compile(r"^([\w./-]+\.py)::", re.M)


def _read_workflows(root: Path) -> list[Path]:
    d = root / ".github" / "workflows"
    return sorted(list(d.glob("*.yml")) + list(d.glob("*.yaml"))
                  ) if d.is_dir() else []


def test_invocations(root: Path) -> list[dict]:
    """Runner invocations from every workflow's run steps, one dict per
    command line: {workflow, job, command, dynamic, working_directory}.
    Backslash line-continuations are normalized to single commands."""
    import yaml
    out: list[dict] = []
    for wf in _read_workflows(root):
        try:
            data = yaml.safe_load(wf.read_text())
        except yaml.YAMLError:
            continue
        for job_name, job in ((data or {}).get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            for step in job.get("steps") or []:
                run = (step or {}).get("run")
                if not isinstance(run, str) or not _RUNNER_RE.search(run):
                    continue
                script = run.replace("\\\n", " ")
                for line in script.splitlines():
                    line = re.sub(r"\s+", " ", line.strip())
                    if not _RUNNER_RE.search(line):
                        continue
                    out.append({
                        "workflow": wf.name, "job": str(job_name),
                        "command": line,
                        "dynamic": bool(_DYNAMIC_RE.search(line)),
                        "working_directory":
                            (step.get("working-directory")
                             or job.get("defaults", {}).get("run", {})
                                   .get("working-directory")),
                    })
    return out


def _resolver_flag(command: str) -> str:
    if re.search(r"\bplaywright\s+test\b", command):
        return " --list"
    return " --collect-only -q"


def resolve_invocation(inv: dict, root: Path,
                       timeout: int = _RESOLVE_TIMEOUT_SEC
                       ) -> set[str] | None:
    """The invocation's executed file set per its OWN resolver, or None =
    UNRESOLVED (dynamic args / resolver failed). Never guesses from args."""
    if inv.get("dynamic"):
        return None
    cwd = root / inv["working_directory"] if inv.get("working_directory") \
        else root
    cmd = inv["command"] + _resolver_flag(inv["command"])
    try:
        r = subprocess.run(["/bin/bash", "-c", cmd], cwd=cwd,
                           capture_output=True, text=True, timeout=timeout,
                           check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    text = (r.stdout or "") + "\n" + (r.stderr or "")
    files = set(_PW_LIST_RE.findall(text)) | set(_PY_COLLECT_RE.findall(text))
    return files or None                 # a resolver that lists nothing proves nothing


def executed_union(root: Path) -> dict:
    """{resolved: set[str], unresolved: list[inv], invocations: int} across
    every workflow test invocation."""
    resolved: set[str] = set()
    unresolved: list[dict] = []
    invs = test_invocations(root)
    for inv in invs:
        s = resolve_invocation(inv, root)
        if s is None:
            unresolved.append(inv)
        else:
            resolved |= s
    return {"resolved": resolved, "unresolved": unresolved,
            "invocations": len(invs)}


def _covers(resolved: set[str], ref: str) -> bool:
    ref_name = ref.rsplit("/", 1)[-1]
    for f in resolved:
        if ref.endswith(f) or f.endswith(ref) or f.rsplit("/", 1)[-1] == ref_name:
            return True
    return False


def spec_refs(entry: dict) -> list[str]:
    """The spec files a criterion claims CI executes: explicit `ci_executes`
    (string or list) wins; else extracted from its verify commands."""
    ce = entry.get("ci_executes")
    if ce:
        return [ce] if isinstance(ce, str) else [str(x) for x in ce]
    found: list[str] = []
    for key in ("verify_command", "ci_verify_command"):
        found += _SPEC_TOKEN_RE.findall(str(entry.get(key, "")))
    return sorted(set(found))


def credit_check(root: Path, entry: dict) -> tuple[bool, str]:
    """(ok, why): a criterion referencing spec files is creditable only when
    every referenced spec is inside the CI-resolved union. A criterion
    referencing no spec claims nothing — trivially ok."""
    refs = spec_refs(entry)
    if not refs:
        return True, "no spec reference — nothing claimed about CI execution"
    union = executed_union(root)
    missing = [r for r in refs if not _covers(union["resolved"], r)]
    if not missing:
        return True, (f"CI executes {len(refs)} referenced spec(s) "
                      f"(resolved from {union['invocations']} invocation(s))")
    unres = ""
    if union["unresolved"]:
        unres = (f"; {len(union['unresolved'])} invocation(s) UNRESOLVED "
                 f"({', '.join(i['job'] for i in union['unresolved'])}) — "
                 f"they count as covering nothing")
    return False, (
        f"CI does not execute: {', '.join(missing)} — the credited check can "
        f"be green while this spec never runs (fb-39bd12ff439b). Add the "
        f"spec to a CI job's invocation (or fix ci_executes){unres}")
