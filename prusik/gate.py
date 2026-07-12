"""Hook + CLI gate entry points.

Invoked by Claude Code hooks (pre-tool / post-tool / stop / session-start) and
by slash commands (advance / brief / scope / sprint-start). Produces the right
stdout JSON / exit code to match the Claude Code hook contract.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import sys
from pathlib import Path

from prusik import consistency, discovery, ledger, phases, schema


def _read_stdin_json() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}


def pre_tool(args=None) -> int:
    # The platform adapter owns native I/O (parse the host's tool-call event + express a
    # deny); the gate's policy below is host-agnostic — it sees only a neutral ToolEvent
    # (roadmap Horizon-2 E). Claude Code is the only shipped adapter.
    from prusik.platform_adapter import get_adapter
    adapter = get_adapter()
    event = adapter.parse_event(adapter.read_payload())
    if event is None:
        return 0

    config = phases.load_sprint_config()
    if not config:
        return 0  # prusik not in this repo; don't interfere

    state = phases.current_sprint_state()
    if not state:
        return 0  # no active sprint

    return _gate_tool_event(event, config, state["phase"], state.get("feature"), adapter)


def _gate_tool_event(event, config: dict, phase: str, feature, adapter) -> int:
    """Host-agnostic gate policy over a neutral ToolEvent. Returns the adapter's deny exit
    code if the event is blocked, else 0. Identical decisions/ledger records/messages as
    before the v0.22 adapter seam — only the I/O (event parsing + deny) is now adapter-owned."""
    for target in event.file_targets:
        ok, reason = phases.is_path_writable(target, config, phase, feature)
        if not ok:
            # redirect_rel records where the write should have gone (the worktree) —
            # informative context for the catch-quality ledger, which credits build-phase
            # blocks as true catches when the feature later advances (derived from the
            # ledger, not here).
            rel = _worktree_redirect_rel(target, config, phase, feature)
            ledger.append("gate_blocked", tool=event.tool, target=target,
                          phase=phase, feature=feature, reason=reason,
                          redirect_rel=rel)
            hint = _worktree_redirect_hint(target, config, phase, feature)
            msg = f"[prusik-gate] phase '{phase}' blocks write to {target}: {reason}"
            if hint:
                msg += f"\n  → {hint}"
            return adapter.deny(msg)

    if event.command is not None:
        cmd = event.command
        executable = _strip_heredocs(cmd)
        phase_spec = phases.get_phase_spec(config, phase) or {}

        # v0.3.8: Bash redirects also write files, and were bypassing the
        # writable-path check entirely (that check only covered Write/Edit/
        # NotebookEdit). Scan redirect targets and apply is_path_writable
        # per target.
        for target in _bash_redirect_targets(executable):
            ok, reason = phases.is_path_writable(target, config, phase, feature)
            if not ok:
                ledger.append("gate_blocked", tool=event.tool, command=cmd,
                              phase=phase, feature=feature,
                              reason=f"bash redirect to unwriteable path: {target} ({reason})")
                hint = _worktree_redirect_hint(target, config, phase, feature)
                msg = f"[prusik-gate] phase '{phase}' blocks bash redirect to {target}: {reason}"
                if hint:
                    msg += f"\n  → {hint}"
                return adapter.deny(msg)

        # Preferred: deny_commands — token-based match. Immune to prose
        # inside heredocs or strings because we check command-position only.
        for denied in phase_spec.get("deny_commands", []):
            if _command_denied(executable, denied):
                ledger.append("gate_blocked", tool=event.tool, command=cmd,
                              phase=phase, feature=feature,
                              reason=f"deny command: {denied}")
                return adapter.deny(f"[prusik-gate] phase '{phase}' blocks command: {denied!r}")

        # Legacy: deny_bash — raw regex. Kept for back-compat with configs
        # authored before v0.3.5.
        for pattern in phase_spec.get("deny_bash", []):
            if re.search(pattern, executable):
                ledger.append("gate_blocked", tool=event.tool, command=cmd,
                              phase=phase, feature=feature, reason=f"deny pattern: {pattern}")
                return adapter.deny(f"[prusik-gate] phase '{phase}' blocks bash pattern /{pattern}/")

    return 0


# v0.8.9: When a deny fires on a path the agent COULD have written to had
# they targeted the worktree mirror instead, surface the redirect path in
# the deny message. Live-cc reported a 6.5h stall on m4-s2d (May 9 01:34)
# because the deny named the constraint but not the route around it. This
# converts the deny from "you can't write here" to "you can't write here,
# try worktrees/<role>/<path> instead."
def _worktree_role_pattern(config: dict, phase: str,
                            feature: str | None) -> str | None:
    """The most-specific `worktrees/<role>/**` writable pattern for this phase
    (concrete role beats a '*' wildcard), or None if the phase permits no
    worktree redirect (e.g. integrating)."""
    concrete: str | None = None
    wildcard: str | None = None
    for pat in phases.writable_patterns(config, phase, feature) or []:
        if not pat.startswith("worktrees/"):
            continue
        segs = pat.split("/", 2)
        if len(segs) < 2:
            continue
        if segs[1] == "*":
            wildcard = pat
        else:
            return pat                      # specific match wins
    return concrete or wildcard


def _worktree_redirect_rel(target: str, config: dict, phase: str,
                           feature: str | None) -> str | None:
    """Project-relative path a denied write should be redirected to inside a
    worktree (the rel after `worktrees/<role>/`), or None when no redirect
    applies. Machine counterpart of _worktree_redirect_hint; also the pending-
    block key used by catch_quality outcome capture."""
    if _worktree_role_pattern(config, phase, feature) is None:
        return None
    from pathlib import Path as _Path
    try:
        root = ledger.project_root()
        t = _Path(target)
        if t.is_absolute():
            try:
                rel = str(t.resolve().relative_to(root.resolve()))
            except ValueError:
                rel = target
        else:
            rel = target
    except Exception:
        rel = target
    if rel.startswith("worktrees/"):
        return None                          # already a worktree-shaped path
    return rel


def _worktree_redirect_hint(target: str, config: dict, phase: str,
                             feature: str | None) -> str | None:
    """Human-readable redirect-to-worktree hint for a denied path (None when
    the phase permits no worktree redirect, e.g. integrating)."""
    rel = _worktree_redirect_rel(target, config, phase, feature)
    if rel is None:
        return None
    chosen = _worktree_role_pattern(config, phase, feature)
    if not chosen:
        return None
    role_seg = chosen.split("/", 2)[1]
    if role_seg == "*":
        return (f"Try writing to worktrees/<your-role>/{rel} instead "
                f"(substitute your builder role for <your-role>).")
    return f"Try writing to worktrees/{role_seg}/{rel} instead ({phase} phase)."


# Bash shell redirects that WRITE to a target file.
# Matches: >file  >>file  &>file  2>file  2>>file  1>file
# Targets can be quoted or bare. We deliberately skip /dev/null etc.
_REDIRECT_RE = re.compile(
    r"""(?:^|[\s;&|(])(?:[0-9&]?>>?|&>)\s*("[^"]+"|'[^']+'|[^\s;&|<>()`]+)""",
    re.MULTILINE,
)
_TEE_RE = re.compile(
    r"""\btee\s+(?:-a\s+)?("[^"]+"|'[^']+'|[^\s;&|<>()`]+)""",
)
_SKIP_TARGETS = {"/dev/null", "/dev/stderr", "/dev/stdout",
                 "/dev/tty", "/dev/zero"}


def _bash_redirect_targets(cmd: str) -> list[str]:
    """Extract file paths that a bash command writes to via redirect or tee.

    Called on heredoc-stripped input so we don't pick up redirects that
    were only in heredoc prose.

    v0.3.11: also strips `-c "..."` / `-c '...'` payloads BEFORE scanning,
    because inline Python/bash payloads contain `>`, `>=`, `>>` (as
    comparison operators, regex tokens, etc.) that are NOT shell redirects
    but that the scanner was mistaking for them. After stripping, a
    path-likeness filter rejects remaining tokens that start with `=` or
    `:` or don't look file-shaped, catching any false-positives that
    survive.
    """
    cleaned = _strip_c_payloads(cmd)
    targets: list[str] = []
    for m in _REDIRECT_RE.finditer(cleaned):
        tok = m.group(1).strip("\"'")
        if tok and tok not in _SKIP_TARGETS and _looks_like_file_target(tok):
            targets.append(tok)
    for m in _TEE_RE.finditer(cleaned):
        tok = m.group(1).strip("\"'")
        if tok and tok not in _SKIP_TARGETS and _looks_like_file_target(tok):
            targets.append(tok)
    return targets


# Strip `python -c "<program>"` / `bash -c '<script>'` / `sh -c "..."` etc.
# The embedded program is not shell — it won't be parsed for redirects.
_C_PAYLOAD_RE = re.compile(
    r"""(?:python[23]?|bash|sh|zsh|ksh|uv\s+run\s+python[23]?|uv\s+run)\s+
        (?:[^'"\s<>;&|()`]+\s+)*           # any flags before -c
        -c\s+
        (?:"[^"]*"|'[^']*')                # the payload itself (quoted)
    """,
    re.VERBOSE,
)


def _strip_c_payloads(cmd: str) -> str:
    """Remove `<interpreter> -c "..."` payloads so their contents don't
    get scanned for shell redirects."""
    return _C_PAYLOAD_RE.sub("", cmd)


def _looks_like_file_target(tok: str) -> bool:
    """Reject tokens that clearly aren't file paths.

    A legitimate redirect target is path-shaped: starts with a path
    marker (/, ., ~), contains a slash, or is a filename-ish bare word.
    Python comparison operators and code fragments (`=30`, `:foo`,
    `(x`, etc.) don't match any of these.
    """
    if not tok:
        return False
    tok = tok.strip("\"'")
    if not tok:
        return False
    # Common false-positives from Python payloads we failed to strip
    if tok[0] in "=:(){}[]":
        return False
    # Definite path shapes
    if tok[0] in "/.~":
        return True
    if "/" in tok:
        return True
    # Has a file extension — accept (handles quoted paths with spaces)
    if re.search(r"\.[a-zA-Z0-9]+$", tok):
        return True
    # Bare word without extension (e.g. `tee logfile`) — conservative accept
    if re.match(r"^[a-zA-Z_][\w\-]*$", tok):
        return True
    return False


def _command_denied(cmd: str, denied: str) -> bool:
    """True if any shell statement in cmd begins with the denied command tokens.

    Tokenizes cmd into statements (split on ; && || | \\n) and matches the
    denied tokens as a prefix of each statement's word list. Safe against
    prose inside heredocs (already stripped) or inside quoted strings
    (those end up as single tokens that don't match naked word prefixes).

    Examples, with denied = "git push":
      "git push origin main"      → True
      "echo 'git push'"           → False (echo is command, 'git push' is a quoted arg)
      "git status && git push"    → True (second statement matches)
      "git   push main"           → True (multiple spaces collapse in split)
    """
    denied_tokens = denied.split()
    if not denied_tokens:
        return False
    # Shell statement separators
    statements = re.split(r"(?:;|&&|\|\||\||\n)", cmd)
    for stmt in statements:
        tokens = stmt.split()
        if len(tokens) >= len(denied_tokens) and tokens[:len(denied_tokens)] == denied_tokens:
            return True
    return False


def is_command_denied(cmd: str, phase_spec: dict) -> bool:
    """Public: True if `cmd` invokes any of `phase_spec`'s deny_commands.
    The phase-level deny decision (used by the gate's Bash path). Composed by
    the injection harness to verify the deny-commands gate on a given config."""
    return any(_command_denied(cmd, d) for d in phase_spec.get("deny_commands", []))


_HEREDOC_RE = re.compile(
    # Match `<<[-]?[']DELIM[']` then any chars, then ^DELIM$ on its own line.
    r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n.*?^\s*\1\s*$",
    re.MULTILINE | re.DOTALL,
)


def _strip_heredocs(cmd: str) -> str:
    """Remove heredoc bodies from a shell command before pattern matching.

    Heredoc bodies are shell *data*, not *commands* — the shell does not
    execute words inside them. Prose like "discuss git merge semantics"
    appearing in a heredoc (e.g. when appending to a log file via
    `cat <<EOF`) must not trip deny_bash patterns.
    """
    return _HEREDOC_RE.sub("", cmd)


def stop(args=None) -> int:
    payload = _read_stdin_json()
    if payload.get("stop_hook_active"):
        return 0  # avoid infinite loop

    config = phases.load_sprint_config()
    if not config:
        return 0
    state = phases.current_sprint_state()
    if not state:
        return 0

    # v0.3.8: honor `prusik pause` marker. During deliberate pauses,
    # skip exit-artifact enforcement so the operator isn't blocked
    # from ending the turn while fixing prusik issues mid-phase.
    from prusik import pause as _pause
    if _pause.is_paused():
        return 0

    phase = state["phase"]
    feature = state.get("feature")
    phase_spec = phases.get_phase_spec(config, phase) or {}
    missing = _unsatisfied_exit_artifacts(phase_spec, feature)
    if missing:
        reason = (f"[prusik-gate] Phase '{phase}' has unsatisfied exit artifacts:\n  - "
                  + "\n  - ".join(missing)
                  + "\nProduce the artifact(s) or run /sprint-advance if you mean to move on.")
        # Stop hook: exit 2 + JSON on stdout per hook spec to block session end
        print(json.dumps({"decision": "block", "reason": reason}))
        return 2
    return 0


# ============================================================
# v0.8.11 — Convergence-stall detector (PostToolUse hook)
#
# Driven by m4-h2 integrator stalling 38+ min on 4 identical regression-gate
# runs (23 failures each) with NO operator signal — parent token counter
# frozen at Agent dispatch, subagent burning invisible budget. Prusik had
# no detector for "agent retried same command, got same result, retrying
# again."
#
# This hook fingerprints Bash output per command-shape, keeps a small ring
# buffer in .sprint/convergence-watch.json, and on N=3 consecutive identical
# fingerprints emits a `convergence_stall` ledger event AND injects an
# additionalContext systemMessage into the next agent turn naming the stall.
# Role-spec extensions in integrator + regression-sentinel teach the response
# pattern: on observing the message, STOP and emit FAIL — do not retry.
#
# Mechanical signal + cognitive response = recursive critic-actor pattern at
# the subagent boundary. Prusik cannot reach inside an Agent dispatch to
# break the loop, but it can make the loop visible.

_CONVERGENCE_RING_SIZE = 3
_CONVERGENCE_WATCH_FILE = "convergence-watch.json"
_CONVERGENCE_OUTPUT_SAMPLE_BYTES = 8000  # cap hashing cost
_CONVERGENCE_SKIP_COMMANDS = {
    "ls", "pwd", "cd", "echo", "cat", "true", "false", "date", "whoami",
}


def _convergence_watch_path() -> Path:
    return ledger.project_root() / ".sprint" / _CONVERGENCE_WATCH_FILE


def _load_convergence_state() -> dict:
    p = _convergence_watch_path()
    if not p.exists():
        return {"watches": {}}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {"watches": {}}


def _save_convergence_state(state: dict) -> None:
    p = _convergence_watch_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2))


