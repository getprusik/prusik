"""Known-failure baselines (v0.73.0, an adopter enabler #4).

The execution-evidence gate requires a clean suite, but a real suite carries 1-2
genuinely pre-existing flakes — so a sprint that is green on its OWN work stalls
on inherited debt, and the operator hand-deselects the flake every time. This
lets a PROVEN pre-existing failure be tolerated, without ever becoming a channel
to launder a NEW failure. Five integrity properties, all enforced here:

  1. git-stash PROVEN  — `prove` stashes the sprint's changes, runs the test on
     HEAD, and records the baseline ONLY if it fails there too. If it PASSES on
     HEAD, the failure is the sprint's — refused, loudly.
  2. dated             — each entry records the date proven + the HEAD sha.
  3. ages out          — entries expire (default 30 days); expired entries are
     no longer tolerated, forcing a re-proof or a real cleanup.
  4. visible           — a JSON store at `.sprint/known-failures.json`, listable;
     `deselect_args` shows exactly what's tolerated.
  5. scoped            — only the EXACT proven tests are tolerated (via pytest
     `--deselect`); any other failure still blocks. New failures never hide.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path

_PROVE_TIMEOUT_SEC = 600
DEFAULT_DAYS = 30

# Wall-clock reads across common stacks. A test that reads the clock can FAIL on base
# at one hour and PASS at another, so a single git-stash run can mislabel a TIME-OF-DAY
# flake as "pre-existing" — and a same-time re-run can't tell them apart (fb-72ad02292a10).
# Best-effort + cross-language; only surfaces a caveat, never blocks.
_CLOCK_RE = re.compile(
    r"\bdatetime\.(?:now|today|utcnow)\b|\bdate\.today\b|\btime\.(?:time|monotonic)\s*\("
    r"|\bperf_counter\s*\(|\bDate\.now\s*\(|\bnew\s+Date\s*\(|\bDateTime\.(?:Now|UtcNow)\b"
    r"|\bmoment\s*\(|\bInstant\.now\b|\bSystem\.currentTimeMillis\b")


def _reads_wall_clock(test: str, root: Path) -> bool:
    """Does the failing test's source read the wall clock? Only when the test id carries
    a readable file path (`path::node`, `path:line`, or `path`); a bare slug can't be
    inspected, so it returns False (no false alarm)."""
    cand = test.split("::", 1)[0].split(":", 1)[0].strip()
    if not cand or ("/" not in cand and "." not in cand):
        return False
    p = root / cand
    if not p.is_file():
        return False
    try:
        return bool(_CLOCK_RE.search(p.read_text(encoding="utf-8", errors="ignore")))
    except OSError:
        return False


def _store(root: Path) -> Path:
    return root / ".sprint" / "known-failures.json"


def load(root: Path) -> list[dict]:
    p = _store(root)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return []
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def save(root: Path, entries: list[dict]) -> None:
    p = _store(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(entries, indent=2) + "\n")


def _expires(e: dict) -> date:
    try:
        return date.fromisoformat(e.get("expires", "1970-01-01"))
    except ValueError:
        return date(1970, 1, 1)


def active(entries: list[dict], today: date) -> list[dict]:
    """Non-expired entries — the only ones tolerated."""
    return [e for e in entries if _expires(e) >= today]


def add_entry(root: Path, test: str, *, proven_sha: str, note: str,
              days: int, today: date, kind: str = "pre-existing") -> dict:
    entries = [e for e in load(root) if e.get("test") != test]   # replace
    e = {"test": test, "kind": kind, "recorded": today.isoformat(),
         "expires": (today + timedelta(days=days)).isoformat(),
         "proven_sha": proven_sha, "note": note}
    entries.append(e)
    save(root, entries)
    return e


def deselect_args(root: Path, today: date) -> list[str]:
    """pytest `--deselect <test>` args for every ACTIVE baseline entry — what the
    sentinel appends so a proven flake doesn't fail the capture (the rest still must)."""
    out: list[str] = []
    for e in active(load(root), today):
        out += ["--deselect", e["test"]]
    return out


