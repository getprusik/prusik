"""Which prusik runs in a project is a checked fact, not an accident of PATH or cwd
(fb-f381d16021db).

A project's pin is its deployed surface version: the version field of
`.claude/.prusik-manifest.json`, which `init` and `refresh` write and adopters commit. The
prusik source repo has no manifest; there the pin is the version in the repo's own
`prusik/__init__.py`, and only when `pyproject.toml` names the project `prusik`. A directory
with neither has no pin and is not checked. A manifest that is present but cannot be read is
not "no pin": it refuses.

`check` is the one verdict every entrypoint uses: pin, running version, origin, and whether
this process may act. `child_command` is the one way prusik launches a prusik child: the
child's env names the parent's package dir (`PRUSIK_ORIGIN`), and a child that imported a
different `prusik` refuses. `PYTHONSAFEPATH=1` keeps the cwd off `sys.path` on 3.11+; on
3.10 it is a no-op, and the origin check is the defence.

This module is on every hook's hot path, so it imports only what the hook already loads
(json, os, re, sys from the interpreter baseline, pathlib from the allowlist). It never
imports `manifest`, `refresh` or `version_check`; those delegate here for the two rules they
share (the surface-version field fallback and the version parse).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import prusik
from prusik import __version__

MANIFEST_REL = Path(".claude") / ".prusik-manifest.json"
ORIGIN_ENV = "PRUSIK_ORIGIN"
# Set by the launcher only when it added PYTHONSAFEPATH, so the child can hand its own
# children the environment the operator had (a captured test suite must keep its cwd import).
_SAFEPATH_ADDED_ENV = "PRUSIK_LAUNCH_SAFEPATH"

# Verdict statuses. UNPINNED / EQUAL / AHEAD_OPTED_OUT run; the rest refuse.
UNPINNED = "unpinned"
EQUAL = "equal"
BEHIND = "behind"
AHEAD = "ahead"
AHEAD_OPTED_OUT = "ahead-opted-out"
CORRUPT = "corrupt"
FOREIGN = "foreign"
_RUNS = frozenset({UNPINNED, EQUAL, AHEAD_OPTED_OUT})

_VER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_PROJECT_TABLE_RE = re.compile(r"^\[project\]\s*$(.*?)(?=^\[|\Z)", re.M | re.S)
_NAME_RE = re.compile(r"""^name\s*=\s*["']prusik["']\s*(#.*)?$""", re.M)
_SOURCE_VERSION_RE = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']""", re.M)


# ── the shared rules (manifest / version_check delegate here) ─────────────────────────

def parse_version(v: str | None) -> tuple[int, int, int] | None:
    """`X.Y.Z` (optionally `v`-prefixed) as an int tuple; None when it isn't one."""
    m = _VER_RE.match((v or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def surface_version(m: dict | None) -> str:
    """The version a deployed template surface corresponds to, through the legacy fields
    for a not-yet-migrated manifest; 'unknown' only when there is no evidence at all."""
    if not m:
        return "unknown"
    return (m.get("template_surface_version")
            or m.get("last_refresh_version")
            or m.get("kit_version") or "unknown")


# ── pin resolution ─────────────────────────────────────────────────────────────────

class PinUnreadable(Exception):
    """A pin file exists but no pin can be read from it."""

    def __init__(self, source: Path, why: str) -> None:
        super().__init__(f"{source}: {why}")
        self.source = source
        self.why = why


def resolve(root: Path) -> tuple[str | None, Path | None, str | None]:
    """(pin, the file it came from, kind) where kind is "manifest" or "source"; all None when
    the directory has no pin. Raises PinUnreadable for a pin file that exists but can't be
    read, so a corrupt manifest never degrades to unchecked."""
    mp = root / MANIFEST_REL
    try:
        text = mp.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _source_pin(root)
    except (OSError, UnicodeDecodeError) as e:
        raise PinUnreadable(mp, f"unreadable ({e.__class__.__name__}: {e})") from None
    try:
        m = json.loads(text)
    except ValueError as e:
        raise PinUnreadable(mp, f"not valid JSON ({e})") from None
    if not isinstance(m, dict):
        raise PinUnreadable(mp, "not a JSON object")
    v = surface_version(m)
    if parse_version(v) is None:
        raise PinUnreadable(mp, f"no X.Y.Z surface version (got {v!r})")
    return v, mp, "manifest"


def _source_pin(root: Path) -> tuple[str | None, Path | None, str | None]:
    try:
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, None, None
    table = _PROJECT_TABLE_RE.search(pyproject)
    if not table or not _NAME_RE.search(table.group(1)):
        return None, None, None
    init = root / "prusik" / "__init__.py"
    try:
        m = _SOURCE_VERSION_RE.search(init.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        raise PinUnreadable(init, f"unreadable ({e.__class__.__name__}: {e})") from None
    if not m or parse_version(m.group(1)) is None:
        raise PinUnreadable(init, "no X.Y.Z __version__")
    return m.group(1), init, "source"


# ── the verdict ────────────────────────────────────────────────────────────────────

def package_dir() -> str:
    """This process's prusik package directory: the origin a launched child must match."""
    return os.path.dirname(os.path.abspath(__file__))


class Verdict:
    __slots__ = ("status", "pin", "source", "kind", "running", "origin", "detail")

    def __init__(self, status: str, *, pin: str | None = None, source: Path | None = None,
                 kind: str | None = None, detail: str = "") -> None:
        self.status = status
        self.pin = pin
        self.source = source
        self.kind = kind
        self.running = __version__
        self.origin = os.path.abspath(prusik.__file__)
        self.detail = detail

    @property
    def runs(self) -> bool:
        return self.status in _RUNS

    def as_dict(self) -> dict:
        return {"status": self.status, "runs": self.runs, "pin": self.pin,
                "pin_source": str(self.source) if self.source else None,
                "running": self.running, "origin": self.origin}


def check(root: Path, environ=None) -> Verdict:
    """The verdict for this process acting in `root`: a foreign origin first (a launched child
    that imported another package), then the pin against the running version. AHEAD becomes
    AHEAD_OPTED_OUT only under the project's `auto_refresh_on_skew: false`."""
    env = os.environ if environ is None else environ
    want = env.get(ORIGIN_ENV)
    if want and os.path.realpath(want) != os.path.realpath(package_dir()):
        return Verdict(FOREIGN, detail=want)
    try:
        pin, source, kind = resolve(root)
    except PinUnreadable as e:
        return Verdict(CORRUPT, source=e.source, detail=e.why)
    if pin is None:
        return Verdict(UNPINNED)
    # resolve() returns only parseable pins. A running version that isn't X.Y.Z (a local dev
    # build) matches only its exact string, and otherwise refuses as AHEAD.
    p, r = parse_version(pin), parse_version(__version__)
    if (p == r) if r is not None else (pin == __version__):
        return Verdict(EQUAL, pin=pin, source=source, kind=kind)
    if r is not None and p is not None and r < p:
        return Verdict(BEHIND, pin=pin, source=source, kind=kind)
    status = AHEAD_OPTED_OUT if _opted_out(root) else AHEAD
    return Verdict(status, pin=pin, source=source, kind=kind)


def _opted_out(root: Path) -> bool:
    """The project's `auto_refresh_on_skew: false`. Read only on a mismatch, so the equal
    path never loads the YAML config; a config that can't be read is not an opt-out."""
    try:
        from prusik import phases
        cfg = phases.load_sprint_config(root) or {}
    except Exception:  # noqa: BLE001 — unreadable config: no opt-out, so the check refuses
        return False
    return isinstance(cfg, dict) and cfg.get("auto_refresh_on_skew") is False


def refusal(v: Verdict) -> str:
    """What a refused entrypoint prints: what runs, where from, the pin and its file, the fix."""
    running = f"this prusik is {v.running} (imported from {v.origin})"
    if v.status == FOREIGN:
        return (f"[prusik] refusing: {running}, but the prusik that launched it runs from "
                f"{v.detail} ({ORIGIN_ENV}). Another `prusik` package on sys.path (often a "
                f"prusik/ directory in the working directory) shadowed the launcher's.\n"
                f"  Fix: remove or rename the shadowing package, or run from a directory "
                f"that has none.")
    if v.status == CORRUPT:
        return (f"[prusik] refusing: {running}, but this project's pin file {v.source} "
                f"is {v.detail}.\n"
                f"  Fix: restore it (`git checkout -- {v.source}`), or re-scaffold with "
                f"`prusik init --force`.")
    pinned = f"this project is pinned to {v.pin} ({v.source})"
    where = "behind the pin: " if v.status == BEHIND else "ahead of the pin: "
    return f"[prusik] refusing: {running}, {where}{pinned}.\n  Fix: {install_fix(v)}"


def install_fix(v: Verdict) -> str:
    """The one fix for a version mismatch, shared by the refusal and doctor. A refused
    PreToolUse hook blocks the agent's own tools, so each fix is runnable with Claude Code's
    `!` prefix, which does not go through hooks."""
    if v.kind == "source" and v.source is not None:
        # The source repo's pin is a release version, so the fix is a released engine, never
        # an editable install of unreleased code (fb-c713dc6530a9).
        repo = v.source.parent.parent
        if v.status == BEHIND:
            return (f"this checkout is bumped to {v.pin} ahead of the installed engine. A "
                    f"release in progress resumes with `{repo}/scripts/release.sh` (push, "
                    f"release, publish, install); the pre-tool hook lets exactly that command "
                    f"through, alone on its line. A published {v.pin} installs with "
                    f"`! <the python on bare prusik's shebang> -m pip install prusik=={v.pin}`.")
        return (f"the installed engine is newer than this checkout; update it "
                f"(`! git -C {repo} pull`).")
    if v.status != BEHIND:
        return ("run `prusik refresh` from the terminal (`! prusik refresh` inside Claude "
                "Code), then restart Claude Code.")
    return (f"install the pinned version so bare `prusik` resolves to it "
            f"(`! uv tool install prusik=={v.pin}` or `! pipx install prusik=={v.pin}`).")


# A bare path: no shell metacharacter, quote, space or expansion can appear in it, so the
# command runs exactly that file and nothing else.
_BARE_PATH_RE = re.compile(r"[A-Za-z0-9_./-]+")


def release_resume(v: Verdict, payload: object) -> bool:
    """True when this PreToolUse payload is the one call a refused source repo lets through:
    a Bash command that is exactly the repo's scripts/release.sh, relative to the payload's
    cwd or absolute, with nothing before or after it. Only while the source pin is ahead of
    the installed engine, the state a release that stopped after its bump leaves behind
    (fb-b071cab60a5b); anything else would run unreleased code's tools on an older engine."""
    if v.status != BEHIND or v.kind != "source" or v.source is None:
        return False
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return False
    tool_input = payload.get("tool_input")
    cmd = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(cmd, str) or not _BARE_PATH_RE.fullmatch(cmd.strip()):
        return False
    cwd = payload.get("cwd")
    base = cwd if isinstance(cwd, str) and os.path.isabs(cwd) else str(v.source.parent.parent)
    script = v.source.parent.parent / "scripts" / "release.sh"
    return os.path.realpath(os.path.join(base, cmd.strip())) == os.path.realpath(script)


# ── a sprint never moves the pin (fb-dc6e3239ce99) ────────────────────────────────────

def _pin_in(text: str | None, kind: str) -> str | None:
    """The pin a pin file's text names, by the same rule `resolve` reads it with."""
    if text is None:
        return None
    if kind == "source":
        m = _SOURCE_VERSION_RE.search(text)
        return m.group(1) if m else None
    try:
        m = json.loads(text)
    except ValueError:
        return None
    return surface_version(m) if isinstance(m, dict) else None


def sprint_moves(root: Path, feature: str) -> list[dict]:
    """Each of `feature`'s worktrees whose change moves the project's pin: its sprint branch's
    tip names a different pin than the branch's fork point, or (no git) its mirror's pin file
    differs from the project's. The release owns the version; merged, a moved pin puts the
    checkout out of step with the installed engine and the pin gate refuses every tool call.
    Empty when the project has no pin or no worktree moves it."""
    try:
        _, source, kind = resolve(root)
    except PinUnreadable:
        return []                      # the pin gate itself refuses a corrupt pin
    if source is None or kind is None:
        return []
    rel = source.relative_to(root).as_posix()
    wts = sorted(d.name for d in (root / "worktrees").glob("*/"))
    if (root / ".git").exists():
        return _branch_moves(root, feature, wts, rel, kind)
    moves = []
    want = _pin_in(source.read_text(encoding="utf-8"), kind)
    for wt in wts:
        try:
            got = _pin_in((root / "worktrees" / wt / rel).read_text(encoding="utf-8"), kind)
        except (OSError, UnicodeDecodeError):
            continue                   # a partial mirror without the pin file leaves it alone
        if got != want:
            moves.append({"where": f"worktrees/{wt}", "file": rel, "from": want, "to": got,
                          "revert": f"cp {rel} worktrees/{wt}/{rel}"})
    return moves


def _branch_moves(root: Path, feature: str, wts: list[str], rel: str,
                  kind: str) -> list[dict]:
    import subprocess
    from prusik import worktrees

    def git(*args: str) -> str | None:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None

    moves = []
    for wt in wts:
        branch = worktrees.branch_name(feature, wt)
        if not git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"):
            continue                   # another feature's worktree, or not a branch
        base = (git("rev-parse", "--verify", "--quiet",
                    f"{worktrees.base_ref(branch)}^{{commit}}")
                or git("merge-base", branch, "HEAD") or "").strip()
        if not base:
            continue                   # no fork point: no change set to judge
        old = _pin_in(git("show", f"{base}:{rel}"), kind)
        new = _pin_in(git("show", f"{branch}:{rel}"), kind)
        if old != new:
            moves.append({"where": branch, "file": rel, "from": old, "to": new,
                          "revert": f"git -C worktrees/{wt} checkout {base[:12]} -- {rel} "
                                    f"&& git -C worktrees/{wt} commit -m 'restore the pin'"})
    return moves


RELEASE_OWNS_BUMP = ("the release owns the version bump (scripts/release.sh in the prusik "
                     "repo, `prusik refresh` in a project), so a sprint never changes it")


def moves_message(moves: list[dict]) -> str:
    """The refusal for `sprint_moves`: what moved, where, and the revert."""
    lines = [f"sprint changes the version pin; {RELEASE_OWNS_BUMP}. Merged, it would put "
             "the checkout out of step with the "
             "installed prusik and the pin gate would refuse every tool call. Revert it on the "
             "builder's side:"]
    for m in moves:
        lines.append(f"  - {m['where']}: {m['file']} {m['from']} → {m['to']}. "
                     f"Revert: {m['revert']}")
    return "\n".join(lines)


# ── launching a prusik child ─────────────────────────────────────────────────────────

def child_command(*args: str, environ=None) -> tuple[list[str], dict[str, str]]:
    """(argv, env) for `python -m prusik <args>` that imports THIS process's package: the env
    names it in PRUSIK_ORIGIN (the child refuses otherwise) and sets PYTHONSAFEPATH=1 so on
    3.11+ the child's cwd is never on sys.path."""
    env = dict(os.environ if environ is None else environ)
    env[ORIGIN_ENV] = package_dir()
    if "PYTHONSAFEPATH" not in env:
        env["PYTHONSAFEPATH"] = "1"
        env[_SAFEPATH_ADDED_ENV] = "1"
    return [sys.executable, "-m", "prusik", *args], env


def consume_launch_env(environ=None) -> None:
    """A launched child's contract is one hop: once checked, drop what the launcher added so
    this process's own children (a captured test suite, a git call) get the operator's env."""
    env = os.environ if environ is None else environ
    env.pop(ORIGIN_ENV, None)
    if env.pop(_SAFEPATH_ADDED_ENV, None) == "1":
        env.pop("PYTHONSAFEPATH", None)