def _normalize_command_shape(cmd: str) -> str:
    """Reduce a bash command to a stable shape-key for grouping retries.

    Strips leading `cd "..." &&`, env-var assignments, and pipe-tail noise.
    Truncates to a stable prefix. Two invocations differing only in
    timestamped output paths or temp dirs map to the same shape.
    """
    # Strip leading cd <dir> &&
    cmd = re.sub(r'^\s*cd\s+(?:"[^"]*"|\'[^\']*\'|\S+)\s*&&\s*', '', cmd)
    # Strip leading FOO=bar BAZ=qux env-var assignments
    cmd = re.sub(r'^\s*(?:[A-Z_][A-Z0-9_]*=\S+\s+)+', '', cmd)
    # Strip date-substitution patterns like $(date +%H%M)
    cmd = re.sub(r'\$\(date[^)]*\)', '<TS>', cmd)
    # Strip timestamp-looking numerics in flags
    cmd = re.sub(r'-\d{8,}', '-<TS>', cmd)
    # Truncate to a stable shape
    return cmd.strip()[:160]


def _command_token(cmd: str) -> str:
    """Extract the program name (first non-env, non-cd token) for skip-list checks."""
    shape = _normalize_command_shape(cmd)
    m = re.match(r'^\s*(\S+)', shape)
    return m.group(1) if m else ""


def _fingerprint_output(output: str) -> str:
    """Stable hash of normalized tool output.

    Normalizes wall-clock numerics (test durations, dates, ISO timestamps)
    so that two structurally-identical runs with different runtimes
    fingerprint identically. Caps input size to keep hashing cheap.
    """
    if not output:
        return "EMPTY"
    sample = output[:_CONVERGENCE_OUTPUT_SAMPLE_BYTES]
    # Normalize ANSI escape sequences
    sample = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', sample)
    # Normalize pytest duration: "in 220.77s (0:03:40)" → "in <DUR>"
    sample = re.sub(r'in\s+\d+\.\d+s\s*\(\d+:\d+:\d+\)', 'in <DUR>', sample)
    # Normalize bare floating durations
    sample = re.sub(r'\b\d+\.\d{2,}s\b', '<S>', sample)
    # Normalize ISO timestamps
    sample = re.sub(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:?\d{2}|Z)?',
                    '<TS>', sample)
    # Normalize HH:MM:SS clock readings
    sample = re.sub(r'\b\d{2}:\d{2}:\d{2}\b', '<TIME>', sample)
    # Normalize tempdir paths
    sample = re.sub(r'/tmp/[^\s:]+', '/tmp/<X>', sample)
    return hashlib.sha256(sample.encode("utf-8", errors="replace")).hexdigest()[:16]


def _bash_succeeded(payload: dict, tool_response) -> bool | None:
    """Best-effort: did the Bash command SUCCEED? True / False, or None when no
    reliable signal is present. The convergence-stall detector exists for a stuck
    agent RETRYING A FAILING command (the m4-h2 case: 23 failures each); a SUCCESSFUL
    command repeated identically — e.g. an idempotent `alembic upgrade head` that's a
    no-op once at head — is NOT a stall (fb-876ad6010f72). Claude Code exposes a
    top-level `tool_response_success` boolean (there is no exit code in the payload);
    we probe a few fallback fields too so a field-name/version difference degrades
    GRACEFULLY (None → caller keeps prior behavior) instead of misfiring."""
    if isinstance(payload, dict) and isinstance(
            payload.get("tool_response_success"), bool):
        return payload["tool_response_success"]
    if isinstance(tool_response, dict):
        if isinstance(tool_response.get("success"), bool):
            return tool_response["success"]
        for key in ("exit_code", "returncode", "exitCode", "code"):
            if key in tool_response:
                try:
                    return int(tool_response[key]) == 0
                except (TypeError, ValueError):
                    pass
        for key in ("is_error", "isError"):
            if isinstance(tool_response.get(key), bool):
                return not tool_response[key]
    return None


def post_tool(args=None) -> int:
    """PostToolUse hook — convergence-stall detector for Bash retries.

    Returns additionalContext via hookSpecificOutput when N=3 consecutive
    identical fingerprints for the same command-shape are observed within
    the current sprint. The agent's next turn sees the systemMessage; role
    specs teach the response pattern (FAIL out, do not retry).
    """
    payload = _read_stdin_json()
    tool_name = payload.get("tool_name", "")
    if tool_name != "Bash":
        return 0

    config = phases.load_sprint_config()
    if not config:
        return 0
    state = phases.current_sprint_state()
    if not state:
        return 0

    tool_input = payload.get("tool_input", {})
    tool_response = payload.get("tool_response", {})
    cmd = tool_input.get("command", "")
    if not cmd.strip():
        return 0

    token = _command_token(cmd)
    if token in _CONVERGENCE_SKIP_COMMANDS:
        return 0

    shape = _normalize_command_shape(cmd)

    # CONSECUTIVE-ONLY (fb-427808ba40dd): a stuck retry is the SAME command run
    # BACK-TO-BACK with no progress between. The old ring accumulated identical outputs
    # for a shape across the WHOLE session, so a command legitimately re-run at every
    # gate across sprints (e.g. a clean `contracts:check` ~10×) tripped the guard even
    # though every run PASSED and real work happened between them. Now: ANY different
    # Bash command since this shape's last run means the agent made progress → reset the
    # shape's ring; only a genuinely consecutive same-shape repeat accumulates. Tracked
    # BEFORE the trivial-output early-return so even a short intervening command counts
    # as work. Payload-independent — the Bash hook carries NO success/exit signal to key
    # on (the v0.135.0 `tool_response_success` reset was inert; confirmed by fb-427808ba40dd).
    wstate = _load_convergence_state()
    prev_shape = wstate.get("last_shape")
    wstate["last_shape"] = shape

    output = ""
    if isinstance(tool_response, dict):
        output = (str(tool_response.get("stdout") or "")
                  + str(tool_response.get("stderr") or ""))
    elif isinstance(tool_response, str):
        output = tool_response
    if len(output.strip()) < 80:
        _save_convergence_state(wstate)   # persist the activity, then skip trivial output
        return 0

    fp = _fingerprint_output(output)
    watches = wstate.setdefault("watches", {})
    entry = watches.setdefault(shape, {"fingerprints": [], "feature": state.get("feature")})
    if prev_shape != shape and entry["fingerprints"]:
        entry["fingerprints"] = []           # intervening work broke the retry loop
    # Best-effort success reset, kept as defense-in-depth IF Claude Code ever surfaces a
    # success signal to the hook (it does not today) — harmless no-op when absent.
    if _bash_succeeded(payload, tool_response) is True:
        if entry["fingerprints"]:
            entry["fingerprints"] = []
        _save_convergence_state(wstate)
        return 0

    fps: list[str] = entry["fingerprints"]
    fps.append(fp)
    if len(fps) > _CONVERGENCE_RING_SIZE:
        fps[:] = fps[-_CONVERGENCE_RING_SIZE:]
    entry["feature"] = state.get("feature")
    _save_convergence_state(wstate)

    if len(fps) == _CONVERGENCE_RING_SIZE and len(set(fps)) == 1:
        ledger.append(
            "convergence_stall",
            # kind discriminates the two convergence-stall facets that share
            # this event: tool-level (identical Bash outputs, this v0.8.11
            # soft-warn) vs phase-level (rewind thrash, the v0.49.0 hard-stop).
            kind="bash_output_repeat",
            command_shape=shape,
            fingerprint=fp,
            n=_CONVERGENCE_RING_SIZE,
            phase=state["phase"],
            feature=state.get("feature"),
        )
        # Reset the ring so we don't re-signal on every subsequent
        # identical run — operator/agent has been notified; further loops
        # are their problem to break.
        entry["fingerprints"] = []
        _save_convergence_state(wstate)

        msg = (
            f"[prusik-convergence-stall] {_CONVERGENCE_RING_SIZE} consecutive "
            f"identical results for command shape '{shape[:80]}'. The inner "
            f"loop is not converging. STOP retrying. If you are a reviewer, "
            f"emit FAIL with the observed cascade and quote this message "
            f"verbatim. If you are the integrator or a builder, halt and "
            f"surface the stall — operator intervention is required."
        )
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": msg,
            }
        }))
    return 0


def session_start(args=None) -> int:
    config = phases.load_sprint_config()
    if not config:
        return 0
    state = phases.current_sprint_state()
    if not state:
        ctx = "[prusik] No active sprint. Write briefs/<feature>.md and use /sprint-start."
    else:
        feature = state.get("feature")
        phase = state.get("phase")
        ctx = f"[prusik] Active sprint '{feature}' in phase '{phase}'. Run /sprint-status for details."
    # v0.86.0 — surface engine↔template skew at session start so a restart-needed
    # state is impossible to miss (CC can't be force-restarted; this makes it loud).
    try:
        from prusik import refresh as _refresh
        banner = _refresh.skew_banner(ledger.project_root())
        if banner:
            ctx = f"[prusik] {banner}\n" + ctx
    except Exception:  # noqa: BLE001 — the hook must never fail
        pass
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": ctx,
        }
    }))
    return 0


def sprint_init(args) -> int:
    """Orchestrator: collapse the pre-sprint ritual into one command.

    Runs the deterministic steps (discovery, fingerprint), and prints clear
    next-step guidance when an LLM step is needed (cartographer, brief-critic).
    Idempotent — re-run until it advances into scoping.
    """
    feature = args.feature
    root = ledger.project_root()
    config = phases.load_sprint_config(root)
    if not config:
        print("[prusik-sprint-init] no .claude/sprint-config.yaml; run `prusik init` first.",
              file=sys.stderr)
        return 1

    # 0. No-skew guarantee (v0.86.0): if the engine moved ahead of this project's
    # deployed templates (you upgraded the package but didn't `prusik refresh`),
    # auto-sync them now so a sprint NEVER starts on stale agents. Closes the
    # package↔templates loophole — nothing to remember. Best-effort; the refresh
    # fails closed + visibly on a local-edit conflict.
    try:
        from prusik import refresh as _refresh
        _refresh.auto_sync_if_skewed(root, config)
        # field bridge #4: if skew REMAINS after the sync attempt (auto-refresh
        # opted out, or the sync hit a local-edit conflict), say so LOUDLY — a
        # skewed sprint must never start silently. The fixes are inert until
        # `prusik refresh`; doctor reporting it isn't enough (nobody runs doctor
        # mid-flow). Fail-loud floor, independent of the auto_refresh opt-out.
        banner = _refresh.skew_banner(root)
        if banner:
            print(f"[prusik-sprint-init] {banner}")
    except Exception as e:  # noqa: BLE001 — never wedge sprint-init on the sync
        print(f"[prusik-sprint-init] (template auto-sync skipped: {e})",
              file=sys.stderr)

    # 0b. Throttled (≤1/day) staleness nudge — so you're reminded to `prusik
    # update` at the natural moment, with no separate `doctor` run and no
    # network hit every time. Silent on offline / recently-checked / current.
    try:
        from prusik import version_check
        nudge = version_check.nudge_if_stale(root)
        if nudge:
            print(f"[prusik-sprint-init] {nudge}")
    except Exception:  # noqa: BLE001 — a nudge must never break sprint-init
        pass

    # 1. Discovery (inventory + dep-graph). Refresh if missing or >7d old.
    import time as _time
    dep_graph_path = root / ".sprint" / "dep-graph.json"
    inventory_path = root / ".sprint" / "inventory.json"
    need_discovery = not dep_graph_path.exists() or not inventory_path.exists()
    if not need_discovery:
        age_days = (_time.time() - dep_graph_path.stat().st_mtime) / 86400
        if age_days > 7:
            need_discovery = True
    if need_discovery:
        print("[prusik-sprint-init] refreshing discovery (inventory + dep-graph)…")
        discovery.inventory(root)
        discovery.dep_graph(root)

    # 2. design/map.md must exist (cartographer produces it; we can't).
    map_path = root / "design" / "map.md"
    if not map_path.exists():
        print("[prusik-sprint-init] design/map.md missing — cartographer hasn't run yet.")
        print()
        print("Next:")
        print("  1. Invoke cartographer via Agent(subagent_type='cartographer').")
        print("     The role will read .sprint/dep-graph.json and produce design/map.md.")
        print("  2. Run: prusik discovery fingerprint-map")
        print(f"  3. Re-run: prusik gate sprint-init --feature {feature}")
        return 1

    # 3. Fingerprint — deterministic, run it if missing.
    fp_path = root / ".sprint" / "map-fingerprint.json"
    if not fp_path.exists():
        discovery.fingerprint_map(root)
    else:
        drift = discovery.map_drift(root)
        # Default threshold mirrors the pre-sprint gate default.
        max_pct = 30
        if drift and drift.get("drift_pct", 0) > max_pct:
            print(f"[prusik-sprint-init] map drift {drift['drift_pct']}% > {max_pct}% — map is stale.")
            print(f"  added modules:   {drift.get('added', [])[:5]}")
            print(f"  removed modules: {drift.get('removed', [])[:5]}")
            print()
            print("Next:")
            print("  1. Re-invoke cartographer to refresh design/map.md.")
            print("  2. Run: prusik discovery fingerprint-map")
            print(f"  3. Re-run: prusik gate sprint-init --feature {feature}")
            return 1

    # 4. Brief must exist (human/user authors it; we can't).
    brief_path = root / "briefs" / f"{feature}.md"
    if not brief_path.exists():
        print(f"[prusik-sprint-init] briefs/{feature}.md missing.")
        print(f"  Author it via /brief-new {feature}, prusik serve, or hand-write.")
        return 1

    # 4a. v0.4.1: run brief-lint. Near-miss warnings become an actionable
    # pre-sprint gate so the author gets "did you mean?" suggestions BEFORE
    # scoping discovers the discrepancy. Structural errors surface as usual.
    from prusik.brief_lint import lint as _brief_lint
    print("[prusik-sprint-init] running brief-lint…")
    lint_rc = _brief_lint(brief_path)
    if lint_rc != 0:
        print("[prusik-sprint-init] brief-lint surfaced issues. Address them and")
        print(f"                 re-run `prusik gate sprint-init --feature {feature}`")
        print("                 or pass --skip-lint to proceed anyway.")
        if not getattr(args, "skip_lint", False):
            return 1

    # 5. Brief-critique must exist (brief-critic produces it).
    critique_path = root / "reports" / feature / "brief-critique.txt"
    if not critique_path.exists():
        print(f"[prusik-sprint-init] reports/{feature}/brief-critique.txt missing.")
        print()
        print("Next:")
        print(f"  1. Invoke brief-critic via Agent(subagent_type='brief-critic') on briefs/{feature}.md.")
        print(f"  2. Re-run: prusik gate sprint-init --feature {feature}")
        return 1

    # 6. v0.5.9: permissions baseline must be present. Hard-blocks team-mode
    # silent-Bash-denial bugs at the start of the sprint rather than mid-build.
    # cli-foundation lost ~5 subagent rounds to this exact class — surfacing
    # it before any sprint tokens burn is the right safety net even though
    # v0.5.8's surgical merge largely prevents the underlying drift.
    from prusik import permissions as _permissions
    perm_missing = _permissions.missing(root)
    if perm_missing:
        print(f"[prusik-sprint-init] permissions baseline incomplete — {len(perm_missing)} "
              f"entries missing from .claude/settings.json + settings.local.json.")
        print()
        print("Subagents in team-mode sprints will hit silent Bash denials for")
        print("commands matching the missing patterns (uv, pytest, ruff, mypy, etc.).")
        print()
        print("Fix (one of):")
        print("  1. prusik refresh                    # auto-merges baseline (v0.5.8+)")
        print("  2. prusik permissions audit          # prints paste-ready JSON to merge")
        print()
        print(f"Then re-run: prusik gate sprint-init --feature {feature}")
        ledger.append("sprint_init_blocked", feature=feature,
                       reason="permissions baseline incomplete",
                       missing_count=len(perm_missing))
        return 1

    # 7. All deterministic prerequisites satisfied — run sprint-start.
    from argparse import Namespace
    return sprint_start(Namespace(feature=feature))