def prune(root: Path, today: date) -> int:
    entries = load(root)
    keep = active(entries, today)
    if len(keep) != len(entries):
        save(root, keep)
    return len(entries) - len(keep)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=False)


def prove(root: Path, test: str, command: str, *, days: int = DEFAULT_DAYS,
          today: date | None = None) -> tuple[bool, str]:
    """Stash the sprint's changes, run `command` on HEAD, and baseline `test`
    ONLY if it fails there (proven pre-existing). The integrity core — a failure
    that passes on HEAD is the sprint's and is refused."""
    today = today or date.today()
    if _git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
        return False, "not inside a git work tree — cannot prove pre-existence"
    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    if not _git(root, "status", "--porcelain").stdout.strip():
        return False, ("working tree is clean — HEAD and current are identical, "
                       "so a failure here is not the sprint's to baseline anyway.")
    stash = _git(root, "stash", "push", "-u", "-m", "prusik-baseline-proof")
    if stash.returncode != 0 or "No local changes" in stash.stdout:
        return False, f"git stash failed: {(stash.stdout + stash.stderr).strip()}"
    try:
        proc = subprocess.run(["/bin/bash", "-c", command], cwd=str(root),
                              capture_output=True, text=True,
                              timeout=_PROVE_TIMEOUT_SEC, check=False)
        failed_on_head = proc.returncode != 0
    except (subprocess.TimeoutExpired, OSError) as e:
        failed_on_head = None  # type: ignore[assignment]
        err = str(e)
    finally:
        pop = _git(root, "stash", "pop")
    if pop.returncode != 0:
        return False, (f"CRITICAL: `git stash pop` failed — your changes are in "
                       f"the stash, restore manually: {pop.stderr.strip()}")
    if failed_on_head is None:
        return False, f"could not run the proof command on HEAD: {err}"
    if not failed_on_head:
        return False, ("test PASSED on HEAD (without your changes) — this failure "
                       "is the SPRINT's, not pre-existing. NOT baselined. Fix it.")
    clock = _reads_wall_clock(test, root)
    note = f"stash-proven pre-existing on {head[:12]}"
    if clock:
        note += " · CLOCK-DEPENDENT (possible time-of-day flake)"
    add_entry(root, test, proven_sha=head[:12], days=days, today=today, note=note)
    msg = (f"proven pre-existing on {head[:12]} — baselined "
           f"(expires in {days}d). Tolerated until then; re-prove or fix.")
    if clock:
        msg += (" ⚠ TIME-OF-DAY RISK: this test reads the wall clock, so failing on base "
                "right now does NOT prove the failure is pre-existing — a same-time re-run "
                "can't distinguish it from a flake that passes at another hour. Make it "
                "deterministic (freeze the clock — e.g. freezegun / a fixed date), then "
                "re-prove; or characterise non-determinism with `prusik gate baseline "
                "prove-flaky`.")
    return True, msg


_DEFAULT_FLAKY_RUNS = 5


