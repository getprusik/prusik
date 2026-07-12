"""Narrative-claim detector — give the BUILDER's prose the prove-or-it-didn't-happen
floor the reviewer already has.

Field escape #3: a build report (`reports/<feature>/build-<role>.txt`) literally said
"BASELINE PROVEN: fails on main" for two failures — but main was green, so the claim
was false. `prove-flaky` (v0.160) and `baseline prove` (fb-c871) gate the REVIEWER's
flake/pre-existing claims with git-stash A/B-vs-base proof; the builder's
"baseline-proven / pre-existing / fails-on-main" NARRATIVE was un-gated prose — a
trust surface with no floor, and a real regression walks straight through it.

This reconciles dismissal/proof CLAIMS in build reports against actual PROOF. The
ungameable class (An adopter) is "unproven dismissal of a red", not one phrase — keying on
a literal string just makes builders rephrase, so it matches the SEMANTICS:
  - PROOF-ASSERTIONS ("baseline proven", "stash-proven", "fails on main", "proven
    flaky") always count — they assert a proof was performed;
  - DISMISSALS ("pre-existing", "known flake", "unrelated", "out of scope",
    "transient", "env-gated", "not our change") count when the report shows an actual
    failure — so rephrasing can't evade it, while a dismissal word in a GREEN report
    (dismissing nothing) doesn't false-flag;
  - proof is FEATURE-SCOPED: a `known_failure_baseline` ledger event with proven=True
    whose `feature` matches THIS one. The ledger event is the reliable record (it's
    emitted on every prove; a refused prove records proven=False), and tagging it by
    feature is what stops another feature's proof from laundering an unproven claim —
    a claim in feature F's report is backed only by F's own successful proves.

Precision-first (a false flag erodes the gate): vague or negated mentions ("no
pre-existing failures") don't match the affirmative patterns, and a feature that
actually ran `baseline prove` is never flagged. Honest scope (v1): backing is at the
feature level (did THIS feature prove anything), not yet per-individual-test —
matching each claimed test to its baselined entry needs failing-test-IDs in the
captured evidence (today a count), the v2 sharpening.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from prusik import ledger

# PROOF-ASSERTIONS — the report claims a proof was performed. Self-indicating (they
# assert a proof of a failure), so they count regardless of other context.
_PROOF_ASSERTION: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"baseline[\s_-]*proven", re.I), "baseline-proven"),
    (re.compile(r"stash[\s_-]*proven", re.I), "stash-proven"),
    (re.compile(r"proven[\s_-]+pre[\s_-]*existing", re.I), "proven-pre-existing"),
    (re.compile(r"\bfails?\s+on\s+(?:main|base|head)\b", re.I), "fails-on-base"),
    (re.compile(r"(?:proven|demonstrated)[\s_-]*flaky", re.I), "proven-flaky"),
]

# DISMISSALS — the ungameable class (An adopter): the escape is "unproven dismissal of a
# red", not any one phrase. Key on the SEMANTICS so a builder can't just rephrase
# "baseline proven" into "pre-existing" / "unrelated" / "out of scope". Counted ONLY
# when the report shows an actual failure (below) — that keeps precision: a dismissal
# term in a green report is not dismissing anything.
_DISMISSAL: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bpre[\s_-]*existing\b", re.I), "pre-existing"),
    (re.compile(r"\b(?:known[\s_-]*)?flak(?:e|y)\b", re.I), "flake"),
    (re.compile(r"\bintermittent\b", re.I), "intermittent"),
    (re.compile(r"\btransient\b", re.I), "transient"),
    (re.compile(r"\bunrelated\b", re.I), "unrelated"),
    (re.compile(r"\bout[\s_-]*of[\s_-]*scope\b", re.I), "out-of-scope"),
    (re.compile(r"\benv(?:ironment)?\s+(?:issue|gated|specific)\b", re.I), "env-gated"),
    (re.compile(r"\bnot\s+(?:my|our|this|the)\s+\w*\s*change\b", re.I), "not-our-change"),
]

# An AFFIRMATIVE failure: a NON-ZERO count ("[1-9]… fail") or ✗ (case-insensitive),
# OR an explicit caps FAIL status (case-sensitive, so it doesn't fire on "0 failed").
# A clean "1106 passed, 0 failed" report never triggers the dismissal class.
_FAILURE_INDICATOR = re.compile(r"\b[1-9]\d*\s+fail|✗", re.I)
_FAILURE_CAPS = re.compile(r"\bFAIL(?:ED|ING|URE)?\b")


def _has_failure(text: str) -> bool:
    return bool(_FAILURE_INDICATOR.search(text) or _FAILURE_CAPS.search(text))


def scan_claims(text: str) -> list[dict[str, Any]]:
    """Proof-assertions (always), plus unproven dismissals of an actual red (only when
    the report shows a real failure) — keyed on the dismissal semantics, not a literal
    string, so rephrasing doesn't evade it."""
    has_failure = _has_failure(text)
    out: list[dict[str, Any]] = []
    for i, line in enumerate(text.splitlines(), 1):
        for pat, label in _PROOF_ASSERTION:
            if pat.search(line):
                out.append({"claim": label, "line": i, "text": line.strip()[:120]})
        if has_failure:
            for pat, label in _DISMISSAL:
                if pat.search(line):
                    out.append({"claim": f"unproven-dismissal:{label}",
                                "line": i, "text": line.strip()[:120]})
    return out