# v0.11.0 #2: brief Types a one-shot trivial change can plausibly be. The
# excluded set (new_feature, refactor, migration) carries real design
# blast radius and MUST go through full scope/plan review — this is the
# ungameable guard on the trivial lane.
_TRIVIAL_ELIGIBLE_TYPES: frozenset[str] = frozenset(
    {"bug_fix", "doc", "config", "test", "chore"})


def sprint_start(args) -> int:
    feature = args.feature
    root = ledger.project_root()
    brief_path = root / "briefs" / f"{feature}.md"
    if not brief_path.exists():
        print(f"[prusik-gate] Brief not found: {brief_path}", file=sys.stderr)
        print(f"Write the brief first (try /brief-new {feature}).", file=sys.stderr)
        return 2
    ok, errors = schema.validate_brief(brief_path)
    if not ok:
        print(f"[prusik-gate] Brief invalid: {brief_path}", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 2

    # Pre-sprint gates: brief-critique, etc. Declared in sprint-config.yaml.
    config = phases.load_sprint_config(root) or {}
    unmet = _check_pre_sprint_gates(config, feature, root)
    if unmet:
        print("[prusik-gate] Cannot start sprint: unmet pre-sprint gates:", file=sys.stderr)
        for u in unmet:
            print(f"  - {u}", file=sys.stderr)
        ledger.append("sprint_start_blocked", feature=feature, unmet=unmet)
        return 2

    # v0.3.9: wipe stale worktrees before the sprint begins. Worktrees are
    # per-sprint ephemeral scratch space; contamination from a prior sprint
    # leaks into the current sprint's touch-list and confuses reviewers
    # (observed live: leftover fixtures from a previous sprint tripped
    # conventions-enforcer).
    cleaned = _clean_worktrees(root)
    if cleaned:
        print(f"[prusik-gate] Cleaned {len(cleaned)} stale worktree(s): {cleaned}")

    # v0.11.1 (Candidate S): a starting sprint cannot inherit a prior
    # sprint's fix-round. Defensively reap any pre-existing marker — the
    # load-bearing fix for the m4-s8c→#13 leak: m4-s8c was bypassed (no
    # sprint_complete ran), its open round orphaned and survived ~26h into
    # #13's reviewing phase, silently granting worktrees/*/** writable to a
    # foreign sprint. Reaping de-facto coupled to sprint_complete is exactly
    # why a bypass orphans it; sprint_start now reaps unconditionally.
    from prusik import fix_round as _fr
    _reaped = _fr.reap(root, reason=f"sprint-start: new sprint {feature!r}")
    if _reaped:
        print(f"[prusik-gate] Reaped orphaned fix-round from "
              f"{_reaped.get('feature')!r} before starting {feature!r}.")

    # v0.11.0 #2 — proportional-ceremony (trivial) lane. Ceremony
    # proportional to blast radius IS prusik's stated value; a guarded,
    # near-zero-blast-radius change earning near-zero design ceremony is
    # that principle applied, not a loophole. The guard is ungameable: the
    # brief's Type must be one a one-shot change can plausibly be. Decided
    # HERE (sprint-start) not at triage — triage runs AFTER scoping, too
    # late to skip the scope-critic ceremony that is the actual cost.
    state: dict = {"phase": "scoping", "feature": feature}
    if getattr(args, "trivial", False):
        btype = schema.parse_sections(brief_path.read_text()).get("## Type", "")
        btype = (btype.strip().split() or [""])[0].strip("`*_").rstrip(",.")
        if btype not in _TRIVIAL_ELIGIBLE_TYPES:
            print(f"[prusik-gate] --trivial rejected: brief Type is {btype!r}; the "
                  f"trivial lane is only for {sorted(_TRIVIAL_ELIGIBLE_TYPES)}.",
                  file=sys.stderr)
            print("  A new_feature/refactor/migration has real blast radius — "
                  "run without --trivial (full scope/plan review).",
                  file=sys.stderr)
            ledger.append("trivial_lane_rejected", feature=feature, brief_type=btype)
            return 2
        state["lane"] = "trivial"

    phases.set_sprint_state(state)
    ledger.append("sprint_started", feature=feature, cleaned_worktrees=cleaned,
                  lane=state.get("lane", "standard"))
    print(f"[prusik-gate] Sprint started: {feature}"
          + ("  [TRIVIAL LANE]" if state.get("lane") == "trivial" else ""))
    print("Phase: scoping")
    if state.get("lane") == "trivial":
        print(f"Trivial lane: write design/{feature}/trivial.md "
              f"(## Change + ## How verified), then /sprint-advance solo_execute "
              f"(skips scope-critic/triage/plan-critic). Reviewing gate UNCHANGED.")
    else:
        print(f"Next: run scoping role (reads briefs/{feature}.md + design/map.md),"
              f" produce design/{feature}/scope.md, then /sprint-advance triage.")
    return 0


def _clean_worktrees(root) -> list[str]:
    """Wipe every subdirectory of worktrees/ and return their names.

    Called at sprint-start so each sprint sees a clean slate. Files
    directly under worktrees/ (e.g. worktrees/.gitkeep, worktrees/README.md)
    are preserved — only subdirectories get removed.
    """
    import shutil as _shutil
    wt = root / "worktrees"
    if not wt.exists():
        return []
    cleaned: list[str] = []
    for child in wt.iterdir():
        if child.is_dir():
            _shutil.rmtree(child)
            cleaned.append(child.name)
    return cleaned


# v0.9.0 — success_criteria verification at sprint-complete.
#
# Driven by m4-h2 acceptance-metric miss (reviewer noted blocker, waved
# through, post-hoc bisect revealed criterion not met) AND m4-s9a (reviewer
# PASSED structurally without running 14 per-file content assertions,
# integrator-phase pytest caught 13 failures). Same shape: declared
# criteria not mechanically verified pre-complete. v0.9.0 closes the gap
# at the sprint-complete gate.

_VERIFY_DEFAULT_TIMEOUT_SEC = 300


def _run_success_criteria(feature: str, root: Path) -> tuple[bool, list[dict]]:
    """Run each verify_command from briefs/<feature>.criteria.yaml.

    Returns (all_passed, results). results entries:
        {"id": str, "passed": bool, "exit_code": int, "output_path": str,
         "verify_command": str, "expected_exit": int}

    For each criterion:
      - Run the script from project root with a per-criterion timeout.
      - Capture stdout+stderr to reports/<feature>/verify-<id>.txt.
      - exit_code != expected_exit → criterion FAILED.
      - Timeout → criterion FAILED with explicit stderr marker.
      - Ledger event `success_criterion_verified` emitted per criterion.
    """
    import subprocess

    brief_path = root / "briefs" / f"{feature}.md"
    criteria_path = schema.criteria_path_for_brief(brief_path)
    if not criteria_path.exists():
        return True, []  # no sibling file → opt-out / migration window

    criteria = schema.load_criteria(criteria_path)
    if not criteria:
        return True, []

    reports_dir = root / "reports" / feature
    reports_dir.mkdir(parents=True, exist_ok=True)

    # v0.65.0 — pre-flight infra gate (An adopter enabler #1). Health-check the infra the
    # criteria DECLARE (criteria.yaml `requires:`) BEFORE running verify_commands.
    # A command that errors because Postgres/the dev server is down would read as a
    # false skip/fail; fail CLOSED here with a clear "unreachable" instead. No
    # silent fallback — required infra down → blocked, not green-by-accident.
    from prusik import infra_check
    infra_ok, infra_results = infra_check.verify_criteria_infra(criteria_path)
    if infra_results:
        if infra_ok:
            ledger.append("infra_preflight", feature=feature, ok=True,
                          checked=[r["name"] for r in infra_results])
        else:
            down = [r for r in infra_results if not r["up"]]
            detail = "; ".join(f"{r['name']} ({r['target']}): {r['detail']}"
                               for r in down)
            out_path = reports_dir / "infra-preflight.txt"
            out_path.write_text(
                "[prusik-gate] required infra unreachable — NOT running "
                f"verify_commands (would false-skip/error):\n{detail}\n")
            ledger.append("infra_preflight", feature=feature, ok=False,
                          down=[r["name"] for r in down])
            return False, [{
                "id": "<infra-preflight>", "passed": False, "exit_code": -10,
                "output_path": str(out_path.relative_to(root)),
                "verify_command": "<infra health-check>", "expected_exit": 0,
                "infra_down": [r["name"] for r in down],
            }]

    results: list[dict] = []
    all_passed = True
    for entry in criteria:
        cid = entry.get("id", "<missing>")
        # CI-shaped criterion (fb-c80cb5c55771): a criterion whose real verify can
        # ONLY run in CI (a browser-e2e needing a live HTTPS stack + browsers, not
        # available on the dev host) closes on a green CI CHECK, not a faked local run.
        # `verify_in: ci` selects its `ci_verify_command` — a status check that exits 0
        # ONLY when the required CI run is green (e.g. `gh pr checks <pr> --required`).
        # Real evidence, fail-closed: a missing/red check still FAILS the criterion.
        ci_shaped = str(entry.get("verify_in", "")).lower() == "ci"
        vc = (entry.get("ci_verify_command", "") if ci_shaped
              else entry.get("verify_command", ""))
        expected = entry.get("expected_exit", 0)
        out_path = reports_dir / f"verify-{cid}.txt"

        # v0.81.0 (field finding #16) — operator-declared external block. A criterion that
        # genuinely needs operator-provided live setup (a real Stripe key, a
        # third-party sandbox) is DEFERRED, not run — neither pass nor fail. A
        # legitimate operator opt-out: VISIBLE (recorded + reported) and JUSTIFIED
        # (reason required by the schema), so it can't silently hide a real gap.
        if entry.get("blocked_external"):
            reason = str(entry.get("blocked_reason", "")).strip()
            results.append({"id": cid, "passed": None, "blocked": True,
                            "blocked_reason": reason, "exit_code": None,
                            "output_path": str(out_path),
                            "verify_command": vc, "expected_exit": expected})
            out_path.write_text(f"[prusik-gate] criterion id={cid!r} BLOCKED on "
                                f"external setup — deferred: {reason}\n")
            ledger.append("success_criterion_blocked", feature=feature, id=cid,
                          reason=reason)
            continue

        if not vc:
            why = ("ci_verify_command missing — a CI-verified criterion (verify_in: ci) "
                   "must PROVE the required CI check is green on the merge commit "
                   "(e.g. `gh pr checks <pr> --required`, exit 0 only when green); it "
                   "closes on real CI evidence, never a faked/skipped local run"
                   ) if ci_shaped else "missing verify_command"
            results.append({"id": cid, "passed": False, "exit_code": -1,
                            "output_path": str(out_path),
                            "verify_command": vc, "expected_exit": expected})
            all_passed = False
            out_path.write_text(f"[prusik-gate] {why} (criterion id={cid!r})\n")
            ledger.append("success_criterion_verified", feature=feature,
                          id=cid, passed=False, exit_code=-1, reason=why)
            continue

        try:
            proc = subprocess.run(
                ["/bin/bash", "-c", vc] if not vc.startswith("/")
                    else [vc],
                cwd=str(root),
                capture_output=True, text=True,
                timeout=_VERIFY_DEFAULT_TIMEOUT_SEC, check=False,
            )
            exit_code = proc.returncode
            out_text = (proc.stdout or "") + "\n--- stderr ---\n" + (proc.stderr or "")
            timed_out = False
        except subprocess.TimeoutExpired:
            exit_code = -2
            out_text = (f"[prusik-gate] verify_command exceeded "
                        f"{_VERIFY_DEFAULT_TIMEOUT_SEC}s timeout\n"
                        f"verify_command: {vc}\n")
            timed_out = True
        except OSError as e:
            exit_code = -3
            out_text = f"[prusik-gate] verify_command failed to spawn: {e}\n"
            timed_out = False

        out_path.write_text(out_text)
        passed = (not timed_out) and (exit_code == expected)
        if not passed:
            all_passed = False
        results.append({
            "id": cid, "passed": passed, "exit_code": exit_code,
            "output_path": str(out_path.relative_to(root)),
            "verify_command": vc, "expected_exit": expected,
        })
        ledger.append("success_criterion_verified", feature=feature,
                      id=cid, passed=passed, exit_code=exit_code,
                      verify_command=vc, expected_exit=expected,
                      verified_via=("ci" if ci_shaped else "local"),
                      output_path=str(out_path.relative_to(root)))

    return all_passed, results


def sprint_complete(args) -> int:
    """Close the sprint: record predicted-vs-actual and clear state."""
    feature = args.feature
    root = ledger.project_root()
    state = phases.current_sprint_state()
    if not state or state.get("feature") != feature:
        print(f"[prusik-gate] No active sprint for feature '{feature}' "
              f"(current state: {state})", file=sys.stderr)
        return 1

    # v0.9.0 — verify each declared success criterion before recording
    # sprint_complete. If any criterion FAILED, refuse the close. The
    # integrator writes reports/<feature>/integration-failure.txt naming
    # which criteria failed; operator must address or remove the criteria.
    all_passed, verify_results = _run_success_criteria(feature, root)
    # v0.81.0 (field finding #16) — blocked-on-external criteria are DEFERRED, not failed:
    # surface them so the deferral is visible, never silently swallowed.
    blocked = [r for r in verify_results if r.get("blocked")]
    if blocked:
        print(f"[prusik-gate] {len(blocked)} criterion(s) DEFERRED — blocked on "
              f"operator-provided external setup (visible, not hidden):",
              file=sys.stderr)
        for r in blocked:
            print(f"  - {r['id']}: {r.get('blocked_reason') or '(no reason given)'}",
                  file=sys.stderr)
        # v0.105.0 (field finding #22) — the defer→resolve complement: close it in-band
        # once the dependency exists, instead of a new sprint or an eyeball.
        print(f"    → when the setup exists, resolve in-band with proof: "
              f"`prusik criterion resolve {feature} <criterion-id>` "
              f"(runs the verify_command, records evidence, clears the deferral).",
              file=sys.stderr)
    failed = [r for r in verify_results if r.get("passed") is False]
    if failed:
        lines = [f"[prusik-gate] success_criteria not met for sprint '{feature}':"]
        for r in failed:
            lines.append(f"  - {r['id']}: exit={r['exit_code']} "
                         f"(expected {r['expected_exit']}); output: {r['output_path']}")
        lines.append("Resolve the failing criteria, or remove them from "
                     f"briefs/{feature}.criteria.yaml if no longer applicable, "
                     "then retry /sprint-complete.")
        print("\n".join(lines), file=sys.stderr)
        return 1

    predicted = _recover_predicted(root, feature)
    actual = {
        "mode": predicted.get("mode"),  # will be overwritten by --escalated
        "duration_min": args.duration_min,
        "tokens": args.tokens,
    }

    # v0.3.11: if the operator didn't pass --duration-min, fall back to
    # ledger-derived wall-clock (sprint_started timestamp to now). Digest
    # prediction-error stats are useless when half the entries are None.
    if actual["duration_min"] is None:
        derived = _derive_duration_from_ledger(feature)
        if derived is not None:
            actual["duration_min"] = derived
            actual["duration_source"] = "ledger"

    if args.escalated:
        actual["mode"] = "team" if predicted.get("mode") == "solo" else actual["mode"]
        actual["escalated"] = True

    ledger.append("sprint_complete", feature=feature,
                  predicted=predicted, actual=actual)
    phases.clear_sprint_state()
    # v0.11.1 (Candidate S): a completing sprint must not leave an open
    # fix-round to orphan. Symmetric with the sprint_start defensive reap —
    # both terminals reap, so neither a clean close nor a bypass can strand
    # a round into a foreign sprint.
    from prusik import fix_round as _fr
    if _fr.reap(root, reason=f"sprint-complete: {feature!r}"):
        print(f"[prusik-gate] Reaped open fix-round at sprint-complete of {feature!r}.")
    print(f"[prusik-gate] Sprint complete: {feature}")
    print(f"  predicted: {predicted}")
    print(f"  actual:    {actual}")
    return 0


def _derive_duration_from_ledger(feature: str) -> int | None:
    """Compute wall-clock minutes from sprint_started ledger ts to now."""
    from prusik.ledger import read_all
    from datetime import datetime as _dt, timezone as _tz
    started = None
    for r in read_all():
        if r.get("event") == "sprint_started" and r.get("feature") == feature:
            started = r.get("ts")
    if not started:
        return None
    try:
        start = _dt.fromisoformat(started)
    except ValueError:
        return None
    now = _dt.now(start.tzinfo) if start.tzinfo else _dt.now(_tz.utc)
    return int((now - start).total_seconds() / 60)


def _check_convergence_stall(feature: str, current: str | None,
                             target_phase: str, config: dict) -> int | None:
    """Convergence-stall control (v0.49.0). If `feature` has rewound up to its
    budget, escalate: record `convergence_stall`, PAUSE the sprint, and return
    a non-zero rc so the caller halts the rewind. Returns None to proceed.

    Fail-closed and recoverable: the run stops until a human runs `prusik
    resume`, and the budget resets on resume (see convergence._anchor_ts)."""
    from prusik import convergence
    from prusik import pause as _pause

    limit = convergence.max_rewinds(config)
    records = ledger.read_all()
    if not convergence.is_stall(records, feature, limit):
        return None

    count = convergence.rewind_count(records, feature)
    # kind="phase_rewind" distinguishes this hard-stop from the v0.8.11
    # tool-output soft-warn that shares the `convergence_stall` event.
    ledger.append("convergence_stall", kind="phase_rewind", feature=feature,
                  from_phase=current, to_phase=target_phase,
                  rewind_count=count, limit=limit)
    _pause.pause(reason=(f"convergence-stall: '{feature}' rewound {count}× "
                         f"(budget {limit}) without converging — human review "
                         f"needed before continuing"))
    print(f"[prusik-gate] CONVERGENCE STALL: '{feature}' has rewound {count}× "
          f"(budget {limit}). Sprint PAUSED — not churning autonomously. A human "
          f"must review the thrash and `prusik resume` (resume resets the "
          f"budget).", file=sys.stderr)
    return 2


def _full_suite_gate(current: str, target_phase: str, feature: str) -> int | None:
    """Full-suite proof check at building exit (field finding #14 v1+v2). Returns 2 to
    BLOCK (only when `require_full_suite_at_build` is set in sprint-config), else
    None to proceed (printing an advisory if the full suite wasn't proven). The
    learned test-count baseline distinguishes a real full-suite proof from a
    subset of just-the-new-tests."""
    try:
        from prusik import phases as _phases
        from prusik import suite_baseline
        root = ledger.project_root()
        config = _phases.load_sprint_config() or {}
        require_full = bool(config.get("require_full_suite_at_build"))
        records = ledger.read_all()
        entered = max((r.get("ts", "") for r in records
                       if r.get("event") == "phase_advance"
                       and r.get("to_phase") == current), default="")
        green = [int(r.get("executed", 0) or 0) for r in records
                 if r.get("event") == "prove_run" and r.get("kind") == "tests"
                 and r.get("proven") and r.get("ts", "") >= entered]
        best = max(green) if green else None
        baseline = suite_baseline.load(root)
        if best is None:
            problem = ("no green `prusik prove --kind tests` recorded this "
                       "building phase")
        elif not suite_baseline.looks_full(best, baseline):
            problem = (f"the proven run executed {best} test(s) but the full suite "
                       f"is ~{baseline} — looks like a subset, not the full suite")
        else:
            return None        # full suite proven green → proceed
    except Exception as e:  # noqa: BLE001 — never break advance on a check error
        print(f"[prusik-gate] (full-suite check skipped: {e})", file=sys.stderr)
        return None
    if require_full:
        print(f"[prusik-gate] BLOCKED leaving '{current}': {problem}. Advancing must "
              f"run on FULL-suite evidence, not a subset — a touched-set green is "
              f"false confidence (fb-90cfcfa8b918). Run the FULL suite green "
              f"(`prusik prove --kind tests -- <full test command>`) before advancing. "
              f"(require_full_suite_at_build is on.)", file=sys.stderr)
        ledger.append("advance_blocked", from_phase=current, to_phase=target_phase,
                      feature=feature, reason=f"full-suite not proven: {problem}")
        return 2
    print(f"[prusik-gate] full-suite ADVISORY — {problem}. A structural change can "
          f"break existing tests outside the touched set; a touched-set green hides "
          f"them. Prove the FULL suite green before advancing. (Set "
          f"require_full_suite_at_build in sprint-config to hard-block.)")
    return None


def advance(args) -> int:
    target_phase = args.phase
    feature = args.feature
    config = phases.load_sprint_config()
    if not config:
        print("No sprint-config.yaml found. Run 'prusik init' first.", file=sys.stderr)
        return 1
    target_spec = phases.get_phase_spec(config, target_phase)
    if not target_spec:
        valid = [p["name"] for p in config.get("phases", [])]
        print(f"Unknown phase: {target_phase}. Valid: {valid}", file=sys.stderr)
        return 1

    state = phases.current_sprint_state() or {}
    current = state.get("phase")

    # v0.3.10: rewind detection. If the target phase is EARLIER in the
    # canonical order than current, require --allow-rewind. Rewinds are
    # occasionally correct (reviewer finds a defect → re-enter building
    # to fix), but silent rewinds make retros hard to read.
    rewinding = bool(current) and phases.is_rewind(current, target_phase)
    if rewinding and not getattr(args, "allow_rewind", False):
        print(f"[prusik-gate] Refusing to rewind {current} → {target_phase} "
              f"(earlier in canonical phase order).", file=sys.stderr)
        print("  Rewinds are sometimes correct (e.g., a reviewer finds a defect",
              file=sys.stderr)
        print("  that needs fixing in the builder worktree). Pass --allow-rewind",
              file=sys.stderr)
        print("  to confirm; the ledger will record this as `phase_rewind`",
              file=sys.stderr)
        print("  rather than `phase_advance` so retros show the flip.",
              file=sys.stderr)
        ledger.append("advance_blocked", from_phase=current, to_phase=target_phase,
                      feature=feature, reason="rewind without --allow-rewind")
        return 2

    # Forward advance: verify exit artifacts + consistency on the phase we're leaving.
    # Skipped for rewinds because the artifacts aren't produced yet by definition.
    if current and not rewinding:
        current_spec = phases.get_phase_spec(config, current) or {}
        missing = _unsatisfied_exit_artifacts(current_spec, feature)
        if missing:
            # v0.11.0 #3: a recorded integrate-with-flag escalation is a
            # deliberate, rationale-bearing, AUDITED operator override of
            # the reviewing correctness gate — structurally the same
            # pattern as --allow-rewind. It does NOT fabricate a PASS:
            # the reports keep their FAIL, the ledger records exactly what
            # was overridden and why. This replaces the old out-of-prusik
            # STOP that left no prusik record at all (the m4-s8c bypass).
            if current == "reviewing":
                from prusik import fix_round as _fr
                esc = _fr.latest_integrate_escalation(feature)
                if esc:
                    print("[prusik-gate] INTEGRATING UNDER ESCALATION — reviewing "
                          "gate OVERRIDDEN by recorded operator decision.",
                          file=sys.stderr)
                    print(f"  Overridden: {missing}", file=sys.stderr)
                    print(f"  Rationale: {esc.get('rationale')}", file=sys.stderr)
                    print("  This is loud and audited; reports retain FAIL.",
                          file=sys.stderr)
                    ledger.append("integrated_under_escalation",
                                  from_phase=current, to_phase=target_phase,
                                  feature=feature, overridden=missing,
                                  rationale=esc.get("rationale"))
                    # fall through past the block — advance proceeds
                else:
                    print(f"[prusik-gate] Cannot advance from '{current}': unmet exit artifacts:",
                          file=sys.stderr)
                    for m in missing:
                        print(f"  - {m}", file=sys.stderr)
                    suspects = consistency.detect_reviewer_fabrication(
                        ledger.project_root(), feature)
                    if suspects:
                        consistency.emit_fabrication_warnings(suspects)
                    ledger.append("advance_blocked", from_phase=current,
                                  to_phase=target_phase, feature=feature,
                                  missing=missing)
                    return 2
            else:
                print(f"[prusik-gate] Cannot advance from '{current}': unmet exit artifacts:",
                      file=sys.stderr)
                for m in missing:
                    print(f"  - {m}", file=sys.stderr)
                ledger.append("advance_blocked", from_phase=current, to_phase=target_phase,
                              feature=feature, missing=missing)
                return 2

        inconsistencies = consistency.run_for_phase(current, ledger.project_root(), feature)

        # v0.10.0 (Fix 1): observability of the eliminated hand-list rot.
        # Emitted on building/solo_execute exit when the hand-list actually
        # drifted from scope — proving the cure (drift existed but did NOT
        # block, because the gate now compares derived reality vs scope).
        if current in ("building", "solo_execute"):
            summary = consistency.reconciliation_summary(
                ledger.project_root(), feature)
            if summary and (summary["stale_in_plan"]
                            or summary["dropped_from_plan"]):
                ledger.append("modules_reconciled", from_phase=current,
                              feature=feature, **summary)
            # v0.68.0 — cross-builder contract drift (field finding #7). Non-blocking
            # SIGNAL: a symbol defined in >1 worktree is parallel-builder drift
            # the post-integration sentinel would catch ~30 min later. Surface it
            # here, in seconds, before reviewing. Never alters rc.
            try:
                from prusik import cross_builder
                adv = cross_builder.advisory(ledger.project_root(), feature)
                if adv:
                    print(adv)
                    ledger.append("cross_builder_check", from_phase=current,
                                  feature=feature,
                                  duplicates=len(cross_builder.duplicate_symbols(
                                      ledger.project_root(), feature)))
            except Exception as e:  # noqa: BLE001 — advisory must never break advance
                print(f"[prusik-gate] (cross-builder advisory skipped: {e})",
                      file=sys.stderr)
            # v0.80.0/0.82.0 — full-suite proof check (field finding #14). Builders
            # claim "done" on their own tests + type-check; a structural change
            # breaks EXISTING tests they didn't write, invisible until the
            # reviewing sentinel a phase later. ADVISORY by default; a HARD BLOCK
            # when `require_full_suite_at_build` is set in sprint-config (#14 v2).
            # The baseline (largest green tests count seen) tells a real full-suite
            # proof from a subset of just-the-new-tests.
            fs_block = _full_suite_gate(current, target_phase, feature)
            if fs_block is not None:
                return fs_block

        # Full-suite evidence at the INTEGRATION gate (fb-90cfcfa8b918). A
        # touched-set green at reviewing is false confidence — the full suite catches
        # fan-out regressions OUTSIDE the touched set (one sprint: 133 touched passed,
        # the full suite caught 24). Require a FRESH full-suite prove recorded this
        # reviewing phase before integrating; same advisory-default / opt-in-hard-block
        # + subset-vs-full baseline check as the building gate.
        if current == "reviewing" and target_phase == "integrating":
            fs_block = _full_suite_gate(current, target_phase, feature)
            if fs_block is not None:
                return fs_block

        if inconsistencies:
            print(f"[prusik-gate] Cannot advance from '{current}': cross-artifact inconsistencies:",
                  file=sys.stderr)
            for i in inconsistencies:
                print(f"  - {i}", file=sys.stderr)
            ledger.append("advance_blocked", from_phase=current, to_phase=target_phase,
                          feature=feature, inconsistencies=inconsistencies)
            return 2

    # v0.49.0 — convergence-stall control. A rewind that brings this feature
    # past its rewind budget is thrashing (the canonical case: 8 rewinds, no
    # remediation). FAIL CLOSED: pause + escalate to a human instead of churning
    # autonomously — checked BEFORE the state transition so a stalled rewind
    # neither moves the phase nor records a `phase_rewind`.
    if rewinding:
        stall_rc = _check_convergence_stall(feature, current, target_phase, config)
        if stall_rc is not None:
            return stall_rc

    # v0.11.0 #2: preserve the lane across phase transitions (set_sprint_state
    # overwrites the whole dict; the trivial lane must survive every advance).
    new_state = {"phase": target_phase, "feature": feature}
    if state.get("lane"):
        new_state["lane"] = state["lane"]
    phases.set_sprint_state(new_state)
    if rewinding:
        ledger.append("phase_rewind", from_phase=current, to_phase=target_phase,
                      feature=feature)
        print(f"[prusik-gate] REWIND: {current} → {target_phase}", file=sys.stderr)
    else:
        ledger.append("phase_advance", from_phase=current, to_phase=target_phase,
                      feature=feature)
        print(f"[prusik-gate] Advanced: {current} → {target_phase}")
    return 0


def mark_fallback(args) -> int:
    """Log that a reviewer-artifact fallback was invoked.

    v0.5.0: when the slash command detects a reviewer role (brief-critic,
    scope-critic, plan-critic, regression-sentinel, conventions-enforcer)
    produced a PASS/FAIL verdict in its text response but did NOT write
    the artifact file, the command writes the file from the parsed text
    and calls this to log the event. Surfaces in `prusik digest` so the
    operator sees how often fallback fires — if it stays low post-
    v0.4.5 restart hint, the fallback is a safety net; if it's recurring,
    it's doing real work and the agent prompt needs further tightening.
    """
    ledger.append(
        "reviewer_fallback_used",
        role=args.role,
        feature=args.feature,
    )
    print(f"[prusik-gate] reviewer_fallback_used logged: role={args.role} feature={args.feature}")
    return 0


# v0.10.0 — Fix 3: content-addressed re-gating.
#
# m4-s8c mined 13 scope-critic dispatches, most on UNCHANGED content
# ("APPROVED 6+ passes; rewound purely because position reset"). A critic
# verdict is bound to the substantive-content hash of what it judged. On a
# rewind/re-entry, if the artifact's substantive hash is unchanged since the
# last APPROVED verdict, the verdict carries forward — no re-dispatch. Cost
# moves from O(rewinds) to O(substantive-changes). The engine itself honors
# carry-forward (durable; not dependent on prompt behavior).

# v0.11.0 #1 — reviewer roles judge the BUILT CODE (the worktree file-set),
# not a design doc. Extra project-relative files folded into the judged
# hash: conventions-enforcer judges code AGAINST CLAUDE.md, so CLAUDE.md is
# an input to its verdict; regression-sentinel judges code + tests only.
_REVIEWER_INPUTS: dict[str, tuple[str, ...]] = {
    "regression-sentinel": (),
    "conventions-enforcer": ("CLAUDE.md",),
}

_POSITIVE_VERDICTS = {"APPROVED", "PASS"}

# approval-artifact basename → (role, source-template | None, gate token).
# src_tmpl None ⇒ the judged source is the worktree file-set (reviewers).
#
# scope/plan: v0.10.0 Fix 3 — the m4-s8c critic rerun storm (13 + 6).
# regression/conventions: v0.11.0 #1 — these were EXCLUDED in v0.10.0
# ("re-running tests is legitimate"), but that left the DOMINANT per-rewind
# cost (full suite + cold-start mypy on 96k LOC; regression-sentinel.md:34
# "the dominant slowness") fully O(rewinds). Carry-forward here does NOT
# fabricate a test result: it reuses a real prior PASS only when the built
# code is byte-identical (the worktree hash IS the rebuilt-detector — if a
# rewind re-entered building the hash changes and the gate re-runs). The
# deterministic-build assumption the worktree model already relies on, made
# explicit, bounded to one sprint's rewind cycle (same env/session).
# brief-critic still excluded (pre-sprint, no measured waste).
_APPROVAL_SOURCE: dict[str, tuple[str, str | None, str]] = {
    "scope-approval.txt": ("scope-critic", "design/{feature}/scope.md", "APPROVED"),
    "plan-approval.txt": ("plan-critic", "design/{feature}/plan.md", "APPROVED"),
    "regression.txt": ("regression-sentinel", None, "PASS"),
    "conventions.txt": ("conventions-enforcer", None, "PASS"),
}


# Derived build/cache dirs — regenerated by builds/installs, NOT the source a
# reviewer judged. Pruned from the substantive hash so a build-triggering capture
# (turbo `^build` → dist/) doesn't move the hash and stale a co-reviewer's evidence
# (fb-b4eb142e5740). Conservative by design: only UNAMBIGUOUSLY-derived names
# — excluding real source would be worse (a stale reviewer PASS surviving a real
# code change), so when in doubt a dir stays IN the hash.
_DERIVED_DIRS = frozenset({
    "node_modules", "dist", ".next", ".nuxt", ".turbo", ".svelte-kit",
    "coverage", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
})


def _worktree_substantive_hash(root: Path, extra: tuple[str, ...] = ()) -> str:
    """Deterministic 16-hex hash of the built-code worktree file-set.

    Code is hashed by raw bytes (whitespace IS significant in code, unlike
    markdown sections). Fix-4 meta carve-outs are excluded so a rewind that
    only churns reports/.sprint/etc. does not bust a valid reviewer PASS.
    Each `extra` project file (e.g. CLAUDE.md) folds in via the
    section-normalized substantive_hash (it is judged as a doc, not code).
    """
    import hashlib as _h
    import os as _os
    from prusik import consistency as _consistency
    parts: list[str] = []
    wt = root / "worktrees"
    if wt.exists():
        collected: list[tuple[str, bytes]] = []
        for teammate in sorted(wt.iterdir()):
            if not teammate.is_dir():
                continue
            # A REAL git worktree (TS full tree): hash exactly what git considers
            # project content (tracked + untracked-not-ignored). This EXCLUDES every
            # gitignored build artifact BY CONSTRUCTION — a tsc `tsbuildinfo`, dist/,
            # coverage/, lockfiles — so a build-/typecheck-triggering capture can't drift
            # the hash and stale a co-reviewer's evidence. Inverts the doomed derived-dir
            # DENYLIST (which let `tsbuildinfo` slip through) into a git-tracked ALLOWLIST
            # — completes the recurring dist-in-hash fix (fb-b4eb142e5740 →
            # fb-086ca221468d). Genuinely-new source (untracked, not ignored) is still in,
            # so a stale PASS can't survive a real code addition.
            git_files = _consistency.git_project_files(teammate)
            if git_files is not None:
                rels: list[str] = git_files
            else:
                # Partial mirror (Python): no git worktree to ask. Walk the dir, prune
                # the DERIVED build/cache dirs by name, THEN filter the result through the
                # ROOT repo's gitignore — so a capture-generated artifact (`.coverage`,
                # `*.log`) that a co-reviewer's run drops into the mirror is excluded the
                # SAME way the real-git-worktree path excludes it (v0.152.0). This makes
                # each reviewer's snapshot stable against another reviewer's capture side
                # effects — the parallel-reviewer hash race (fb-92e248d6a208).
                walked = []
                for dirpath, dirnames, filenames in _os.walk(teammate):
                    dirnames[:] = [d for d in dirnames if d not in _DERIVED_DIRS]
                    for fn in filenames:
                        f = Path(dirpath) / fn
                        if f.is_file():
                            walked.append(str(f.relative_to(teammate)))
                ignored = _consistency.gitignored_subset(root, walked)
                rels = [r for r in walked if r not in ignored]
            for rel in rels:
                # Exclude non-code ORCHESTRATION from the built-code hash: a full git
                # worktree (TS) contains the whole tree, so editing design/
                # (scope/plan/deviations) or .claude/ (settings/config/agents) — which a
                # reviewer never reads — would otherwise re-stale its evidence by
                # whole-tree hash (field retro #3). Freshness is scoped to the CODE the
                # reviewer judged; a reviewer that needs a specific doc folds it in
                # explicitly via _REVIEWER_INPUTS.
                if (rel.startswith(".sprint/") or rel.startswith("reports/")
                        or rel.startswith("scripts/verify/")
                        or rel.startswith("briefs/")
                        or rel.startswith("design/")
                        or rel.startswith(".claude/")):
                    continue
                f = teammate / rel
                try:
                    collected.append((f"{teammate.name}/{rel}", f.read_bytes()))
                except OSError:
                    continue
        for relpath, content in sorted(collected):
            parts.append(relpath)
            parts.append(_h.sha256(content).hexdigest())
    for ex in extra:
        exp = root / ex
        if exp.exists():
            parts.append(ex)
            parts.append(schema.substantive_hash(exp))
    return _h.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def _judged_key(role: str, feature: str, artifact: str | None) -> str:
    """Stable ledger key for what `role` judged. Reviewers judge the
    worktree (one synthetic key, so record/lookup always agree); critics
    judge their named design artifact."""
    if role in _REVIEWER_INPUTS:
        return "worktrees"
    return artifact or ""


def _judged_hash(role: str, feature: str, artifact: str | None,
                 root: Path) -> str | None:
    """Substantive hash of what `role` judged, or None if unjudgeable."""
    if role in _REVIEWER_INPUTS:
        return _worktree_substantive_hash(root, _REVIEWER_INPUTS[role])
    if not artifact:
        return None
    src = root / artifact
    if not src.exists():
        return None
    return schema.substantive_hash(src)


def _latest_verdict(role: str, feature: str, key: str) -> dict | None:
    """Most recent critic_verdict ledger event for (role, feature, key)."""
    found = None
    for r in ledger.read_all():
        if (r.get("event") == "critic_verdict"
                and r.get("role") == role
                and r.get("feature") == feature
                and r.get("artifact") == key):
            found = r
    return found


def record_verdict(args) -> int:
    """Bind a verdict to the substantive hash of what it judged.

    Called by the critic/reviewer slash-command AFTER it produces a
    verdict. The hash is what makes the verdict carry-forwardable.
    """
    root = ledger.project_root()
    key = _judged_key(args.role, args.feature, args.artifact)
    h = _judged_hash(args.role, args.feature, args.artifact, root)
    if h is None:
        print(f"[prusik-gate] cannot record verdict — nothing to judge "
              f"({args.role}, {args.artifact})", file=sys.stderr)
        return 2
    ledger.append("critic_verdict", role=args.role, feature=args.feature,
                  artifact=key, verdict=args.verdict, content_hash=h)
    print(f"[prusik-gate] verdict recorded: {args.role} {args.verdict} "
          f"{key} @ {h}")
    return 0


def verdict_current(args) -> int:
    """Exit 0 iff a prior POSITIVE verdict's hash matches what's judged NOW.

    The orchestrator calls this before re-dispatching a critic/reviewer on
    a rewind: exit 0 → carry the prior verdict, skip the dispatch; nonzero
    → substantive change (or never passed), re-dispatch is warranted.
    """
    root = ledger.project_root()
    key = _judged_key(args.role, args.feature, args.artifact)
    cur = _judged_hash(args.role, args.feature, args.artifact, root)
    if cur is None:
        print(f"[prusik-gate] re-gate needed: nothing to judge ({key})")
        return 1
    v = _latest_verdict(args.role, args.feature, key)
    if v is None:
        print(f"[prusik-gate] re-gate needed: no prior verdict for {args.role}")
        return 1
    if v.get("verdict") not in _POSITIVE_VERDICTS:
        print(f"[prusik-gate] re-gate needed: last verdict was {v.get('verdict')}")
        return 1
    if v.get("content_hash") != cur:
        print(f"[prusik-gate] re-gate needed: {key} changed since pass "
              f"({v.get('content_hash')} → {cur})")
        return 1
    print(f"[prusik-gate] verdict current: {args.role} {v.get('verdict')} {key} "
          f"@ {cur} — carry forward, skip re-dispatch")
    return 0


def _approval_carried_forward(approval_rel: str, feature: str,
                              root: Path) -> str | None:
    """If `approval_rel` is a verdict gate whose judged source is unchanged
    since the last POSITIVE verdict, return a carry-forward marker string
    (containing the gate's expected token) to (re)write into the file.

    Engine half of the cure: even if a rewind wiped the approval file, a
    still-valid hash-bound verdict in the ledger satisfies the gate without
    re-dispatching the critic/reviewer.
    """
    from pathlib import Path as _P
    base = _P(approval_rel).name
    mapping = _APPROVAL_SOURCE.get(base)
    if not mapping:
        return None
    role, src_tmpl, token = mapping
    artifact = src_tmpl.format(feature=feature) if src_tmpl else None
    key = _judged_key(role, feature, artifact)
    cur = _judged_hash(role, feature, artifact, root)
    if cur is None:
        return None
    v = _latest_verdict(role, feature, key)
    if (not v or v.get("verdict") not in _POSITIVE_VERDICTS
            or v.get("content_hash") != cur):
        return None
    src_desc = artifact if artifact else "built code (worktree)"
    return (f"{token} (carried forward — {src_desc} substantively unchanged "
            f"since {role} verdict @ {cur}; v0.11.0)")


# ── v0.12.0 — reviewer execution-evidence (Candidate F) ──────────────────
#
# `prusik gate capture` runs a reviewer's suite, captures the REAL exit code +
# a non-empty primitive parsed from the tool's OWN output, and writes a
# machine evidence manifest. The reviewing gate then honors a PASS only
# against that manifest — never the agent's word. This pulls the proven
# integrator-gate-inward (prusik's already-working false-clean defense at
# merge) earlier, into the reviewer phase, before effort is sunk to
# integration.

_CAPTURE_TIMEOUT_SEC = 1800  # test suites legitimately run long

_PHASE_ROLE = {
    "regression": "regression-sentinel",
    "conventions": "conventions-enforcer",
}

# `prusik gate capture` already runs the command through `bash -c`, so a user-added
# `bash -c …` wrapper double-wraps it AND the argv join (" ".join) drops the inner
# quoting — `-- bash -c pnpm contracts:check` becomes `bash -c "bash -c pnpm
# contracts:check"`, whose inner bash runs the SCRIPT `pnpm` ($0=contracts:check) →
# a help screen, exit≠0. The identical wrong result then trips a convergence stall
# (fb-9f107742fe4d).
_CAPTURE_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_CAPTURE_SHELL_C = frozenset({"-c", "-lc", "-ic", "-lic", "-cl"})


def _shell_wrapper_misuse(cmd: list[str]) -> str | None:
    """Return loud, actionable guidance if `cmd` is a redundant `<shell> -c …`
    wrapper (which capture double-wraps + mangles), else None. Caller fails CLOSED
    — never run the wrong thing silently."""
    if len(cmd) >= 2 and Path(cmd[0]).name in _CAPTURE_SHELLS \
            and cmd[1] in _CAPTURE_SHELL_C:
        return (
            f"[prusik-gate] capture already runs your command through `bash -c`, so a "
            f"`{cmd[0]} {cmd[1]} …` wrapper double-wraps it and the argv join then "
            f"drops the inner quoting — e.g. `-- bash -c pnpm contracts:check` runs "
            f"the script `pnpm` (a help screen), not `pnpm contracts:check`. Drop the "
            f"wrapper:\n"
            f"  • simple command — pass it directly:\n"
            f"      prusik gate capture … -- pnpm contracts:check\n"
            f"  • compound / shell syntax (&&, |, env=...) — pass it as ONE quoted arg:\n"
            f"      prusik gate capture … -- 'pnpm contracts:check && pnpm lint:prove'")
    return None


# The 'real work happened' primitive now lives in prusik.evidence (shared by
# the standalone `prusik prove` command). Kept as a module-level alias so the
# capture path + any importers keep the original name.
from prusik.evidence import executed_count as _parse_nonempty_primitive  # noqa: E402

_CAPTURE_PATH_CACHE: str | None = None


def _capture_env_path() -> str:
    """PATH for the capture subprocess, enriched with the user's login+interactive shell
    PATH so a toolchain installed via a version manager (nvm/volta/fnm) — which adds to
    PATH in shell init, NOT in a bare non-interactive `bash -c` — resolves during capture
    (fb-53f161606abc: `npx`/`pnpm` exited 127 and were recorded as a meaningless
    evidence entry). Best-effort and ISOLATED: the login PATH is extracted between NUL
    sentinels so a profile banner can't corrupt it, validated, then unioned with the
    current PATH (toolchain dirs first). On any failure/timeout it falls back to the
    current PATH — capture then behaves exactly as before, and the command-not-found
    guard still catches an unresolved tool. Cached per process."""
    global _CAPTURE_PATH_CACHE
    if _CAPTURE_PATH_CACHE is not None:
        return _CAPTURE_PATH_CACHE
    import os
    import subprocess
    fallback = os.environ.get("PATH", "")
    _CAPTURE_PATH_CACHE = fallback
    shell = os.environ.get("SHELL") or "/bin/bash"
    try:
        r = subprocess.run([shell, "-lic", r'printf "\0%s\0" "$PATH"'],
                           capture_output=True, text=True, timeout=5, check=False)
        out = r.stdout or ""
        if out.count("\x00") >= 2:
            login_path = out.split("\x00")[1]
            if login_path and (os.pathsep in login_path or login_path.startswith("/")):
                seen: set[str] = set()
                merged: list[str] = []
                for d in login_path.split(os.pathsep) + fallback.split(os.pathsep):
                    if d and d not in seen:
                        seen.add(d)
                        merged.append(d)
                _CAPTURE_PATH_CACHE = os.pathsep.join(merged)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return _CAPTURE_PATH_CACHE


def capture(args) -> int:
    """Run a reviewer command, record prusik-captured execution evidence, and
    exit with the command's own exit code (transparent to pass/fail).

    Usage: prusik gate capture --feature F --phase regression --kind tests \\
               -- <verbatim test/lint command>
    """
    import subprocess
    root = ledger.project_root()
    cmd = list(args.command or [])
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        # fb-9a095c7674f2: a standalone `--reset` is a CLEAR — discard the phase's
        # prior evidence and record NOTHING, so an agent never fakes a clear with a
        # no-op `-- echo reset` (which appended a tests=0 entry that tripped the
        # false-clean guard at every advance). Re-capture afterward with a real command.
        if getattr(args, "reset", False):
            reports_dir = root / "reports" / args.feature
            reports_dir.mkdir(parents=True, exist_ok=True)
            ev_path = schema.evidence_path_for(reports_dir, args.phase)
            n = len(schema.load_evidence(ev_path)) if ev_path.exists() else 0
            ev_path.write_text(json.dumps(
                {"schema_version": schema.EVIDENCE_SCHEMA_VERSION, "entries": []},
                indent=2))
            print(f"[prusik-gate] --reset: cleared {n} evidence entr"
                  f"{'y' if n == 1 else 'ies'} for {args.phase}. No entry recorded "
                  f"— re-capture with a real test/lint command.")
            return 0
        print("[prusik-gate] capture: no command given after `--`",
              file=sys.stderr)
        return 2
    misuse = _shell_wrapper_misuse(cmd)
    if misuse:
        print(misuse, file=sys.stderr)
        return 2
    # Reconstruct the shell line WITHOUT dropping quoting. A bare `" ".join` mangled
    # any arg with internal spaces — `pytest -m "not browser_smoke"` → argv
    # ["pytest","-m","not browser_smoke"] → "pytest -m not browser_smoke" → pytest
    # reads `browser_smoke` as a PATH (exit 4, 0 tests), blocking evidence capture
    # (fb-32b3a89cc1d5). shlex.join re-quotes each arg so the marker survives.
    # A SINGLE arg stays raw — it's a deliberate shell line (e.g. `"a && b"`), which
    # shlex.join would wrongly quote into one literal command.
    cmd_str = cmd[0] if len(cmd) == 1 else shlex.join(cmd)

    import os as _os
    cap_env = {**_os.environ, "PATH": _capture_env_path()}
    try:
        proc = subprocess.run(["/bin/bash", "-c", cmd_str], cwd=str(root), env=cap_env,
                               capture_output=True, text=True,
                               timeout=_CAPTURE_TIMEOUT_SEC, check=False)
        exit_code = proc.returncode
        combined = (proc.stdout or "") + "\n--- stderr ---\n" + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        exit_code = -2
        combined = f"[prusik-gate] capture exceeded {_CAPTURE_TIMEOUT_SEC}s\n"
    except OSError as e:
        exit_code = -3
        combined = f"[prusik-gate] capture failed to spawn: {e}\n"

    # Stream the real output through so the reviewer sees it unaltered.
    sys.stdout.write(combined)

    kind = args.kind
    value = _parse_nonempty_primitive(kind, combined, cmd_str)

    # Is this real execution evidence, or a non-evidence artifact (the tool never ran / a
    # cache replay / …)? ONE registered classifier owns every known mode — see
    # `capture_diagnose`. The first match refuses to record (fail-closed: the tool's word
    # isn't evidence) and names the remedy; the refusal is logged (`capture_non_evidence`)
    # so recurrence is measurable per project. A NEW non-evidence context is a registered
    # detector + test there, NOT a new branch here — the structural cure for the recurring
    # evidence-capture finding cluster (exit-127 fb-53f161606abc, cache-replay an adopter
    # fb-b587d8d9b71c, …).
    from prusik import capture_diagnose
    verdict = capture_diagnose.diagnose(capture_diagnose.CaptureResult(
        kind=kind, exit_code=exit_code, value=value, output=combined, command=cmd_str))
    if verdict is not None:
        print(f"[prusik-gate] capture: {verdict.remedy}", file=sys.stderr)
        ledger.append("capture_non_evidence", feature=args.feature,
                       phase=args.phase, kind=kind, mode=verdict.mode)
        return verdict.exit_code
    # v0.82.0 (#14 v2) — a green full-suite tests capture (the sentinel's run) is
    # the authoritative full-suite size; feed the baseline so the building-exit
    # gate can tell a full proof from a subset. max-based, so a subset can't lower it.
    if kind == "tests" and exit_code == 0 and value > 0:
        from prusik import suite_baseline
        suite_baseline.update(root, value)
    out_sha = hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]
    role = _PHASE_ROLE.get(args.phase, "")
    wt_hash = _worktree_substantive_hash(root, _REVIEWER_INPUTS.get(role, ()))

    entry = {
        "phase": args.phase,
        "command": cmd_str,
        "exit_code": exit_code,
        "nonempty_primitive": {"kind": kind, "value": value},
        "output_sha": out_sha,
        "worktree_hash": wt_hash,
        "captured_by": schema.EVIDENCE_CAPTURED_BY,
    }
    # field bridge #3: record observed FAILED count for a tests capture, so the
    # advance gate can bound a declared known-failure baseline (a failing command
    # is tolerable only when observed ≤ what's git-stash-proven pre-existing).
    if kind == "tests":
        from prusik import evidence as _evidence
        entry["observed_failures"] = _evidence.failed_count(combined)
        # field finding #4: record the worktree's named skipped tests so `prusik delta-check`
        # can later surface EXACTLY which tests stopped running on the integrated tree
        # (provenance, not a bare count). Only when present, to keep evidence lean.
        if (skip_names := _evidence.skipped_tests(combined)):
            entry["skipped_tests"] = skip_names
        # fb-6a573cfe59fb: a SCOPED behavior run that exits nonzero PURELY from a
        # package-wide coverage threshold (0 tests failed) is a coverage shortfall, not
        # a test failure — record it from the live output so the advance gate diagnoses
        # it truthfully instead of "errored phase scored clean".
        if _evidence.coverage_gate_only_exit(combined, exit_code):
            entry["coverage_gate_only"] = True

    # v0.18.0 F §3.5 companion #1: baseline declaration. When the
    # reviewer is asserting a baseline (known_failures count), the
    # declaration MUST include domain + source — without those, the gate
    # rejects an empty-known-failures claim as a false-clean (the #13
    # [00:13] class). Optional fields: if the reviewer isn't asserting a
    # baseline, just don't pass them.
    if getattr(args, "baseline_domain", None) or \
       getattr(args, "baseline_source", None) or \
       getattr(args, "baseline_known_failures", None) is not None:
        bl = {}
        if getattr(args, "baseline_domain", None):
            bl["domain"] = args.baseline_domain
        if getattr(args, "baseline_source", None):
            bl["source"] = args.baseline_source
        if getattr(args, "baseline_known_failures", None) is not None:
            bl["known_failures_count"] = int(args.baseline_known_failures)
        entry["baseline"] = bl

    # v0.18.0 F §3.5 companion #2: parse pytest skip information from the
    # captured output. The flag-heuristic (ground-truth check) runs gate-
    # side; here we just capture the data the heuristic needs. Format
    # recognized: `SKIPPED [N] <path>:<line>: <reason>` and the variant
    # `<test_id> SKIPPED (<reason>)` from pytest -v output.
    if kind == "tests":
        skips = _parse_pytest_skips(combined)
        if skips:
            entry["skips"] = skips

    reports_dir = root / "reports" / args.feature
    reports_dir.mkdir(parents=True, exist_ok=True)
    ev_path = schema.evidence_path_for(reports_dir, args.phase)

    # v0.20.0: `--reset` discards prior evidence entries for this phase
    # before appending. Adopter footgun closure (live-cc [09:07] B): a
    # stale exit-1 entry coexists with a later exit-0 entry and silently
    # poisons the every-entry-exit-0 gate check; the only recovery was
    # manual rm of the evidence file. --reset makes it discoverable.
    if getattr(args, "reset", False):
        if ev_path.exists():
            prior_count = len(schema.load_evidence(ev_path))
            print(f"[prusik-gate] --reset: discarding {prior_count} prior "
                  f"evidence entr{'y' if prior_count == 1 else 'ies'} "
                  f"for {args.phase}.")
        prior = []
    else:
        # Keep only current-build entries (drop stale prior-build evidence
        # so a rebuild cannot ride a previous build's numbers), then append.
        prior = [e for e in schema.load_evidence(ev_path)
                 if isinstance(e, dict) and e.get("worktree_hash") == wt_hash]
    prior.append(entry)
    tmp = ev_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(
        {"schema_version": schema.EVIDENCE_SCHEMA_VERSION, "entries": prior},
        indent=2))
    import os as _os
    _os.replace(tmp, ev_path)

    ok = exit_code == 0 and value > 0
    ledger.append("reviewer_execution_verified", feature=args.feature,
                   phase=args.phase, exit_code=exit_code,
                   nonempty=value, kind=kind, ok=ok)
    print(f"\n[prusik-gate] evidence recorded: {args.phase} "
          f"exit={exit_code} {kind}={value} → {ev_path.name}")

    # v0.18.0: surface flagged skips at capture time so the reviewer sees
    # them in their session (alongside the regression.txt output) — not
    # only at advance time. The flag is decision support, NOT a gate-block.
    if entry.get("skips"):
        flagged = _falsifiable_skip_reasons(root, entry["skips"])
        if flagged:
            print(f"\n[prusik-gate] skip-reason ground-truth flag — "
                  f"{len(flagged)} skip(s) name things present in the repo:")
            for sk in flagged:
                print(f"  ⚠ {sk['test_id']}: {sk['reason']}")
                print(f"     reason cites: {sk['cites']} (present in repo)")
            print("\n  Adjudicate honest-forward-skip vs masking-skip "
                  "before accepting the PASS. Prusik mechanizes the "
                  "flag; the call is yours (mission boundary).")
            for sk in flagged:
                ledger.append("reviewer_skip_flagged", feature=args.feature,
                              phase=args.phase, test_id=sk["test_id"],
                              reason=sk["reason"], cites=sk["cites"])
    return exit_code


