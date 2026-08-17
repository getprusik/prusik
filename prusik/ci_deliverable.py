"""CI-deliverable observation — when the sprint's own deliverable IS a CI job.

fb-e340cd203897: a sprint that changes `.github/workflows/*.yml` can complete `done`
with the deliverable's RUNTIME never observed. A job triggered only on `pull_request`
/ `schedule` / `workflow_dispatch` never fires on the sprint's push to its branch, so
the reviewing gate (real for the CODE) is systematically hollow for the job as a
deliverable — and a genuinely-broken job (a nightly whose first real run was RED) ships
green. This is the wired≠observed class (fb-41877c6a453f) recurring one meta-level up:
the sprint that BUILDS a CI gate never observes the gate.

This detects a CHANGED workflow whose triggers won't fire on the sprint's push, and —
unless a real green run was recorded (`prusik gate mark-ci-observed`) — surfaces it:
advisory + a `ci_deliverable_unobserved` event by default, a hard block under
`ci_observe: {require: true}`. Every signal is git's own diff + the workflow's own
`on:` block, never an agent claim. GitHub Actions parses a bare `on:` key as the YAML
boolean `True` (the "Norway problem"), so the trigger block is read from `data[True]`.
"""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path


def _git(root: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(root), *args],
                           capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _on_spec(data: dict):
    """The workflow's `on:` value. PyYAML maps the bare key `on:` to boolean True
    (YAML 1.1), so prefer `data[True]`; fall back to a literal 'on' string key."""
    if True in data:
        return data[True]
    return data.get("on")


def trigger_events(data: dict) -> set[str]:
    """The event names in a workflow's `on:` — push / pull_request / schedule / …"""
    on = _on_spec(data)
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return {str(x) for x in on}
    if isinstance(on, dict):
        return {str(k) for k in on}
    return set()


def push_observes(data: dict, branch: str) -> bool:
    """True if a push to `branch` fires this workflow — i.e. the sprint's own push
    to its branch runs the job, so its runtime IS observed by normal CI. A `push`
    trigger with no branch filter covers every branch; a `branches:` filter must match
    (GitHub glob), and a `branches-ignore:` that matches means NOT observed."""
    on = _on_spec(data)
    if isinstance(on, str):
        return on == "push"
    if isinstance(on, list):
        return "push" in {str(x) for x in on}
    if not isinstance(on, dict) or "push" not in on:
        return False
    spec = on["push"]
    if not isinstance(spec, dict):
        return True                                   # `push:` with no filter
    if spec.get("branches"):
        return any(fnmatch.fnmatch(branch, str(p)) for p in spec["branches"])
    if spec.get("branches-ignore"):
        return not any(fnmatch.fnmatch(branch, str(p)) for p in spec["branches-ignore"])
    return True                                       # push present, no branch filter


def _diff_base(root: Path) -> str | None:
    """The commit to diff the sprint's changes against. Prefer the base_commit recorded
    at sprint-start (push-independent — captures what the sprint changed even after it's
    pushed); else the merge-base with the tracked upstream (works pre-push). None when
    neither is resolvable (non-git / detached / no upstream) → detector inapplicable."""
    from prusik import phases
    base = (phases.current_sprint_state() or {}).get("base_commit")
    if base and _git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"):
        return base
    upstream = _git(root, "rev-parse", "--abbrev-ref", "@{upstream}")
    return _git(root, "merge-base", upstream, "HEAD") if upstream else None


def changed_workflows(root: Path) -> list[Path] | None:
    """Workflow files the sprint changed (diff from its base to HEAD) — its own CI-job
    deliverables. None when the base can't be resolved (the detector says so, never
    guesses at what changed)."""
    base = _diff_base(root)
    if not base:
        return None
    out = _git(root, "diff", "--name-only", f"{base}..HEAD", "--", ".github/workflows")
    if out is None:
        return None
    return [root / p for p in out.splitlines() if p.strip()]


def observed_workflows(root: Path, feature: str | None) -> set[str]:
    """Workflow names recorded as observed-green for this feature (via
    `mark-ci-observed`) — the real dispatch/PR run that clears the deliverable."""
    from prusik import ledger
    seen = set()
    for e in ledger.read_all():
        if (e.get("event") == "ci_deliverable_observed"
                and (feature is None or e.get("feature") == feature)):
            wf = e.get("workflow")
            if wf:
                seen.add(str(wf))
    return seen


def unobserved(root: Path, branch: str, feature: str | None) -> list[dict]:
    """Changed workflows whose runtime the sprint's push won't observe and which have
    no recorded observed run — each {workflow, triggers}. [] when clean; the caller
    treats a None from changed_workflows() (git/upstream absent) as inapplicable."""
    import yaml
    wfs = changed_workflows(root)
    if not wfs:
        return []
    observed = observed_workflows(root, feature)
    out = []
    for wf in wfs:
        try:
            data = yaml.safe_load(wf.read_text()) or {}
        except (yaml.YAMLError, OSError):
            continue
        if not isinstance(data, dict) or not (data.get("jobs")):
            continue
        if push_observes(data, branch):
            continue                                  # normal push run observes it
        if wf.name in observed:
            continue                                  # a real green run was recorded
        out.append({"workflow": wf.name, "triggers": sorted(trigger_events(data))})
    return out


def gate_check(root: Path, config: dict, feature: str | None, phase: str) -> bool:
    """Advance/complete check: advisory (True) + a `ci_deliverable_unobserved` event by
    default; blocking (False) under `ci_observe: {require: true}`. A changed CI job the
    sprint can't fire is a deliverable shipped untested."""
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD") or "HEAD"
    items = unobserved(root, branch, feature)
    if not items:
        return True
    from prusik import ledger
    names = ", ".join(f"{i['workflow']} (on: {'/'.join(i['triggers']) or '?'})"
                      for i in items)
    ledger.append("ci_deliverable_unobserved", feature=feature, phase=phase,
                  workflows=[i["workflow"] for i in items])
    require = bool((config.get("ci_observe") or {}).get("require"))
    tone = "BLOCKED" if require else "ADVISORY"
    print(f"[prusik-gate] ci-observe {tone} — this sprint changed CI workflow "
          f"deliverable(s) whose triggers WON'T fire on a push to '{branch}', so their "
          f"runtime is never observed by the sprint: {names}. 'reviewing PASS' proves "
          f"the code, not the running job — a job that only runs on a PR/schedule can "
          f"ship broken under a green gate (fb-e340cd203897). Dispatch it, confirm the "
          f"run is GREEN, then record it: `prusik gate mark-ci-observed <workflow> "
          f"--run <run-id>` (gh-verified). "
          + ("" if require else "(Set ci_observe: {require: true} to hard-block.)"))
    return not require
