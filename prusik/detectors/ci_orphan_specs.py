"""ci-orphan-specs — e2e specs on disk that NO CI invocation executes.

fb-39bd12ff439b at scale: 12 of 22 specs (all auth flows, onboarding) ran in
no job, silently, for weeks. Diff-time gates can't see this rot; a sweep
names it any day. Executed truth comes from `ci_exec` (the runners' own
resolvers); an unresolved invocation is its own finding and covers nothing —
when nothing resolves, this detector reports the blindness, never "no
orphans".
"""

from __future__ import annotations

import re

from prusik import ci_exec
from prusik.detectors.base import Finding, ScanContext

NAME = "ci-orphan-specs"
DESCRIPTION = ("e2e specs on disk that no CI invocation executes — resolved "
               "via the runners' own resolvers; unresolved invocations are "
               "loud and cover nothing (fb-39bd12ff439b)")
_SPEC_FILE_RE = re.compile(r"\.(?:spec|test|cy|e2e)\.[jt]sx?$")
_E2E_DIR_RE = re.compile(r"(?:^|/)(?:e2e|acceptance|smoke)(?:/|$)", re.I)


def detect(ctx: ScanContext) -> list[Finding]:
    root = ctx.root
    inventory = sorted(
        str(p.relative_to(root)) for p in ctx.files
        if p.is_file() and _SPEC_FILE_RE.search(p.name)
        and _E2E_DIR_RE.search(str(p.relative_to(root))))
    if not inventory:
        return []
    union = ci_exec.executed_union(root)
    out: list[Finding] = []
    for inv in union["unresolved"]:
        out.append(Finding(
            detector=NAME, cls="ci-invocation-unresolved", severity="warn",
            message=(f"UNRESOLVED CI invocation "
                     f"({inv['workflow']} · job {inv['job']}): "
                     f"`{inv['command'][:90]}` — it counts as covering "
                     f"NOTHING; run the scan where the runner is installed "
                     f"(or in CI) for the authoritative sweep."),
            file=f".github/workflows/{inv['workflow']}"))
    if not union["resolved"]:
        out.append(Finding(
            detector=NAME, cls="ci-execution-unresolved", severity="warn",
            message=(f"No CI test invocation could be resolved on this host "
                     f"({union['invocations']} found) — {len(inventory)} "
                     f"spec file(s) exist but coverage is UNKNOWN, not "
                     f"clean.")))
        return out
    for spec in inventory:
        if not ci_exec._covers(union["resolved"], spec):
            out.append(Finding(
                detector=NAME, cls="ci-orphan-spec", severity="high",
                message=(f"{spec}: executes in NO CI invocation — a green "
                         f"pipeline says nothing about it "
                         f"(fb-39bd12ff439b class)."),
                file=spec))
    return out