# ---- v0.18.0 F §3.5 companion #2 — pytest skip parsing + ground-truth flag ----

import re as _re_skip


def _parse_pytest_skips(text: str) -> list[dict]:
    """Extract SKIPPED test info from pytest output. Two formats handled:

      `SKIPPED [N] path/to/test.py:42: reason text`
      `path/to/test.py::test_name SKIPPED (reason text)`

    Returns list of {test_id, reason, location} dicts. Best-effort —
    pytest output format varies by version + flags.
    """
    skips: list[dict] = []
    seen: set[str] = set()
    # Format 1: SKIPPED [N] path:line: reason
    for m in _re_skip.finditer(
            r"SKIPPED\s*(?:\[\d+\])?\s+(\S+\.py)(?::(\d+))?\s*:\s*(.+)",
            text):
        path, line, reason = m.group(1), m.group(2) or "", m.group(3).strip()
        test_id = f"{path}:{line}" if line else path
        if test_id not in seen:
            seen.add(test_id)
            skips.append({"test_id": test_id, "reason": reason,
                          "location": f"{path}:{line}" if line else path})
    # Format 2: test_id SKIPPED (reason)
    for m in _re_skip.finditer(
            r"(\S+\.py::\S+)\s+SKIPPED\s*\((.+?)\)",
            text):
        test_id = m.group(1)
        if test_id not in seen:
            seen.add(test_id)
            reason = m.group(2).strip()
            skips.append({"test_id": test_id, "reason": reason,
                          "location": test_id.split("::", 1)[0]})
    return skips


