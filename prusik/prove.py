"""`prusik prove` — standalone anti-fabrication gate (no FSM, no init).

The zero-ceremony form of prusik's crown jewel: run a test/lint/type command,
and prove from the tool's OWN output that it actually ran clean — not from the
agent's say-so. Works in any repo, any CI, with no `prusik init`, no sprint,
no phase, no manifest.

    prusik prove -- pytest -q
    prusik prove --kind types -- mypy src/
    prusik prove --min 5 --json -- pytest tests/unit

Exit code reflects the truth: 0 only when PROVEN (command exited 0 AND real
work was observed); 1 when unproven (failed, or exit 0 with nothing executed —
the canonical "tests pass ✅ but nothing ran" false-clean); 2 on usage error.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys

from prusik import evidence

_TIMEOUT_SEC = 1800  # test suites legitimately run long


def run(command: list[str] | None, kind: str = "tests",
        min_executed: int = 1, json_output: bool = False) -> int:
    cmd = list(command or [])
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("[prusik-prove] no command given after `--` "
              "(e.g. `prusik prove -- pytest -q`)", file=sys.stderr)
        return 2
    # Re-serialize argv to a shell string WITHOUT losing the caller's quoting
    # (plain " ".join would mangle `pytest -k "a b"` or nested `bash -c "..."`).
    # bash -c is kept so shell features (pipes, &&, redirects) work.
    cmd_str = shlex.join(cmd)

    try:
        proc = subprocess.run(["/bin/bash", "-c", cmd_str],
                              capture_output=True, text=True,
                              timeout=_TIMEOUT_SEC, check=False)
        exit_code = proc.returncode
        combined = (proc.stdout or "") + "\n--- stderr ---\n" + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        exit_code = -2
        combined = f"[prusik-prove] command exceeded {_TIMEOUT_SEC}s\n"
    except OSError as e:
        exit_code = -3
        combined = f"[prusik-prove] failed to spawn: {e}\n"

    # Stream the real output through, unaltered, so the human sees the truth.
    # In --json mode it goes to stderr so stdout stays pure JSON (parseable by
    # CI / `prusik ci-comment`); still visible in logs.
    sink = sys.stderr if json_output else sys.stdout
    sink.write(combined)
    if not combined.endswith("\n"):
        sink.write("\n")

    executed = evidence.executed_count(kind, combined, cmd_str)
    proven, reason = evidence.prove_verdict(kind, exit_code, executed, min_executed)

    # v0.80.0 — record the proof so it's measurable and gate-checkable (e.g. a
    # builder's full-suite proof at building exit; field finding #14). ONLY inside
    # an initialized project — a standalone prove in a bare dir stays stateless
    # (prove's zero-ceremony invariant: it must not create `.sprint/`).
    try:
        from prusik import ledger
        root = ledger.project_root()
        if (root / ".sprint").exists():
            ledger.append("prove_run", kind=kind, exit_code=exit_code,
                          executed=executed, proven=proven)
            if proven and kind == "tests":
                from prusik import suite_baseline
                suite_baseline.update(root, executed)   # learn the full-suite size
    except Exception:  # noqa: BLE001 — telemetry must never fail the proof
        pass

    if json_output:
        print(json.dumps({
            "command": cmd_str,
            "kind": kind,
            "exit_code": exit_code,
            "executed": executed,
            "min_executed": min_executed,
            "proven": proven,
            "reason": reason,
        }, indent=2))
    else:
        mark = "✓ PROVEN" if proven else "✗ NOT PROVEN"
        print(f"\n[prusik-prove] {mark} — {reason}")
        if not proven and kind == "tests" and exit_code == 0 and executed == 0:
            print("            (exit 0 alone does not prove tests ran — "
                  "this is exactly the fabrication prove exists to catch.)")
        if not proven and "FULL TURBO" in combined:
            print("            (turbo replayed from cache — the tool did not "
                  "actually run, so there is no tool output to read. Re-run "
                  "with --force / no cache for a real verdict. prove will not "
                  "parse turbo's banner as evidence — that would be turbo's "
                  "word, not the tool's execution.)")

    return 0 if proven else 1
