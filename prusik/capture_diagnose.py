"""Capture-result classifier — the ONE extensible surface answering "is this
capture real execution evidence, or a non-evidence artifact?".

WHY THIS EXISTS. The single largest recurring adopter-finding cluster was all one
shape: `prusik gate capture` ran a command, but the result wasn't trustworthy
execution evidence because of some invocation/environment context the agent didn't
account for — the tool wasn't on PATH and never ran (exit 127, fb-53f161606abc);
a turbo cache replay re-emitted a cached verdict with 0 executed (fb-b587d8d9b71c). Each was patched as a NEW inline branch in `gate.capture()`, so the
surface kept growing and the next context was always another scattered special-case.
That recurrence — not any single bug — is what this module is built to stop.

It makes the non-evidence failure modes ONE inspectable, registered list. `capture()`
runs every detector over the result; the FIRST match refuses to record (the tool's
word isn't evidence) and prints the remedy. Fail-closed by construction: a result
becomes evidence only if NO detector claims it. Each refusal is also recorded to the
ledger (`capture_non_evidence`, with the stable mode name), so recurrence is MEASURABLE
per project — fuel for the cross-run calibration loop rather than another silent retry.

THE MAINTENANCE CONTRACT — registering a new non-evidence mode is now bounded:
  1. write a detector `_my_mode(r: CaptureResult) -> NonEvidence | None`
  2. append it to `_DETECTORS`
  3. add its name to `KNOWN_MODES` (the completeness test pins detector↔name parity)
  4. add a unit test feeding a synthetic `CaptureResult`
No edits to the 100-line `capture()` body; no new scattered branch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CaptureResult:
    """The observable outcome of one capture run, fed to every detector."""
    kind: str          # tests | types | lint
    exit_code: int     # the command's own exit code
    value: int         # parsed execution primitive (passed+failed / files checked)
    output: str        # combined stdout+stderr
    command: str       # the reconstructed shell line


@dataclass(frozen=True)
class NonEvidence:
    """A verdict that the capture is NOT execution evidence."""
    mode: str          # stable name (KNOWN_MODES); also the ledger label
    remedy: str        # human diagnosis + the one-line fix
    exit_code: int     # what `prusik gate capture` should exit (fail-closed, ≠0)


# turbo prints `>>> FULL TURBO` when every task is a cache hit, or per-task
# `cache hit, replaying logs` / `cache hit, suppressing logs` — in all of these the
# underlying tool did NOT execute; turbo re-emits or elides the cached output.
_TURBO_CACHE_REPLAY_RE = re.compile(
    r">>>\s*FULL TURBO|cache hit, (?:replaying|suppressing)", re.IGNORECASE)


def _is_turbo_cache_replay(text: str) -> bool:
    return bool(_TURBO_CACHE_REPLAY_RE.search(text))


def _command_not_found(r: CaptureResult) -> NonEvidence | None:
    # bash exit 127 = the tool never ran. Real test/lint runners exit 0-5, never 127,
    # so the signal is precise (fb-53f161606abc).
    if r.exit_code != 127:
        return None
    return NonEvidence(
        "command_not_found",
        "command NOT FOUND (exit 127) — it never ran, so this is not execution "
        "evidence and no entry was recorded. The capture shell is non-interactive; a "
        "toolchain installed via nvm/volta/fnm may not be on its PATH (prusik already "
        "enriches PATH from your login shell — if that didn't resolve it, the tool isn't "
        "on the login PATH either). Fix: use an absolute path to the tool, or put it on "
        "PATH, then re-run `prusik gate capture`.",
        127,
    )


def _cache_replay(r: CaptureResult) -> NonEvidence | None:
    # A turbo cache replay re-emits a prior verdict WITHOUT running the tool; when the
    # cached per-task logs are elided the parsed count is 0. Recording tests=0 would
    # false-block the advance gate as "nothing ran" (fb-b587d8d9b71c). value>0 means
    # the replayed logs carried a real count (the tool's own number, bound to the current
    # worktree-hash) — that stands; only the elided-log 0 is refused. Fail closed: turbo's
    # banner is never silently accepted as a pass.
    if not (r.value <= 0 and r.exit_code == 0 and _is_turbo_cache_replay(r.output)):
        return None
    return NonEvidence(
        "cache_replay",
        f"turbo replayed {r.kind} from cache (`>>> FULL TURBO` / `cache hit`) — the tool "
        f"did NOT actually run, so this is not execution evidence and no entry was "
        f"recorded. A cache replay can read as 0-executed even though the cached run was "
        f"green; recording it would false-block the advance gate as 'nothing ran'. Re-run "
        f"with the cache disabled so the tool truly executes — `turbo run <task> --force` "
        f"(or `--no-cache`), or invoke the runner directly (vitest / eslint / pytest) on "
        f"the changed scope — then re-capture.",
        1,
    )


# The registered non-evidence modes, in match order. Order matters only when two could
# match the same result; today they're disjoint. APPEND new detectors here.
_DETECTORS = (
    _command_not_found,
    _cache_replay,
)

# Stable names of every registered mode — the completeness test pins this to _DETECTORS
# so a detector can't be added without a name (and therefore without observability + a
# documented contract), nor a name orphaned.
KNOWN_MODES = (
    "command_not_found",
    "cache_replay",
)


def diagnose(result: CaptureResult) -> NonEvidence | None:
    """First non-evidence verdict for `result`, or None if it IS execution evidence."""
    for detector in _DETECTORS:
        verdict = detector(result)
        if verdict is not None:
            return verdict
    return None