# Words / phrases in a skip reason that strongly assert ABSENCE — these
# are the high-signal flags. A skip "requires postgres" is environmental;
# a skip "not yet wired" / "TODO X" / "awaiting Y" / "X not implemented"
# is an absence-claim worth ground-truth-checking.
_ABSENCE_PHRASES = (
    r"\bnot\s+yet\s+(?:wired|implemented|landed|built|added)\b",
    r"\b(?:awaiting|pending)\s+\S+",
    r"\bTODO\b.*",
    r"\bFIXME\b.*",
    r"\b(?:un|not\s+)implemented\b",
    r"\bnot\s+available\b",
)


def _extract_groundtruth_candidates(reason: str) -> list[str]:
    """Pull file paths + identifier candidates from a skip reason that
    could be ground-truth-checked against the repo. Conservative: only
    extracts strings that look like code references (paths with
    extensions, dotted identifiers, CamelCase symbols >= 4 chars)."""
    candidates: list[str] = []
    # File paths with code extensions
    for m in _re_skip.finditer(
            r"\b[\w/.-]+\.(?:py|js|ts|tsx|jsx|html|jinja2?|css|rs|go|java|kt|swift)\b",
            reason):
        candidates.append(m.group(0))
    # Dotted module / class identifiers (snake_case or CamelCase)
    for m in _re_skip.finditer(
            r"\b(?:[a-z][a-z0-9_]{3,}|[A-Z][a-zA-Z0-9_]{3,})(?:\.\w+)*\b",
            reason):
        tok = m.group(0)
        # Filter out common English words that match the regex
        if tok.lower() in {"requires", "missing", "waiting", "pending",
                           "skipped", "needs", "wired", "implemented",
                           "broken", "available", "configured", "enabled",
                           "disabled", "should", "would", "could", "feature",
                           "function", "method", "class", "module", "test",
                           "tests", "skip", "skipping", "currently", "until",
                           "before", "after", "during", "between"}:
            continue
        if len(tok) >= 4:
            candidates.append(tok)
    return candidates