def _proven_tests_for_feature(records: list[dict], feature: str) -> set[str]:
    """Tests THIS feature successfully baselined — proven=True `known_failure_baseline`
    events tagged with this feature. Feature-scoped so another feature's proof can't
    back this feature's claim (the v0.174.1 precision fix)."""
    out: set[str] = set()
    for r in records:
        if (r.get("event") == "known_failure_baseline"
                and r.get("proven") is True and r.get("feature") == feature):
            t = r.get("test")
            if t:
                out.add(t)
    return out


def narrative_check(feature: str, root: Path, *,
                    records: list[dict] | None = None) -> dict[str, Any]:
    """Reconcile build-report proof-claims against actual proof. A report asserting a
    proof was done while THIS feature proved nothing = an un-gated narrative escape."""
    reports_dir = root / "reports" / feature
    claims: list[dict[str, Any]] = []
    if reports_dir.is_dir():
        for rep in sorted(reports_dir.glob("build-*.txt")):
            for c in scan_claims(rep.read_text(encoding="utf-8", errors="ignore")):
                claims.append({"report": rep.name, **c})
    records = records if records is not None else ledger.read_all()
    proven = _proven_tests_for_feature(records, feature)
    has_proof = bool(proven)
    unbacked = bool(claims) and not has_proof
    return {
        "feature": feature,
        "claims": claims,
        "has_proof": has_proof,
        "proven_baselines": sorted(proven),
        "unbacked": unbacked,
        "clean": not unbacked,
    }


def run(feature: str, json_output: bool = False, strict: bool = False) -> int:
    """`prusik narrative-check <feature>` — advisory by default; `--strict` rc≠0 so a
    sprint can hard-gate the builder's claims the way the reviewer's are gated."""
    root = ledger.project_root()
    from prusik import calibration
    strict = strict or calibration.is_promoted("narrative_detector", root)
    rep = narrative_check(feature, root)
    if json_output:
        print(json.dumps(rep, indent=2))
        return 1 if (strict and not rep["clean"]) else 0

    if not rep["claims"]:
        print(f"[prusik-narrative] '{feature}': no proof-claims in the build "
              f"report(s) to gate.")
        return 0
    if rep["clean"]:
        print(f"[prusik-narrative] '{feature}': {len(rep['claims'])} proof-claim(s) "
              f"backed by a real baseline proof.")
        return 0

    ledger.append("narrative_flagged", feature=feature, claims=len(rep["claims"]))
    print(f"[prusik-narrative] '{feature}': build report ASSERTS a proof that was "
          f"never performed (no known-failures entry, no proven baseline event):")
    for c in rep["claims"]:
        print(f"    ✗ {c['report']}:{c['line']}  “{c['claim']}”  — {c['text']}")
    print("\n  Run `prusik gate baseline prove --test <id> --command \"<suite>\"` "
          "(or `prove-flaky`) to PROVE it — a claim of pre-existence/flakiness with "
          "no A/B-vs-base proof is exactly the crack a real regression walks through.")
    return 1 if strict else 0
