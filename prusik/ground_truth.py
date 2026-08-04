"""Phase-entry reality — brief-time facts re-verify against the live world.

Two field failures, one mechanism (fb-4c542a24db7c + fb-8637c2416504): a
security sprint's brief enumerated 7 advisories that were fixed on main by
scoping time (scope derived from stale facts), while nothing noticed the
sprint's goal was already met on base.

Ground truth: a criteria.yaml may record `ground_truth: {command,
output_sha256, exit_code, excerpt, captured_at}` — captured from a REAL run
(`prusik gate ground-truth --feature F --capture`). Sprint-start re-runs the
command; drift BLOCKS, fail-closed on purpose: proceeding derives scope from
wrong reality, the exact field failure. Re-capturing after reconciling the
brief IS the acknowledgment path — one command, in-band.

Base probe: prove_red criteria carry the "must FAIL without the change"
semantic, so ALL of them green on base means the goal is already achieved —
the watchdog files a `criteria_already_met` incident (close-or-rescope).
Plain criteria green on base implies nothing and are never probed. Early
phases only: once building starts, green is progress, not drift.

Both halves are dormant unless the adopter authored the fields — zero new
ceremony otherwise. Output hashing is over the raw combined stream; a
volatile command (timestamps) belongs normalized by the author, not
silently by prusik.
"""

from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path

_TIMEOUT_SEC = 300
_EXCERPT_LINES = 20
_EARLY_PHASES = ("scoping", "triage", "planning")


def _criteria_path(feature: str, root: Path) -> Path:
    return root / "briefs" / f"{feature}.criteria.yaml"


def _run(command: str) -> tuple[int, str]:
    try:
        r = subprocess.run(["/bin/bash", "-c", command], capture_output=True,
                           text=True, timeout=_TIMEOUT_SEC, check=False)
    except subprocess.TimeoutExpired:
        return -2, f"[ground-truth] command exceeded {_TIMEOUT_SEC}s"
    except OSError as e:
        return -3, f"[ground-truth] failed to spawn: {e}"
    return r.returncode, (r.stdout or "") + (("\n--- stderr ---\n" + r.stderr)
                                             if r.stderr else "")


def _load_block(path: Path) -> dict | None:
    import yaml
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return None
    gt = (data or {}).get("ground_truth")
    return gt if isinstance(gt, dict) and gt.get("command") else None


def capture(criteria_path: Path) -> int:
    """Run the ground-truth command and record its reality (sha256 + exit
    code + human excerpt) into the criteria file, format-preserving."""
    gt = _load_block(criteria_path)
    if not gt:
        print("[ground-truth] no `ground_truth.command` in "
              f"{criteria_path} — nothing to capture")
        return 1
    code, out = _run(gt["command"])
    if code in (-2, -3):
        print(out)
        return 1
    from ruamel.yaml import YAML
    y = YAML()
    y.preserve_quotes = True
    data = y.load(criteria_path.read_text())
    blk = data["ground_truth"]
    blk["output_sha256"] = hashlib.sha256(out.encode()).hexdigest()
    blk["exit_code"] = code
    blk["excerpt"] = "\n".join(out.splitlines()[:_EXCERPT_LINES])
    blk["captured_at"] = datetime.now(timezone.utc).isoformat()
    with open(criteria_path, "w") as f:
        y.dump(data, f)
    print(f"[ground-truth] captured: exit {code}, "
          f"{len(out.splitlines())} line(s) → {criteria_path}")
    return 0


def check(criteria_path: Path) -> tuple[bool, str]:
    """Re-run the recorded command against live reality. (ok, message)."""
    gt = _load_block(criteria_path)
    if not gt:
        return True, "no ground truth recorded — check dormant"
    if not gt.get("output_sha256"):
        return False, ("ground_truth.command present but never captured — "
                       "run `prusik gate ground-truth --feature <F> "
                       "--capture` from a real run first")
    code, out = _run(gt["command"])
    digest = hashlib.sha256(out.encode()).hexdigest()
    if digest == gt["output_sha256"] and code == gt.get("exit_code"):
        return True, "ground truth matches live reality"
    head = "\n".join(out.splitlines()[:_EXCERPT_LINES])
    return False, (
        f"ground truth DRIFTED since capture ({gt.get('captured_at', '?')}).\n"
        f"  recorded (exit {gt.get('exit_code')}):\n"
        + "\n".join(f"    {ln}" for ln in
                    str(gt.get('excerpt', '')).splitlines())
        + f"\n  live now (exit {code}):\n"
        + "\n".join(f"    {ln}" for ln in head.splitlines()))


def sprint_start_check(feature: str, root: Path) -> bool:
    """Pre-sprint gate: True = pass/dormant; False = drift (blocks).
    Fail-closed: a drifted brief must be reconciled, then re-captured."""
    path = _criteria_path(feature, root)
    if not path.exists():
        return True
    ok, msg = check(path)
    if ok:
        return True
    from prusik import ledger
    ledger.append("ground_truth_drift", feature=feature,
                  criteria=str(path.relative_to(root)))
    print(f"[prusik-gate] ground-truth BLOCK — {msg}\n"
          f"  The brief's facts no longer match the world; scoping from "
          f"them repeats fb-4c542a24db7c.\n"
          f"  Reconcile the brief, then re-baseline:\n"
          f"    prusik gate ground-truth --feature {feature} --capture")
    return False


def base_probe(feature: str | None, root: Path,
               phase: str | None) -> dict | None:
    """Goal-already-met probe for the watchdog: at early phases, run each
    prove_red criterion's verify_command against base; ALL green ⇒ payload
    for a `criteria_already_met` incident. Any red ⇒ None (work remains)."""
    if not feature or phase not in _EARLY_PHASES:
        return None
    path = _criteria_path(feature, root)
    if not path.exists():
        return None
    from prusik import schema
    try:
        criteria = schema.load_criteria(path)
    except Exception:  # noqa: BLE001 — a broken file is lint's problem, not the probe's
        return None
    targets = [c for c in criteria if c.get("prove_red")]
    if not targets:
        return None
    green: list[str] = []
    for c in targets:
        code, _ = _run(str(c.get("verify_command", "")))
        expected = int(c.get("expected_exit", 0))
        if code != expected:
            return None                       # still red — work remains
        green.append(str(c.get("id")))
    return {"green_criteria": green,
            "hint": ("every prove_red criterion already passes on base — "
                     "the goal may be achieved outside this sprint; close "
                     "or rescope (fb-8637c2416504)")}