def prove_flaky(root: Path, test: str, command: str, *,
                runs: int = _DEFAULT_FLAKY_RUNS, days: int = DEFAULT_DAYS,
                today: date | None = None) -> tuple[bool, str]:
    """Demonstrate that `command` is NON-DETERMINISTIC on the CURRENT code — record a
    flaky baseline ONLY if it both PASSES and FAILS across `runs` executions.

    Closes the 'assert flake without proof' crack (fb-b351e5ef9de6): a flake defeats
    the A/B-vs-base `prove` (it can pass or fail on HEAD at random), so agents labelled any
    red 'flake / pre-existing' BY INSPECTION — the exact crack a real regression walks
    through. Flakiness is now SYSTEM-COMPUTED (observed pass+fail on identical code), never
    asserted. Three outcomes:
      - all PASS over N runs → REFUSED (not reproduced flaky; nothing to baseline).
      - all FAIL over N runs → REFUSED (a DETERMINISTIC failure — a real or pre-existing
        regression, NOT a flake; fix it, or A/B-prove pre-existence with `prove`).
      - mixed pass+fail   → PROVEN flaky → baselined (scoped to this test, dated, ages out).
    """
    today = today or date.today()
    if runs < 2:
        return False, "need at least 2 runs to demonstrate non-determinism"
    head = _git(root, "rev-parse", "HEAD").stdout.strip() or "working-tree"
    passed = 0
    for _ in range(runs):
        try:
            proc = subprocess.run(["/bin/bash", "-c", command], cwd=str(root),
                                  capture_output=True, text=True,
                                  timeout=_PROVE_TIMEOUT_SEC, check=False)
        except (subprocess.TimeoutExpired, OSError) as e:
            return False, f"could not run the proof command: {e}"
        if proc.returncode == 0:
            passed += 1
    failed = runs - passed
    if failed == 0:
        return False, (f"all {runs} runs PASSED — not reproduced as flaky, nothing to "
                       f"baseline. A flake is DEMONSTRATED non-determinism, not an "
                       f"assertion.")
    if passed == 0:
        return False, (f"all {runs} runs FAILED — this is a DETERMINISTIC failure, NOT a "
                       f"flake. Either it is a real regression (fix it) or pre-existing "
                       f"(A/B-prove with `prusik gate baseline prove`). A flake must both "
                       f"PASS and FAIL on identical code.")
    add_entry(root, test, proven_sha=head[:12], days=days, today=today, kind="flaky",
              note=f"demonstrated-flaky {passed}P/{failed}F over {runs} runs on {head[:12]}")
    return True, (f"PROVEN flaky — {passed} passed / {failed} failed over {runs} runs on "
                  f"identical code (non-deterministic). Baselined (expires in {days}d), "
                  f"scoped to this test, ages out. The durable fix is a hermetic suite.")


def run(action: str, *, feature: str | None = None, test: str | None = None,
        command: str | None = None, days: int = DEFAULT_DAYS,
        runs: int = _DEFAULT_FLAKY_RUNS, root: Path | None = None) -> int:
    from prusik import ledger
    root = root or ledger.project_root()
    today = date.today()

    if action == "list":
        entries = load(root)
        if not entries:
            print("[baseline] no known-failure baselines.")
            return 0
        act = active(entries, today)
        print(f"[baseline] {len(act)} active / {len(entries)} total "
              f"known-failure(s):")
        for e in entries:
            state = "active" if e in act else "EXPIRED"
            print(f"  [{state}] {e['test']}  (proven {e.get('proven_sha','?')} "
                  f"on {e.get('recorded','?')}, expires {e.get('expires','?')})")
        return 0

    if action == "prune":
        n = prune(root, today)
        print(f"[baseline] pruned {n} expired entr{'y' if n == 1 else 'ies'}.")
        return 0

    if action == "deselect-args":
        args = deselect_args(root, today)
        print(" ".join(args))   # for `$(prusik gate baseline deselect-args)`
        return 0

    if action == "prove":
        if not test or not command:
            print("[baseline] prove needs --test <id> and --command \"<cmd>\".")
            return 2
        ok, msg = prove(root, test, command, days=days, today=today)
        print(f"[baseline] {'PROVEN' if ok else 'REFUSED'}: {msg}")
        ledger.append("known_failure_baseline", feature=feature or "", test=test,
                      proven=ok, action="prove")
        return 0 if ok else 2

    if action == "prove-flaky":
        if not test or not command:
            print("[baseline] prove-flaky needs --test <id> and --command \"<cmd>\" "
                  "(the command that exhibits the flake, e.g. the full suite).")
            return 2
        ok, msg = prove_flaky(root, test, command, runs=runs, days=days, today=today)
        print(f"[baseline] {'PROVEN-FLAKY' if ok else 'REFUSED'}: {msg}")
        ledger.append("known_failure_baseline", feature=feature or "", test=test,
                      proven=ok, action="prove-flaky")
        return 0 if ok else 2

    print(f"[baseline] unknown action: {action}")
    return 2