def _falsifiable_skip_reasons(root: Path, skips: list[dict]) -> list[dict]:
    """For each skip whose reason explicitly asserts ABSENCE, grep the
    worktree (excluding tests/) for ground-truth references. If found,
    flag the skip — the reason claims something is absent that's present.
    Prusik mechanizes the FLAG; the operator adjudicates
    honest-forward-skip (genuinely unbuilt) vs masking-skip (covers a real
    bug). Returns flagged skips with the cite that triggered the flag."""
    flagged: list[dict] = []
    for sk in skips:
        reason = sk.get("reason", "")
        if not any(_re_skip.search(p, reason, _re_skip.IGNORECASE)
                   for p in _ABSENCE_PHRASES):
            continue  # No absence-claim → no flag (environmental skips OK)
        candidates = _extract_groundtruth_candidates(reason)
        for cand in candidates:
            # Search repo excluding tests/ + scratch dirs; if the candidate
            # is found anywhere, the skip is ground-truth-falsifiable.
            found = _grep_repo(root, cand)
            if found:
                sk_flagged = dict(sk)
                sk_flagged["cites"] = cand
                sk_flagged["cite_locations"] = found[:3]
                flagged.append(sk_flagged)
                break  # one flag per skip is enough; don't double-list
    return flagged


def _grep_repo(root: Path, needle: str) -> list[str]:
    """Ground-truth lookup for a skip-reason candidate. Two paths:

    1. File-path-shaped candidate (matches `*.ext`) → search by *file
       existence* using `find -name <basename>`. A skip saying
       "mobile_capture.py not yet wired" is ground-truth-falsified by the
       FILE existing in the repo, not by some other file containing the
       string "mobile_capture.py".
    2. Symbol candidate (no extension) → content grep. A skip saying
       "FooBar not yet wired" is falsified by source containing
       references to FooBar.

    Excludes scratch + tests/ + build dirs in both modes. Returns up to 5
    matched paths."""
    import subprocess as _sp
    if not needle or any(ch in needle for ch in "*?[]\\$`"):
        return []  # Unsafe — would break literal lookup
    skip_dirs = (".sprint", "reports", "worktrees", ".git", "tests",
                 "test", "__pycache__", "node_modules", "dist", "build",
                 ".pytest_cache", ".mypy_cache", ".ruff_cache", ".runtime")
    # File-path-shaped?
    is_path = "." in needle and _re_skip.match(
        r"^[\w/.-]+\.(?:py|js|ts|tsx|jsx|html|jinja2?|css|rs|go|java|kt|swift)$",
        needle)
    if is_path:
        basename = needle.rsplit("/", 1)[-1]
        args = ["find", str(root), "-type", "f", "-name", basename]
        for d in skip_dirs:
            args += ["-not", "-path", f"*/{d}/*"]
    else:
        args = ["grep", "-rIl"]
        for d in skip_dirs:
            args += ["--exclude-dir", d]
        args += [needle, str(root)]
    try:
        out = _sp.run(args, capture_output=True, text=True, timeout=10,
                       check=False).stdout
    except (OSError, _sp.TimeoutExpired):
        return []
    matches = [ln for ln in out.splitlines() if ln.strip()]
    return matches[:5]


