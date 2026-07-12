"""prusik criterion resolve — the defer→resolve complement (field finding #22).

`blocked_external` (#16) deferred a criterion that genuinely needed operator-
provided external setup (a real Stripe key, a third-party sandbox) — but it was a
one-way trapdoor: there was no in-band way to CLOSE it once the dependency exists.
So a deferred criterion could only be resolved by spinning a NEW sprint
(sledgehammer) or an EYEBALL (circumvent). Both are wrong. This is the missing
half: run the deferred criterion's verify_command for real and, if it passes,
record the evidence and flip `blocked_external` → false. #17's "sanctioned in-band
escape," made specific — and it makes "I validated it" into harness evidence.

Guarded (the integrity-critical part): `resolve` closes ONLY a criterion that is
currently `blocked_external: true`. It is NOT a backdoor to green an arbitrary
criterion — those are still verified through reviewing. Sprint-state independent:
the criterion belongs to the feature (brief + criteria + ledger), not a live
sprint, so it works after the sprint has completed (the common case).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from prusik import ledger, schema

_TIMEOUT_SEC = 600


def _criteria_path(feature: str, root: Path) -> Path:
    return root / "briefs" / f"{feature}.criteria.yaml"


def _clear_blocked_external(path: Path, cid: str) -> bool:
    """Flip `blocked_external: true → false` for the criterion, preserving the
    file's formatting (ruamel round-trip). Returns True if a change was written."""
    from io import StringIO

    from ruamel.yaml import YAML
    y = YAML()
    try:
        data = y.load(path.read_text())
    except Exception:  # noqa: BLE001 — unparseable → don't touch the file
        return False
    for c in (data.get("criteria") or []):
        if c.get("id") == cid:
            c["blocked_external"] = False
            buf = StringIO()
            y.dump(data, buf)
            path.write_text(buf.getvalue())
            return True
    return False


def resolve(feature: str, criterion_id: str, root: Path | None = None,
            strict: bool = False) -> int:
    root = root or ledger.project_root()
    path = _criteria_path(feature, root)
    if not path.exists():
        print(f"[prusik-criterion] no criteria file at "
              f"briefs/{feature}.criteria.yaml", file=sys.stderr)
        return 2
    target = next((c for c in schema.load_criteria(path)
                   if c.get("id") == criterion_id), None)
    if target is None:
        print(f"[prusik-criterion] criterion {criterion_id!r} not found in "
              f"{feature}.", file=sys.stderr)
        return 2
    # GUARD: resolve closes ONLY a DEFERRED criterion — never a backdoor.
    if not target.get("blocked_external"):
        print(f"[prusik-criterion] criterion {criterion_id!r} is not "
              f"blocked_external — `resolve` only closes a DEFERRED criterion. A "
              f"non-deferred criterion is verified through reviewing, not here.",
              file=sys.stderr)
        return 2
    vc = str(target.get("verify_command", "")).strip()
    expected = int(target.get("expected_exit", 0))
    if not vc:
        print(f"[prusik-criterion] criterion {criterion_id!r} has no "
              f"verify_command to run.", file=sys.stderr)
        return 2

    # Run the real command — same rigor as the reviewing evidence-gate.
    try:
        proc = subprocess.run(["/bin/bash", "-c", vc], cwd=str(root),
                              capture_output=True, text=True,
                              timeout=_TIMEOUT_SEC, check=False)
        exit_code = proc.returncode
        combined = (proc.stdout or "") + "\n--- stderr ---\n" + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        exit_code = -2
        combined = f"[prusik-criterion] verify_command exceeded {_TIMEOUT_SEC}s\n"
    except OSError as e:
        exit_code = -3
        combined = f"[prusik-criterion] verify_command failed to spawn: {e}\n"
    sys.stdout.write(combined)

    if exit_code == expected:
        cleared = _clear_blocked_external(path, criterion_id)
        ledger.append("success_criterion_verified", feature=feature,
                      id=criterion_id, passed=True, exit_code=exit_code,
                      verify_command=vc, expected_exit=expected,
                      resolution="from_blocked_external")
        print(f"\n[prusik-criterion] ✓ RESOLVED — {criterion_id!r} verified "
              f"(exit {exit_code}); blocked_external "
              f"{'cleared in criteria.yaml' if cleared else 'recorded clear'}. "
              f"The sprint-complete deferral is retroactively closed — re-run this "
              f"command to replay the proof.")
        return 0
    ledger.append("success_criterion_verified", feature=feature, id=criterion_id,
                  passed=False, exit_code=exit_code, verify_command=vc,
                  expected_exit=expected, resolution="from_blocked_external_unmet")
    print(f"\n[prusik-criterion] ✗ NOT RESOLVED — {criterion_id!r} exited "
          f"{exit_code}, expected {expected}. The external setup didn't make it "
          f"pass; it stays deferred (verify and re-run when it's truly ready).")
    return 1 if strict else 0