def _baseline_covers_failure(e: dict, root: Path) -> dict | None:
    """field bridge #3: decide whether a FAILING tests capture is an HONESTLY
    DECLARED pre-existing baseline rather than a false-clean to reject. Returns
    acceptance info if all guards hold, else None (→ the gate still blocks).

    The integrity anchor is the git-stash-PROVEN store (.sprint/known-failures
    .json): `baseline prove` REFUSES to record a failure that passes on HEAD, so
    a declared count alone can't launder a NEW failure. Guards, all required:
      • kind == tests (failed-count is parseable; other kinds stay hard-blocked);
      • baseline declared WITH domain + source (provenance, auditable);
      • observed_failures > 0 and ≤ the declared known_failures_count
        (no MORE failures than declared — a new failure pushes observed over and
        re-blocks);
      • ≥ observed ACTIVE proven entries exist (the declaration is backed by
        real git-stash proofs, not a typed number).
    Lets a non-deselectable, harness-level pre-existing failure be declared
    instead of DROPPED from evidence (which is what the dead-end nudged toward)."""
    from datetime import date

    np = e.get("nonempty_primitive", {})
    if np.get("kind") != "tests":
        return None
    bl = e.get("baseline")
    if not (isinstance(bl, dict) and bl.get("domain") and bl.get("source")):
        return None
    kf = bl.get("known_failures_count")
    observed = e.get("observed_failures")
    if not isinstance(kf, int) or kf <= 0:
        return None
    if not isinstance(observed, int) or observed <= 0 or observed > kf:
        return None
    from prusik import baseline as _baseline
    proven = len(_baseline.active(_baseline.load(root), date.today()))
    if proven < observed:
        return None
    return {"observed": observed, "declared": kf, "proven": proven,
            "domain": bl.get("domain"), "source": bl.get("source")}


def _evidence_unsatisfied(rel_path: str, feature: str | None,
                          root: Path) -> str | None:
    """Return None if the execution-evidence for this artifact satisfies the
    PASS claim, else a human reason string. Closes R1 (errored phase →
    exit≠0), R2 / auto-skip / declared-empty (primitive ≤ 0), and stale
    (evidence not bound to the current worktree). Coherent with v0.11.0 #1
    carry-forward: a verdict carried at the current hash was evidence-
    validated when first recorded — re-requiring it would defeat the
    dominant-cost cure, so a carried paired verdict satisfies."""
    name = Path(rel_path).name
    phase = name.split(".evidence.json")[0]
    abs_path = root / rel_path
    paired_txt = abs_path.parent / f"{phase}.txt"

    # Carry-forward coherence: a carried paired verdict at the current hash
    # already passed evidence when first recorded (v0.11.0 #1).
    if paired_txt.exists() and "carried forward" in paired_txt.read_text():
        ledger.append("reviewer_execution_verified", feature=feature,
                       phase=phase, carried=True, ok=True)
        return None

    if not abs_path.exists():
        return ("no execution-evidence — reviewer must run its suite via "
                "`prusik gate capture --feature {f} --phase {p} --kind <k> -- "
                "<cmd>`".format(f=feature, p=phase))
    ok, errs = schema.validate_evidence_file(abs_path)
    if not ok:
        return f"invalid evidence manifest: {'; '.join(errs)}"

    role = _PHASE_ROLE.get(phase, "")
    cur = _worktree_substantive_hash(root, _REVIEWER_INPUTS.get(role, ()))
    for e in schema.load_evidence(abs_path):
        np = e.get("nonempty_primitive", {})
        if e.get("worktree_hash") != cur:
            return (f"stale — evidence captured against "
                    f"{e.get('worktree_hash')}, code now {cur} (re-run "
                    f"`prusik gate capture`)")
        if e.get("exit_code") != 0:
            # field bridge #3: a FAILING capture can be an honestly-declared
            # pre-existing baseline rather than a false-clean — but ONLY when the
            # failures are git-stash-proven pre-existing AND bounded (see helper).
            if e.get("coverage_gate_only"):
                # fb-6a573cfe59fb: all tests PASSED; the nonzero exit is a coverage
                # threshold unmet on a SCOPED subset, not a test failure. Block (the
                # exit is real) but diagnose truthfully — and make the honest path the
                # easy one: drop the package-wide coverage gate from this scoped run.
                return (f"phase {e.get('phase')!r} exited "
                        f"{e.get('exit_code')} but ZERO tests failed — the nonzero exit "
                        f"is a COVERAGE threshold (`--cov-fail-under`) unmet on a SCOPED "
                        f"subset, NOT a test failure. A package-wide coverage gate is "
                        f"structurally meaningless on a deliberate subset. Re-capture "
                        f"this behavior run with the coverage gate removed (e.g. "
                        f"`--no-cov` / `-p no:cov` / `--cov-fail-under=0`) — the behavior "
                        f"capture proves the tests PASS; the coverage %% gate belongs on "
                        f"the FULL suite, which prusik enforces separately.")
            cov = _baseline_covers_failure(e, root)
            if cov is None:
                return (f"phase {e.get('phase')!r} claims PASS but its captured "
                        f"exit_code={e.get('exit_code')} (false-clean: errored "
                        f"phase scored clean). If these are PRE-EXISTING failures, "
                        f"git-stash-prove each via `prusik gate baseline prove` and "
                        f"declare `--baseline-known-failures N --baseline-domain D "
                        f"--baseline-source S` on capture — an inline count alone "
                        f"can't satisfy the gate (it would launder new failures).")
            ledger.append("baseline_known_failures_accepted", feature=feature,
                           phase=e.get("phase"), observed=cov["observed"],
                           declared=cov["declared"], proven=cov["proven"],
                           domain=cov["domain"], source=cov["source"])
            continue   # accepted as a proven, bounded, declared baseline
        if not isinstance(np.get("value"), int) or np.get("value") <= 0:
            kind = np.get("kind")
            base = (f"phase {e.get('phase')!r} PASS but {kind}={np.get('value')} — "
                    f"nothing measurable ran (false-clean: empty/auto-skipped phase "
                    f"scored clean).")
            # fb-b587d8d9b71c — make the honest path easy: a genuinely-clean run that
            # produced NO count (silent tsc, wrong test path) reads identically to a
            # no-op, so steer the fix per kind instead of a flat "nothing executed".
            if kind == "types":
                hint = (" If the typecheck is genuinely clean it ran SILENTLY with no "
                        "count — re-capture with a files-counting flag (tsc: "
                        "`--extendedDiagnostics`; mypy prints its own count) so a clean "
                        "run is distinguishable from a no-op.")
            elif kind == "tests":
                hint = (" 0 tests executed — a wrong path, an auto-skip, or no "
                        "collection. Point the command at real tests and re-capture.")
            else:
                hint = (" Re-capture a command that does real work, with a runner that "
                        "is LOUD on an empty scope (ruff/eslint count files).")
            return base + hint
        # v0.18.0 F §3.5 companion #1: empty baseline without declared
        # scope is itself a false-clean (the #13 [00:13] class — empty
        # known_failures declared from a structurally-blind context).
        # Schema enforces the shape when baseline is declared; here the
        # gate enforces the SUBSTANTIVE rule: an empty-known-failures
        # claim with declared scope is acceptable (operator declared the
        # domain + source); an empty-known-failures claim with NO
        # baseline declaration AT ALL when the project ships a baseline
        # file is a different layer (out of scope — the project's policy).
        # The mechanizable rule for prusik: if baseline is declared and
        # known_failures_count == 0, the declaration must include both
        # domain AND source (schema enforces this — defense in depth).
        bl = e.get("baseline")
        if bl is not None and isinstance(bl, dict):
            kf = bl.get("known_failures_count")
            if kf == 0 and (not bl.get("domain") or not bl.get("source")):
                return (f"phase {e.get('phase')!r} declares empty baseline "
                        f"(known_failures_count=0) WITHOUT declared "
                        f"domain+source — false-clean class: empty baseline "
                        f"from structurally-blind context (#13 [00:13])")
    ledger.append("reviewer_execution_verified", feature=feature,
                   phase=phase, ok=True,
                   nonempty=sum(e.get("nonempty_primitive", {}).get("value", 0)
                                for e in schema.load_evidence(abs_path)))
    return None


def brief(args) -> int:
    ok, errors = schema.validate_brief(args.path)
    if ok:
        print(f"[prusik-gate] brief valid: {args.path}")
        return 0
    print(f"[prusik-gate] brief INVALID: {args.path}", file=sys.stderr)
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    return 2


def baseline(args) -> int:
    """Known-failure baselines (v0.73.0, field finding #4) — prove / list / prune /
    deselect-args. Dispatches to the baseline module."""
    from prusik import baseline as _baseline
    return _baseline.run(args.action, feature=getattr(args, "feature", None),
                         test=getattr(args, "test", None),
                         command=getattr(args, "command", None),
                         days=getattr(args, "days", _baseline.DEFAULT_DAYS),
                         runs=getattr(args, "runs", _baseline._DEFAULT_FLAKY_RUNS))


def scope(args) -> int:
    ok, errors = schema.validate_scope(args.path, ledger.project_root())
    if ok:
        print(f"[prusik-gate] scope valid: {args.path}")
        return 0
    print(f"[prusik-gate] scope INVALID: {args.path}", file=sys.stderr)
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    return 2


def plan(args) -> int:
    root = ledger.project_root()
    ok, errors = schema.validate_plan(args.path, root)
    if ok:
        print(f"[prusik-gate] plan valid: {args.path}")
    else:
        print(f"[prusik-gate] plan INVALID: {args.path}", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
    # v0.63.0 — plan-time blast-radius advisory (field finding #2). Non-blocking SIGNAL:
    # tests outside the plan's touched set that reference contracts it changes,
    # so the planner catches a ripple at plan time, not a fix-round later. Never
    # alters rc; on failure it says so (not a silent skip).
    try:
        from prusik import blast_plan
        feature = Path(args.path).parent.name
        adv = blast_plan.advisory(feature, root)
        if adv:
            print(adv)
    except Exception as e:  # noqa: BLE001 — advisory must never break the gate
        print(f"[prusik-gate] (plan-reach advisory skipped: {e})",
              file=sys.stderr)
    # v0.76.0 — staffing advisory (finding #12): a proposed role with no shipped
    # agent silently falls back to a manual stand-in. Flag it before the build.
    try:
        from prusik import roles
        adv = roles.advisory(Path(args.path).parent.name, root)
        if adv:
            print(adv)
    except Exception as e:  # noqa: BLE001 — advisory must never break the gate
        print(f"[prusik-gate] (staffing advisory skipped: {e})", file=sys.stderr)
    return 0 if ok else 2


def fix_round(args) -> int:
    """Dispatch `prusik gate fix-round {start,end}`.

    Fix rounds (v0.5.7) temporarily expand reviewing-phase writable scope
    to include `worktrees/*/**` so builders can patch reviewer-found
    defects. Hard cap: 2 rounds per sprint (third FAIL escalates).
    """
    from prusik import fix_round as _fix_round
    if args.fix_round_cmd == "start":
        return _fix_round.start(args.feature)
    if args.fix_round_cmd == "end":
        return _fix_round.end(args.feature)
    if args.fix_round_cmd == "status":
        return _fix_round.status()
    if args.fix_round_cmd == "escalate":
        return _fix_round.escalate(args.feature, args.decision, args.rationale,
                                   auto=getattr(args, "auto", False))
    if args.fix_round_cmd == "classify":
        return _fix_round.classify(
            args.feature, test_fixable=args.test_fixable,
            source_defect=args.source_defect, pre_existing=args.pre_existing,
            note=args.note)
    print(f"[prusik-gate] unknown fix-round subcommand: {args.fix_round_cmd}",
          file=sys.stderr)
    return 1


def verify_reviewer(args) -> int:
    """Cross-check reviewer artifacts against the ledger for fabricated
    Bash-denial claims (v0.8.2, B26).

    Standalone path so the operator can verify on demand without opening
    a fix-round. The same detector also fires automatically from
    `prusik gate fix-round start` and from `prusik gate advance` when leaving
    reviewing with unmet exit artifacts. Always exit 0 — informational,
    not blocking.
    """
    feature = args.feature
    suspects = consistency.detect_reviewer_fabrication(
        ledger.project_root(), feature
    )
    if not suspects:
        print(
            f"[prusik-gate] verify-reviewer: no fabrication signals for "
            f"feature '{feature}'. Reviewer artifacts (if any) are either "
            f"PASS, do not claim Bash deny, or quote `[prusik-gate]` messages "
            f"that match real ledger events."
        )
        return 0
    consistency.emit_fabrication_warnings(suspects)
    return 0


# --- helpers ---

_DRIFT_STOPWORDS = frozenset({
    "update", "create", "delete", "remove", "enable", "disable", "refactor",
    "migrate", "improve", "support", "cleanup", "rename", "change", "adjust",
    "handle", "system", "common", "shared", "module", "feature", "sprint",
    "should", "within", "before", "after", "depth",
})


def _feature_drift_terms(feature: str, root) -> list[str]:
    """Distinctive subsystem stems for `feature`, used to feature-scope map-drift
    (fb-76ff51b273de). The feature SLUG is the operator's deliberate name for the
    subsystem it extends — the lowest-noise signal; the brief's Goal adds a few more.
    Each token ≥6 chars (after a generic-dev-word stoplist) contributes a 6-char prefix
    stem, so a slug `categorization-…` yields `catego` and matches drifted paths like
    CategoryPicker / useCategories / categorization-types. Short/generic tokens are
    dropped: a MISSED match falls back to the global-% and age floors, but a SPURIOUS
    match would fail a sprint wrongly — so the bar errs toward precision."""
    tokens = set(re.split(r"[-_/]+", feature.lower()))
    brief = root / "briefs" / f"{feature}.md"
    if brief.exists():
        try:
            goal = schema.parse_sections(brief.read_text()).get("## Goal", "")
            tokens |= set(re.findall(r"[a-z]{4,}", goal.lower()))
        except OSError:
            pass
    return sorted({tok[:6] for tok in tokens
                   if len(tok) >= 6 and tok not in _DRIFT_STOPWORDS})


def _check_pre_sprint_gates(config: dict, feature: str, root) -> list[str]:
    """Returns a list of unmet gate messages; empty if all pass."""
    gates = config.get("pre_sprint_gates", {}) or {}
    unmet: list[str] = []
    for gate_name, gate_spec in gates.items():
        if not gate_spec.get("enabled", True):
            continue

        check_type = gate_spec.get("check")
        if check_type == "map_freshness":
            drift = discovery.map_drift(root)
            if drift is None:
                hint = gate_spec.get("hint",
                    "no map-fingerprint yet — run cartographer then `prusik discovery fingerprint-map`")
                unmet.append(f"[{gate_name}] {hint}")
                continue
            # FEATURE-SCOPED staleness (the decisive check): a change INSIDE this
            # feature's own subsystem fails REGARDLESS of the global %, because scoping
            # would read a stale map of exactly what it scopes. Global drift % dilutes a
            # single-subsystem change below threshold (the live miss). Independent of age.
            hits = discovery.feature_scoped_drift(
                root, _feature_drift_terms(feature, root))
            if hits:
                hint2 = gate_spec.get("scoped_hint",
                    "re-run cartographer, then `prusik discovery fingerprint-map`")
                unmet.append(
                    f"[{gate_name}] map predates changes in {hits[:6]} — the "
                    f"'{feature}' subsystem drifted since the map was generated "
                    f"(feature-scoped staleness, independent of the "
                    f"{drift['drift_pct']}% global drift). {hint2}")
                continue   # decisive — don't also report the (passing) global %
            max_pct = float(gate_spec.get("max_drift_pct", 30))
            if drift["drift_pct"] > max_pct:
                hint = gate_spec.get("hint",
                    "refresh design/map.md — codebase has drifted since it was written")
                unmet.append(
                    f"[{gate_name}] drift {drift['drift_pct']}% "
                    f"(+{drift['added_count']} / -{drift['removed_count']} modules) "
                    f"exceeds {max_pct}% — {hint}"
                )
            continue

        if check_type == "behavior_regression":
            # Only enforces when the project has declared the contract:
            # top-level `behavior_regression.enabled: true` means "this project
            # promises to keep a behavior-regression suite." If that flag is
            # absent or false, the gate is a no-op — the project hasn't opted
            # in. When the flag IS set, the suite directory must contain at
            # least one matching test file; otherwise the carve-out is
            # silently empty and the regression-sentinel command runs against
            # nothing. (See behavior-tests bridge, B22-adjacent integration.)
            br = (config.get("behavior_regression") or {})
            if not br.get("enabled"):
                continue
            test_dir_rel = gate_spec.get("test_dir", "tests/behavior")
            pattern = gate_spec.get("pattern", "test_*.py")
            test_dir = root / test_dir_rel
            matches: list = []
            if test_dir.is_dir():
                matches = list(test_dir.rglob(pattern))
            if not matches:
                hint = gate_spec.get("hint",
                    f"behavior_regression.enabled is true but {test_dir_rel}/ "
                    f"contains no '{pattern}' files — add at least one or "
                    f"set behavior_regression.enabled: false")
                unmet.append(f"[{gate_name}] {hint}")
            continue

        artifact_tpl = gate_spec.get("require_artifact")
        if not artifact_tpl:
            continue
        path = phases.resolve_path(artifact_tpl, feature=feature)
        abs_path = root / path
        if not abs_path.exists():
            hint = gate_spec.get("hint", f"produce {path} before starting")
            unmet.append(f"[{gate_name}] missing {path} — {hint}")
            continue
        must_contain = gate_spec.get("must_contain")
        if must_contain and must_contain not in abs_path.read_text():
            unmet.append(f"[{gate_name}] {path} must contain {must_contain!r}")
    return unmet


def _recover_predicted(root, feature: str) -> dict:
    """Reconstruct the predicted sprint metadata from triage decision + ledger."""
    import json as _json
    decision_path = root / "decisions" / f"{feature}.json"
    predicted: dict = {}
    if decision_path.exists():
        try:
            dec = _json.loads(decision_path.read_text())
            predicted["mode"] = dec.get("mode")
            predicted["size"] = dec.get("scope_summary", {}).get("size")
            predicted["domains"] = dec.get("scope_summary", {}).get("domains")
        except Exception:
            pass
    # Duration estimate from sprint_started → now (ledger)
    from prusik.ledger import read_all
    from datetime import datetime as _dt, timezone as _tz
    started_at = None
    for r in read_all():
        if r.get("event") == "sprint_started" and r.get("feature") == feature:
            started_at = r.get("ts")
    if started_at:
        try:
            start = _dt.fromisoformat(started_at)
            # tz-aware UTC; mirror start's awareness so the subtraction
            # never mixes offset-naive and offset-aware datetimes. (Was
            # datetime.utcnow() — deprecated/removal-slated in 3.12+.)
            now = _dt.now(_tz.utc) if start.tzinfo \
                else _dt.now(_tz.utc).replace(tzinfo=None)
            predicted["duration_min"] = int((now - start).total_seconds() / 60)
        except Exception:
            pass
    return predicted


def _try_carry_forward(path: str, feature: str | None, root: Path) -> bool:
    """v0.10.0 Fix 3: if `path` is a content-judgment approval whose source
    is substantively unchanged since the last APPROVED verdict, regenerate
    the approval file from the ledger and report it satisfied — no critic
    re-dispatch. Returns True iff carry-forward succeeded."""
    if feature is None:
        return False
    marker = _approval_carried_forward(path, feature, root)
    if not marker:
        return False
    abs_path = root / path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(marker + "\n")
    ledger.append("verdict_carried_forward", feature=feature, approval=path)
    return True


def _unsatisfied_exit_artifacts(phase_spec: dict, feature: str | None) -> list[str]:
    root = ledger.project_root()
    missing: list[str] = []
    # v0.11.0 #2: lane-aware exit artifacts. A phase may declare a
    # `trivial_exit_artifacts` set used only when the active sprint is in
    # the trivial lane (scoping uses this to require a lightweight
    # trivial.md instead of full scope.md + scope-critic APPROVED). The
    # reviewing phase declares no trivial variant, so its correctness floor
    # (regression + conventions PASS) applies to trivial sprints unchanged.
    lane = (phases.current_sprint_state() or {}).get("lane")
    if lane == "trivial" and phase_spec.get("trivial_exit_artifacts"):
        exit_list = phase_spec["trivial_exit_artifacts"]
    else:
        exit_list = phase_spec.get("exit_artifacts", [])
    for artifact in exit_list:
        path = phases.resolve_path(artifact["path"], feature=feature)
        abs_path = root / path
        validator = artifact.get("validator")
        # v0.12.0 (F): execution-evidence fully decides on its own (it
        # tolerates a missing file iff the paired verdict carried forward),
        # so handle it before the generic exists/carry-forward path.
        if validator == "execution_evidence":
            reason = _evidence_unsatisfied(path, feature, root)
            if reason:
                missing.append(f"{path} (execution-evidence: {reason})")
            continue
        if not abs_path.exists():
            if _try_carry_forward(path, feature, root):
                continue
            missing.append(f"{path} (does not exist)")
            continue
        if validator == "brief_schema":
            ok, errs = schema.validate_brief(abs_path)
            if not ok:
                missing.append(f"{path} (schema: {'; '.join(errs)})")
                continue
        elif validator == "scope_schema":
            ok, errs = schema.validate_scope(abs_path, root)
            if not ok:
                missing.append(f"{path} (schema: {'; '.join(errs)})")
                continue
        elif validator == "triage_decision":
            ok, errs = schema.validate_triage_decision(abs_path)
            if not ok:
                missing.append(f"{path} (schema: {'; '.join(errs)})")
                continue
        for sec in artifact.get("required_sections", []):
            if sec not in abs_path.read_text():
                missing.append(f"{path} (missing section: {sec})")
        mc = artifact.get("must_contain")
        if mc and mc not in abs_path.read_text():
            if _try_carry_forward(path, feature, root):
                continue
            missing.append(f"{path} (must contain: {mc!r})")
    return missing


# ============================================================
# v0.19.0 — `prusik gate check-bindings` (Item 2)
# ============================================================
#
# Closes the F §4 assertion-depth gap surfaced by DEV-1 (the trial's
# strongest motivating bug):
#   - Template fetches a URL that doesn't resolve to any touched route
#     (#1: template-fetch-URL ↔ route-path; 404 risk).
#   - Template emits <input name="X"> that no touched handler reads
#     (#2: form-name ↔ handler-key; silent dropthrough).
#
# Per the design pass:
# FLAG only, NOT a gate-block. Prusik mechanizes the binding-mismatch
# detection; adjudicating whether a particular flagged binding is a real
# bug or an intentional cross-module call needs project context the
# static scan can't resolve (mission boundary — same discipline as the
# v0.18.0 skip-reason flag). Reviewer/operator decides.
#
# v0.19.0 ships FastAPI+Jinja extractors (field-grounded). Other stacks
# queued behind a 2nd recurrence outside FastAPI.

def _check_local_detectors(root: Path, touched_files: list, feature: str) -> None:
    """Run project-local (non-built-in) detectors over the touched set and log
    each result as a `detector_flagged` ledger event, so a team's custom
    detector surfaces in `prusik metrics` / `prusik findings`. Best-effort —
    never raises into the gate."""
    try:
        from prusik import detectors as _detreg
        from prusik.detectors.base import ScanContext
        from prusik.scan import _detector_config
        cfg = _detector_config(root)
        registry = _detreg.load(root, cfg)
    except Exception:
        return
    extras = {n: m for n, m in registry.items()
              if n not in ("binding", "test-reach")}
    if not extras:
        return
    ctx = ScanContext(root=root, files=touched_files, config=cfg)
    flagged = 0
    for name in sorted(extras):
        try:
            results = extras[name].detect(ctx)
        except Exception as e:  # one bad detector must not break the check
            print(f"[prusik-gate] detector {name!r} errored: {e}", file=sys.stderr)
            continue
        for f in results:
            ledger.append("detector_flagged", feature=feature,
                           detector=f.detector, cls=f.cls, severity=f.severity,
                           summary=f.message, file=f.file, line=f.line)
            flagged += 1
    if flagged:
        print(f"[prusik-gate] check-bindings: {flagged} custom-detector flag(s) "
              f"from {', '.join(sorted(extras))} → ledger (see `prusik metrics`).")


def check_bindings(args) -> int:
    """Scan touched files in worktrees/ for binding mismatches; emit
    findings as `reviewer_binding_flagged` ledger events + human-readable
    output. Always exit 0 — informational, not gating.

    Usage: prusik gate check-bindings --feature F [--touched-set <path>...]
    """
    from prusik.binding_check import find_unbinding_pairs

    root = ledger.project_root()
    feature = args.feature

    # Determine touched-set. Three sources, in order:
    #   1. Explicit --touched-set (CLI argument list)
    #   2. worktrees/* subtree (prusik's standard authoring location)
    #   3. None → exit clean (no work to check)
    touched_files: list[Path] = []
    explicit = getattr(args, "touched_set", None) or []
    if explicit:
        touched_files = [Path(p) for p in explicit]
    else:
        wt = root / "worktrees"
        if wt.exists():
            for p in wt.rglob("*"):
                if p.is_file():
                    touched_files.append(p)
    if not touched_files:
        print("[prusik-gate] check-bindings: no touched files (worktrees/ "
              "is empty or absent). Nothing to check.")
        return 0

    # Run any project-local (custom) detectors over the touched set too, so a
    # team's own contract checks reach the ledger → `prusik metrics` / findings.
    # Built-ins are handled below; this only runs the extras. (Runs regardless
    # of binding outcome, ahead of the no-binding-findings early return.)
    _check_local_detectors(root, touched_files, feature)

    findings = find_unbinding_pairs(touched_files, root)
    if not findings:
        print(f"[prusik-gate] check-bindings: {len(touched_files)} touched "
              f"file(s) scanned; no binding-mismatch flags raised.")
        return 0

    print(f"[prusik-gate] check-bindings: {len(findings)} binding flag(s) on "
          f"{len(touched_files)} touched file(s):\n")
    by_class: dict = {}
    for f in findings:
        by_class.setdefault(f["class"], []).append(f)

    if by_class.get("fetch_url"):
        print(f"  ── fetch-URL ↔ route-path mismatches "
              f"({len(by_class['fetch_url'])}): ──")
        for f in by_class["fetch_url"]:
            print(f"    ⚠ {f['template']}:{f['template_line']}  "
                  f"({f['kind']}) {f['url']!r}")
            if f["expected"]:
                print(f"        expected (per touched routes): "
                      f"{f['expected']}")
            print(f"        {f['msg']}")
            # v0.27.0 — suggested test scaffold
            sug = f.get("suggested_test")
            if sug:
                print("        ── suggested test (prusik v0.27.0) ──")
                for line in sug["code"].splitlines():
                    print(f"        {line}")
        print()

    if by_class.get("form_name"):
        print(f"  ── form-name ↔ handler-key dropthroughs "
              f"({len(by_class['form_name'])}): ──")
        for f in by_class["form_name"]:
            print(f"    ⚠ {f['template']}:{f['template_line']}  "
                  f"<input name={f['name']!r}>")
            print(f"        no touched handler reads {f['name']!r}; "
                  f"keys read in touched handlers: {f['expected']}")
            print(f"        {f['msg']}")
            sug = f.get("suggested_test")
            if sug:
                print("        ── suggested test (prusik v0.27.0) ──")
                for line in sug["code"].splitlines():
                    print(f"        {line}")
        print()

    print("  Adjudicate each flag — prusik detects the binding "
          "mismatch; deciding whether it's a real bug vs. an intentional "
          "cross-module call is project-context territory (mission "
          "boundary).")

    # Emit per-finding ledger events for traceability + later analysis
    # via `prusik doctor --insights`. The flag itself is informational; the
    # ledger trail is the authoritative record.
    for f in findings:
        ledger.append("reviewer_binding_flagged", feature=feature,
                       binding_class=f["class"],
                       template=f.get("template", ""),
                       url=f.get("url"), form_name=f.get("name"),
                       expected=f.get("expected", []))
    return 0


# ============================================================
# v0.20.0 — `prusik gate check-test-reach` (test-set-reach pre-check)
# ============================================================
#
# Closes the test-set-reach gap surfaced by m4-gate-domain-debt + the
# m4-suspect-skip-audit triple recurrence (3 instances in one sprint at
# the same root mechanism). Per prusik's recurrence discipline, ship
# on 2nd occurrence — now met (and exceeded).
#
# Reviewer-time SIGNAL (NOT gating). Surfaces tests outside the touched
# set that reference contracts inside the touched set — the kind of
# cross-touch-set assertion the worktree-partial-mirror cannot see.

def check_test_reach(args) -> int:
    """Scan tests outside the touched set for references to contracts
    the sprint touched. Flag-only — informational, not gating. Emits
    `reviewer_test_set_reach` ledger event per finding.

    Usage: prusik gate check-test-reach --feature F [--touched-set <path>...]
    """
    from prusik.test_reach import find_test_reach

    root = ledger.project_root()
    feature = args.feature

    touched_files: list[Path] = []
    explicit = getattr(args, "touched_set", None) or []
    if explicit:
        touched_files = [Path(p) for p in explicit]
    else:
        wt = root / "worktrees"
        if wt.exists():
            for p in wt.rglob("*"):
                if p.is_file():
                    touched_files.append(p)
    if not touched_files:
        print("[prusik-gate] check-test-reach: no touched files (worktrees/ "
              "is empty or absent). Nothing to check.")
        return 0

    findings = find_test_reach(touched_files, root)
    if not findings:
        print(f"[prusik-gate] check-test-reach: {len(touched_files)} touched "
              f"file(s) scanned; no out-of-set tests reference touched "
              f"contracts. Either the contracts are too new to have tests "
              f"OR existing tests live in your touched set OR coverage "
              f"genuinely doesn't reach yet — the post-integration "
              f"full-suite gate remains the load-bearing backstop.")
        return 0

    total_refs = sum(len(f["references"]) for f in findings)
    print(f"[prusik-gate] check-test-reach: {len(findings)} touched "
          f"contract(s) referenced by {total_refs} test(s) OUTSIDE the "
          f"touched set:\n")
    print("  These tests are STRUCTURALLY NOT LOADED by reviewing (the")
    print("  worktree-partial-mirror) — they fire only at the")
    print("  post-integration full-suite gate. Decide whether reviewing")
    print("  needs to pull them in OR rely on the backstop (which is")
    print("  load-bearing by design — see v0.12.0 §4).")
    print()
    for f in findings:
        print(f"  ⚠ {f['contract_kind']}: {f['contract_id']}")
        if f.get("file_hint"):
            print(f"        (touched in: {f['file_hint']})")
        for ref in f["references"]:
            print(f"        ↪ referenced by: {ref}")
        print()
        ledger.append("reviewer_test_set_reach", feature=feature,
                       contract_class=f["class"],
                       contract_id=f["contract_id"],
                       reference_count=len(f["references"]),
                       references=f["references"][:3])

    print("  These are SIGNALS, not gates. Adjudicate which (if any)")
    print("  warrant exercising at reviewing time — mission boundary.")
    return 0
