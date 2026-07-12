"""Cross-artifact consistency checks.

The schema validators catch single-artifact problems (missing sections, wrong
enum values, malformed lists). These checks catch *cross-artifact* drift —
e.g., a plan that expanded beyond the declared scope, or a builder that
wrote files outside the plan's module touch-list.

Each check returns a list of error strings; empty list = pass. `gate.advance`
runs the checks appropriate for the phase being left.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from prusik import schema

_BACKTICK_PATH_RE = re.compile(r"`\s*\+?\s*([^`]+?)\s*`")

# Dependency / build / artifact directories that are never a sprint deliverable.
# A worktree set up for a JS/TS build (prusik worktree-setup, findings #10/#11)
# legitimately contains node_modules/ + dist/; the boundary and cache scans must
# not walk them (field finding #15: 79,988 phantom "violations" from node_modules
# on a ~41-file change).
_BUILD_DEP_DIRS = ("node_modules", "dist", "build", ".turbo", ".next", "out",
                   "coverage", ".venv", "venv", "vendor", "target",
                   ".pnpm-store", ".parcel-cache", ".svelte-kit")

# Dependency-lock artifacts: machine-written, churned by any `install`, and never
# a plan deliverable. #15 excluded the dep *dirs* but a lockfile sits at the repo
# root (e.g. pnpm-lock.yaml) so it slipped through as a phantom boundary
# violation (field finding #20.2). Matched by basename.
_DEP_LOCK_FILES = frozenset({
    "pnpm-lock.yaml", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock",
    "bun.lockb", "poetry.lock", "Pipfile.lock", "uv.lock", "Cargo.lock",
    "Gemfile.lock", "composer.lock", "go.sum", "flake.lock",
})


def _under_build_dep_dir(rel: str) -> bool:
    parts = rel.split("/")
    return any(p in _BUILD_DEP_DIRS for p in parts)


def _git(d: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(d), *args],
                          capture_output=True, text=True, check=False)


def _declared_deviations(project_root: Path, feature: str) -> set[str]:
    """File paths the builder DECLARED touching out-of-plan in
    design/<feature>/deviations.md — the sanctioned, visible record of legitimate
    mid-build drift (field finding #17). The boundary check credits these: an honest,
    logged departure from scope is authorized; a silent one is not. Returns both
    full path tokens and basenames so a partial-path log entry still matches."""
    dev = project_root / "design" / feature / "deviations.md"
    if not dev.exists():
        return set()
    try:
        text = dev.read_text()
    except OSError:
        return set()
    import re as _re
    out: set[str] = set()
    # path-like tokens: a dotted filename, optionally with directories, as
    # commonly written in a deviation log (bare, backticked, or parenthesized).
    for m in _re.finditer(r"[\w./-]+\.\w+", text):
        # strip only a leading `./` — NOT all leading dots, which would turn a
        # dotfile like `.env.test` into `env.test` and miss it (#20.1).
        tok = _re.sub(r"^\./", "", m.group(0))
        if tok:
            out.add(tok)
            out.add(Path(tok).name)
    return out


def _is_declared(rel_str: str, declared: set[str]) -> bool:
    if not declared:
        return False
    if rel_str in declared or Path(rel_str).name in declared:
        return True
    # a logged partial path (e.g. `errors/AppError.ts`) crediting a fuller
    # worktree path (`src/errors/AppError.ts`), or vice-versa.
    return any(rel_str.endswith(d) or d.endswith(rel_str)
               for d in declared if "/" in d)


def _git_worktree_changed_files(teammate_dir: Path) -> list[str] | None:
    """For a REAL git worktree (full tree, e.g. a JS/TS build worktree), the
    builder's responsibility is exactly what they CHANGED — tracked diffs vs HEAD
    plus untracked-not-gitignored files. Returns those worktree-relative paths,
    or None when `teammate_dir` is not a git-worktree root (a partial-mirror dir,
    whose every file IS a builder write — caller walks it instead).

    This excludes node_modules/lockfiles (gitignored) AND unchanged config/readme
    files (not in the diff) precisely, without a denylist guess."""
    top = _git(teammate_dir, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return None
    try:
        if Path(top.stdout.strip()).resolve() != teammate_dir.resolve():
            return None        # the project root, not this dir → partial mirror
    except OSError:
        return None
    changed = _git(teammate_dir, "diff", "--name-only", "HEAD").stdout.splitlines()
    untracked = _git(teammate_dir, "ls-files", "--others",
                     "--exclude-standard").stdout.splitlines()
    return [p for p in (changed + untracked) if p.strip()]


def git_project_files(teammate_dir: Path) -> list[str] | None:
    """For a REAL git worktree, the COMPLETE set of files git considers project
    content — tracked (`ls-files`) PLUS untracked-but-not-ignored
    (`ls-files --others --exclude-standard`). Returns worktree-relative paths, or
    None when `teammate_dir` is not a git-worktree root (a partial mirror).

    Unlike `_git_worktree_changed_files` (only the diff), this is the whole judged
    file-set — what the reviewer-evidence hash must cover. Crucially it EXCLUDES
    gitignored build artifacts BY CONSTRUCTION (a tsc `tsbuildinfo`, `dist/`,
    `coverage/`, lockfiles), so a build-/typecheck-triggering capture can't drift the
    hash and stale a co-reviewer's evidence — the root cause of the recurring
    dist-in-hash churn (fb-b4eb142e5740 → fb-086ca221468d), fixed completely by
    inverting the doomed derived-dir denylist into a git-tracked allowlist. Genuinely
    NEW source (untracked, not ignored) is still included, so a stale PASS can't survive
    a real code addition."""
    top = _git(teammate_dir, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return None
    try:
        if Path(top.stdout.strip()).resolve() != teammate_dir.resolve():
            return None        # the project root, not this dir → partial mirror
    except OSError:
        return None
    tracked = _git(teammate_dir, "ls-files").stdout.splitlines()
    untracked = _git(teammate_dir, "ls-files", "--others",
                     "--exclude-standard").stdout.splitlines()
    return [p for p in (tracked + untracked) if p.strip()]


def sprint_changed_files(project_root: Path) -> set[str]:
    """Union of root-relative files changed across all builder worktrees this
    sprint — git-diff for a real (TS) worktree, the written-file walk for a
    partial mirror, build/dep dirs excluded. The "what did this sprint actually
    touch" set used to VERIFY a plan-time blast-radius prediction was consumed
    (field retro #1: predicted-regressing tests that were never updated)."""
    out: set[str] = set()
    worktrees = project_root / "worktrees"
    if not worktrees.exists():
        return out
    for teammate_dir in sorted(worktrees.iterdir()):
        if not teammate_dir.is_dir():
            continue
        changed = _git_worktree_changed_files(teammate_dir)
        if changed is not None:
            out.update(changed)
        else:
            for f in teammate_dir.rglob("*"):
                if f.is_file():
                    rel = str(f.relative_to(teammate_dir))
                    if not _under_build_dep_dir(rel):
                        out.add(rel)
    return out


# Orchestration the reviewer never tests — never staged into root by assembly.
_ASSEMBLE_SKIP_PREFIXES = (".sprint/", "reports/", "design/", ".claude/", "briefs/")


def _assemblable_worktree_files(project_root: Path):
    """Yield (teammate_dir, rel, path) for every PARTIAL-MIRROR worktree file
    eligible for assembly. REAL git worktrees (TS full-tree) are skipped (tested in
    place / git-merged); symlinks (the v0.125.0 .env stage), build/dep dirs, and
    orchestration dirs are never staged."""
    worktrees = project_root / "worktrees"
    if not worktrees.exists():
        return
    for teammate_dir in sorted(worktrees.iterdir()):
        if not teammate_dir.is_dir():
            continue
        if _git_worktree_changed_files(teammate_dir) is not None:
            continue                      # real git worktree → not a partial mirror
        for f in sorted(teammate_dir.rglob("*")):
            if f.is_symlink() or not f.is_file():
                continue                  # skip staged infra (e.g. .env symlink)
            rel = str(f.relative_to(teammate_dir))
            if _under_build_dep_dir(rel) or rel.startswith(_ASSEMBLE_SKIP_PREFIXES):
                continue
            yield teammate_dir, rel, f


def _worktree_file_hashes(project_root: Path) -> dict[str, str]:
    """Snapshot `{<role>/<rel>: sha256}` of the assemblable partial-mirror files.
    Taken at fix-round START so the end-of-round assembly can stage ONLY the files
    the fix-round actually changed — pre-existing worktree scaffolding (e.g. a
    test-writer conftest STUB meant to be dropped at integration) is left alone and
    never clobbers a canonical root file (fb-bfc8ffdf0fd9)."""
    import hashlib
    out: dict[str, str] = {}
    for teammate_dir, rel, f in _assemblable_worktree_files(project_root):
        try:
            out[f"{teammate_dir.name}/{rel}"] = hashlib.sha256(
                f.read_bytes()).hexdigest()
        except OSError:
            continue
    return out


_STUB_MARKERS = (
    "prusik:worktree-local",     # explicit convention — a deliberate worktree stub
    "drop at integration",       # natural-language forms the test-writer already uses
    "dropped at integration",
    "dropped-at-integration",
)


def gitignored_subset(project_root: Path, rels: list[str]) -> set[str]:
    """Of `rels` (root-relative paths), the subset the project's git would IGNORE —
    batched via a single `git check-ignore --stdin`. Empty set when root isn't a git repo
    or git errors (caller then keeps all paths).

    This lets the PARTIAL-MIRROR worktree substantive hash exclude capture-generated
    artifacts (`.coverage`, `*.log`, build output) the SAME way the real-git-worktree hash
    already does via gitignore (v0.152.0) — so one reviewer's capture SIDE EFFECTS can't
    move a co-reviewer's snapshot and retroactively stale its evidence (fb-92e248d6a208, the parallel-reviewer hash race). Each reviewer's snapshot binds to
    the JUDGED source only, stable against another reviewer's run."""
    if not rels:
        return set()
    top = _git(project_root, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return set()
    proc = subprocess.run(
        ["git", "-C", str(project_root), "check-ignore", "--stdin"],
        input="\n".join(rels), capture_output=True, text=True, check=False)
    if proc.returncode not in (0, 1):     # 0=some ignored, 1=none, 128=error
        return set()
    return {ln for ln in proc.stdout.splitlines() if ln}


def _root_pristine_tracked(project_root: Path) -> set[str] | None:
    """Root-relative paths that are git-tracked AND unmodified vs HEAD — the PRISTINE
    CANONICAL files the current sprint never touched at root. Returns None when root is
    not a git repo (the caller then can't use this protection).

    A pristine-canonical file is the one signal that a differing worktree file over it is
    a CLOBBER, not a deliverable sync: the catastrophic case (fb-5bb5171810ee) was a
    1206-line committed `tests/integration/conftest.py` — pristine, never a sprint target
    — silently overwritten by a 14-line UNMARKED worktree placeholder, destroying shared
    fixtures + autouse schema-restore. A genuinely-stale DELIVERABLE, by contrast, was
    written to root by the initial build assembly, so its root copy is untracked OR
    modified-vs-HEAD — NOT pristine — and is safe to re-sync (fb-ba9d617d55cb). Git
    distinguishes them with no marker and no scope plumbing."""
    top = _git(project_root, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return None
    tracked = {ln for ln in _git(project_root, "ls-files").stdout.splitlines() if ln}
    if not tracked:
        return None
    dirty: set[str] = set()
    for ln in _git(project_root, "status", "--porcelain").stdout.splitlines():
        path = ln[3:] if len(ln) > 3 else ""
        if " -> " in path:               # rename: "old -> new" — both sides are touched
            old, new = path.split(" -> ", 1)
            dirty.add(old.strip().strip('"'))
            dirty.add(new.strip().strip('"'))
        elif path:
            dirty.add(path.strip().strip('"'))
    return tracked - dirty


def _is_worktree_local_stub(content: bytes) -> bool:
    """A worktree-LOCAL stub self-declares that it is dropped at integration and the
    canonical root file takes over (fb-bfc8ffdf0fd9 — a test-writer conftest stub
    that, if assembled, CLOBBERS the canonical root conftest). A builder/test-writer
    marks such a file with `prusik:worktree-local` (or the natural-language
    'dropped at integration'). A marked stub is NEVER staged to root, so the canonical
    file it shadows survives. Marker-based, not size/heuristic — explicit + auditable."""
    try:
        low = content.decode("utf-8", errors="ignore").lower()
    except Exception:
        return False
    return any(m in low for m in _STUB_MARKERS)


def assemble_worktrees_to_root(project_root: Path,
                               baseline: dict[str, str] | None = None) -> list[str]:
    """Stage PARTIAL-MIRROR worktree files into the project root so the reviewer
    tests the CURRENT code. A fix-round patches `worktrees/*/**` but does NOT re-run
    the post-build assembly, so the reviewer re-tests the STALE pre-fix-round root and
    reports the same now-fixed defects forever — the unbreakable reviewing loop (fb-db53b5d5d380). Re-staging at fix-round end lets the loop converge.

    Criterion: stage every eligible worktree file whose content DIFFERS from its root
    copy — so a clean worktree deliverable whose root copy is STALE or dirty (e.g. the
    older version laid down by the initial build assembly, never touched DURING a
    fix-round) finally syncs and the root gate matches the green worktree (fb-ba9d617d55cb) — with TWO clobber guards:

      1. A PRISTINE-CANONICAL root file (git-tracked AND unmodified vs HEAD — a file the
         sprint never touched at root) is NEVER overwritten. That is the one signal that
         a differing worktree file over it is a CLOBBER, not a sync: a 1206-line committed
         conftest was silently destroyed by a 14-line UNMARKED worktree placeholder,
         cascading to 510 fixture-errors + DB schema corruption (fb-5bb5171810ee). A
         genuinely-stale deliverable is untracked/modified at root (the initial assembly
         wrote it), NOT pristine, so it still syncs. git-derived — no marker, no scope.
      2. A worktree-LOCAL stub that self-declares it is dropped at integration
         (`_is_worktree_local_stub`) is skipped (fb-bfc8ffdf0fd9) — explicit
         defense-in-depth on top of the structural pristine guard.

    This supersedes the v0.134.0 round-delta scoping (too narrow — missed a stale
    deliverable) and hardens the v0.150.0 differ-from-root (too broad — clobbered an
    unmarked pristine canonical). `baseline` is accepted for back-compat and ignored.
    Returns the root-relative paths staged."""
    import shutil
    pristine = _root_pristine_tracked(project_root)   # None when root isn't a git repo
    copied: list[str] = []
    for teammate_dir, rel, f in _assemblable_worktree_files(project_root):
        try:
            wt_bytes = f.read_bytes()
        except OSError:
            continue
        dest = project_root / rel
        if dest.exists():
            try:
                if dest.read_bytes() == wt_bytes:
                    continue              # already in sync → nothing to stage
            except OSError:
                pass
            # GUARD 1 — never clobber a pristine committed canonical the sprint never
            # touched (fb-5bb5171810ee). A stale deliverable is untracked/modified at
            # root, so it is NOT pristine and is not protected here.
            if pristine is not None and rel in pristine:
                continue
        if _is_worktree_local_stub(wt_bytes):
            continue                      # GUARD 2 — drop-at-integration stub
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
            copied.append(rel)
        except OSError:
            continue
    return sorted(set(copied))


def _first_token(body: str) -> str | None:
    s = body.strip()
    return s.split()[0].rstrip(",.") if s else None


def _bullet_module_tokens(item: str) -> list[str]:
    """All path tokens a Modules-touched bullet declares — the primary token AND any
    additional backticked paths in the same bullet (fb-75fd9cfa7ead: a bullet
    with two backticked paths, ``a.sh`` + ``b.yml``, previously registered only the
    first)."""
    toks: list[str] = []
    primary, _is_new = schema.extract_module_token(item)
    if primary:
        toks.append(primary)
    for m in _BACKTICK_PATH_RE.finditer(item):
        cand = schema.extract_path_token(m.group(1))
        if cand and cand not in toks:
            toks.append(cand)
    return toks


def _modules_from(artifact_path: Path, section: str = "## Modules touched") -> set[str]:
    if not artifact_path.exists():
        return set()
    sections = schema.parse_sections(artifact_path.read_text())
    # fb-6a4075fb15fe + fb-75fd9cfa7ead: tolerate a header SUFFIX
    # ("## Modules touched (final)") and honour a later restated declaration — union
    # the bodies of ALL sections whose header starts with the canonical one, not just
    # the single exact-named contiguous block.
    body = "\n".join(b for h, b in sections.items() if h.startswith(section))
    # v0.5.5: delegate to schema.extract_module_token so this cross-artifact
    # parser agrees with validate_scope on `+ `-prefixed and markdown-wrapped
    # bullets. Previously raw extract_path_token captured `+` as a standalone
    # path for `- + \`path\`` bullets, producing thousands of false
    # builder-out-of-plan violations and deadlocking advance at building.
    # v0.6.7: route through schema.extract_list_items so cross-artifact
    # parsing inherits HR skipping (v0.6.1), `+ `/`* ` alt markers (v0.6.1),
    # AND nested-bullet skipping (v0.6.7) — same code path = can't drift.
    # Pre-v0.6.7 consistency had its own _bullet_items that lacked all three.
    # Drop exclusion/commentary subsections (e.g. "### NOT in the touch-list") so they
    # don't register as declared modules — same helper as validate_scope, so the two
    # cross-artifact parsers can't drift (fb-085135ece453).
    out: set[str] = set()
    for item in schema.extract_list_items(
            schema.strip_non_declaration_subsections(body)):
        if not item.strip():
            continue
        out.update(_bullet_module_tokens(item))
    return out


def plan_within_scope(project_root: Path, feature: str) -> list[str]:
    """plan.md's Modules touched must be a subset of scope.md's Modules touched."""
    scope = project_root / "design" / feature / "scope.md"
    plan = project_root / "design" / feature / "plan.md"
    if not plan.exists() or not scope.exists():
        return []
    scope_mods = _modules_from(scope)
    plan_mods = _modules_from(plan)
    extra = plan_mods - scope_mods
    if extra:
        return [
            f"plan.md adds modules not in scope.md: {sorted(extra)}. "
            f"Update scope first (cheaper) or tighten plan."
        ]
    return []


def _fan_out_prediction(project_root: Path, feature: str) -> set[str]:
    """The blast-radius PREDICTION's reverse-dep files (system-computed from the
    dep-graph; persisted by blast_plan.record_prediction at
    `.sprint/blast-prediction.<feature>.json`). The boundary check credits these as
    sanctioned fan-out — the SAME prediction the fix-round writable lane uses, so a
    builder that mirrors a predicted reverse-dep file into a worktree isn't flagged
    out-of-plan for fixing exactly what the gate told it would break (fb-7ab319116f42).
    Bounded to the prediction; an agent can't add to it."""
    import json as _json
    p = project_root / ".sprint" / f"blast-prediction.{feature}.json"
    if not p.exists():
        return set()
    try:
        pred = _json.loads(p.read_text())
    except (OSError, ValueError):
        return set()
    return set((pred.get("at_risk_tests") or []) + (pred.get("symbol_leak_tests") or []))


def builder_writes_within_plan(project_root: Path, feature: str) -> list[str]:
    """Files written under worktrees/* must fall within the sprint's declared
    boundary.

    v0.10.0 (Fix 1, design-passes/v0.10.0-derive-and-delta-regate.md):
    the comparison target moved from plan.md's hand-maintained
    `## Modules touched` to **scope.md's** declared modules. Prusik's
    `worktrees/<role>/` file-set is already the derived (non-hand-maintained)
    source of truth for "what this sprint changed"; the Tier-1 rot was never
    in the reality source, it was in the rotting middle (`plan_mods`) the
    reality was compared against. scope is set once in scoping and is the
    real authoritative boundary; comparing derived reality directly against
    it eliminates the hand-list rot class (m4-s8c: `plan_within_scope`
    blocked 3× on hand-list drift; this is the structural cure). plan_mods
    survives only as the legacy fallback when scope.md is absent, so the
    prusik stays opt-in / non-coupling for non-prusik-scoped projects.

    Meta-artifact carve-outs (orchestrator conventions, not deliverables):
      - tests/ — test files are implicitly part of every sprint.
      - reports/{feature}/build-<role>.txt — v0.5.6: builders emit PASS/FAIL
        build reports here; orchestrator meta-artifacts, never modules.
      - .sprint/ — v0.10.0 Fix 4: prusik-internal housekeeping.
    """
    import fnmatch as _fnmatch
    scope = project_root / "design" / feature / "scope.md"
    plan = project_root / "design" / feature / "plan.md"
    # Authoritative boundary = scope. Legacy fallback = plan (only when
    # scope.md is absent — preserves pre-v0.10.0 behavior for projects that
    # don't produce a scope artifact).
    if scope.exists():
        boundary_mods = _modules_from(scope)
        boundary_src = "scope.md"
    elif plan.exists():
        boundary_mods = _modules_from(plan)
        boundary_src = "plan.md"
    else:
        return []
    if not boundary_mods:
        return []
    worktrees = project_root / "worktrees"
    if not worktrees.exists():
        return []
    build_report_glob = f"reports/{feature}/build-*.txt"
    declared = _declared_deviations(project_root, feature)   # #17 — sanctioned drift
    fan_out = _fan_out_prediction(project_root, feature)      # fb-7ab319116f42 — predicted
    violations: list[str] = []
    for teammate_dir in worktrees.iterdir():
        if not teammate_dir.is_dir():
            continue
        # A real git worktree (full tree) → only the builder's CHANGED files; a
        # partial-mirror dir → every written file, minus build/dep dirs (#15).
        changed = _git_worktree_changed_files(teammate_dir)
        if changed is not None:
            rel_strs = changed
        else:
            rel_strs = [str(f.relative_to(teammate_dir))
                        for f in teammate_dir.rglob("*")
                        if f.is_file()
                        and not _under_build_dep_dir(str(f.relative_to(teammate_dir)))]
        for rel_str in rel_strs:
            if rel_str.startswith("tests/") or "/tests/" in rel_str:
                continue
            if _fnmatch.fnmatch(rel_str, build_report_glob):
                continue
            # v0.10.0 (Fix 4): prusik-internal sprint housekeeping is never a
            # plan deliverable. The m4-s8c 01:16:21 block was the subset
            # gate flagging `<role>/.sprint/status/<role>.txt` — prusik
            # blocking its own housekeeping. Scoped to `.sprint/` ONLY:
            # unlike the phase-writable gate (phases.py), the subset gate
            # deliberately keeps `reports/` narrow (the existing
            # build_report_glob above is the precise carve-out) so random
            # junk under worktrees/<role>/reports/ still trips the gate.
            if rel_str.startswith(".sprint/"):
                continue
            # design/ is sprint orchestration (scope/plan/map/deviations), never a
            # code deliverable — and the phase-writable gate already governs which
            # of those a builder may touch. Excluding it here stops the deviations
            # log from flagging *itself* as out-of-boundary (field finding #20.3).
            if rel_str.startswith("design/"):
                continue
            if Path(rel_str).name in _DEP_LOCK_FILES:   # lockfiles (#20.2)
                continue
            if _is_declared(rel_str, declared):   # #17 — recorded in deviations.md
                continue
            if rel_str in fan_out:   # fb-7ab319116f42 — blast-radius-PREDICTED fan-out
                continue
            if not any(rel_str.startswith(m.rstrip("/")) for m in boundary_mods):
                violations.append(
                    f"{teammate_dir.name}/{rel_str} is outside {boundary_src}'s "
                    f"Modules touched {sorted(boundary_mods)}"
                )
    if violations:
        return (
            [f"Builder files fall outside the sprint's declared boundary "
             f"({boundary_src}, {len(violations)} violation(s)):"]
            + [f"  - {v}" for v in violations[:10]]
            + ([f"  ... and {len(violations) - 10} more"] if len(violations) > 10 else [])
            # fb-75fd9cfa7ead / fb-c8175a7678be — point at the RIGHT, writable path:
            # a legitimate rename/consolidation is logged in deviations.md (always-writable,
            # boundary-credited), NOT by editing scope.md (phase-blocked mid-build).
            + ["",
               f"If these are legitimate deviations (a consolidated or renamed file within "
               f"the scoped area), record each in design/{feature}/deviations.md — "
               f"always-writable, no scope.md edit needed (scope is phase-blocked mid-build) "
               f"— as `DEV-NNN: <path> — <why>`; the boundary credits logged deviations. "
               f"If a file is genuinely out of scope, move it back into the scoped modules."]
        )
    return []


# v0.6.2: cache directory markers that linter/test tools write as side-effects.
# Scanned at reviewing-phase exit because reviewers (ruff, pytest, mypy) run
# DURING reviewing, AFTER the building→reviewing advance check fires. Without
# this check, cache contamination flows through integrator into project root,
# then surfaces as conventions violations on the NEXT sprint.
_CACHE_MARKERS = {
    ".ruff_cache",
    ".pytest_cache",
    "__pycache__",
    ".mypy_cache",
    ".tox",
    ".cache",
    ".coverage",
}


def worktrees_clean_of_cache_artifacts(project_root: Path, feature: str
                                        ) -> list[str]:
    """Scan worktrees/ for tool-emitted cache directories before integrator runs.

    Runs at reviewing-phase exit. Reviewers' tool invocations
    (ruff/pytest/mypy without --no-cache flags) write cache dirs into the
    cwd; prusik's PreToolUse Edit/Write gate doesn't intercept subprocess
    syscalls so they slip through. If integrator copies worktrees/<role>/
    into project root, the caches get carried in — surfacing as
    conventions-enforcer violations on the next sprint.

    Defense-in-depth: regression-sentinel + conventions-enforcer role specs
    mandate --no-cache flags (source-side prevention); this check catches
    what slips through (last-mile enforcement). Together they make
    cache-leak bugs mechanically impossible.
    """
    import os
    worktrees = project_root / "worktrees"
    if not worktrees.exists():
        return []
    polluted: list[str] = []
    for teammate_dir in worktrees.iterdir():
        if not teammate_dir.is_dir():
            continue
        for dirpath, dirnames, _files in os.walk(teammate_dir):
            # Prune build/dep trees — don't descend into node_modules/ etc. (#15:
            # both perf and to avoid flagging a dep's own internal cache dirs).
            dirnames[:] = [d for d in dirnames if d not in _BUILD_DEP_DIRS]
            for d in dirnames:
                if d in _CACHE_MARKERS:
                    rel = (Path(dirpath) / d).relative_to(project_root)
                    polluted.append(str(rel))
    if not polluted:
        return []
    return [
        f"Worktree contains tool-emitted cache directories "
        f"({len(polluted)} found):"
    ] + [f"  - {p}" for p in polluted[:10]] + (
        [f"  ... and {len(polluted) - 10} more"] if len(polluted) > 10 else []
    ) + [
        "  Reviewers should pass --no-cache (or equivalent) flags:",
        "    pytest -p no:cacheprovider",
        "    ruff check --no-cache",
        "    mypy --no-incremental --cache-dir=/dev/null",
        "  Or delete the cache dirs from worktrees/ before advancing.",
    ]


def brief_type_matches_scope(project_root: Path, feature: str) -> list[str]:
    """Obvious contradictions between brief type and derived size.

    A bug_fix or doc or config with scope size L/XL usually means either
    the brief mistyped or the scope overreached. Flag, don't block.
    """
    brief = project_root / "briefs" / f"{feature}.md"
    scope = project_root / "design" / feature / "scope.md"
    if not brief.exists() or not scope.exists():
        return []
    b = schema.parse_sections(brief.read_text())
    s = schema.parse_sections(scope.read_text())
    btype = _first_token(b.get("## Type", ""))
    size = _first_token(s.get("## Size", ""))
    small_types = {"bug_fix", "doc", "config"}
    if btype in small_types and size in ("L", "XL"):
        return [
            f"Type '{btype}' conflicts with size '{size}'. "
            f"Either the brief is mis-typed or scope overreached — re-check before proceeding."
        ]
    return []


def reconciliation_summary(project_root: Path, feature: str) -> dict | None:
    """v0.10.0 (Fix 1): observability of the hand-list rot Fix 1 eliminates.

    Pure — no ledger write (gate.advance logs the event; consistency.py
    stays side-effect-free, matching its existing design). Returns None
    when there is nothing to reconcile (no scope artifact). `stale_in_plan`
    = entries the hand-list carried that scope never had (the rot that
    false-blocked pre-v0.10.0); `dropped_from_plan` = scope modules the
    hand-list lost across rewinds (the literal m4-s8c
    settings.py/validation.py loss). Both are now harmless — scope is the
    boundary — but recording them proves the cure and quantifies the tax.
    """
    scope = project_root / "design" / feature / "scope.md"
    plan = project_root / "design" / feature / "plan.md"
    if not scope.exists():
        return None
    scope_mods = _modules_from(scope)
    plan_mods = _modules_from(plan) if plan.exists() else set()
    return {
        "boundary": "scope.md",
        "scope_module_count": len(scope_mods),
        "plan_hand_list_count": len(plan_mods),
        "stale_in_plan": sorted(plan_mods - scope_mods),
        "dropped_from_plan": sorted(scope_mods - plan_mods),
    }


# Registry: phase_leaving → [check_fn, ...]
#
# v0.10.0 (Fix 1): `plan_within_scope` removed from "planning". It only ever
# validated the hand-maintained `plan.md` middle (`plan_mods ⊆ scope_mods`).
# With `builder_writes_within_plan` now comparing derived worktree reality
# directly against scope.md (the authoritative boundary), the rotting middle
# no longer gates anything — so checking its consistency gates nothing. The
# scope-containment invariant is enforced once, at building/solo_execute
# exit, from derived reality vs. the stable boundary. `plan_within_scope`
# the function is retained (direct callers / unit tests unaffected) but is
# no longer a phase gate. This is the structural cure for the m4-s8c Tier-1
# rot (`plan_within_scope` blocked 3× on hand-list drift across ~10 rewinds).
PHASE_CHECKS = {
    "scoping": [brief_type_matches_scope],
    "planning": [],
    "building": [builder_writes_within_plan, worktrees_clean_of_cache_artifacts],
    "solo_execute": [builder_writes_within_plan, worktrees_clean_of_cache_artifacts],
    "reviewing": [worktrees_clean_of_cache_artifacts],
}


def run_for_phase(phase_leaving: str, project_root: Path, feature: str) -> list[str]:
    errors: list[str] = []
    for fn in PHASE_CHECKS.get(phase_leaving, []):
        errors.extend(fn(project_root, feature))
    return errors


# ---------- v0.8.2 — B26 reviewer-fabrication detector ----------
#
# v0.8.1 hardened the role specs to require verify-before-claim. But CC
# caches agent prompts at session start, so a session that started before
# the v0.8.1 templates landed continues to use the OLD anti-pattern
# prompts until the operator restarts. Even after restart, an LLM agent
# under uncertainty may still bail with a fabricated denial — role-spec
# discipline reduces probability, doesn't eliminate it.
#
# This detector closes the gap at the orchestrator boundary. After a
# reviewer artifact is written, cross-check the artifact's claims against
# the ledger. Three failure shapes flagged:
#
#   - Claims `[prusik-gate]` but ledger has no gate_blocked Bash event for
#     the feature — fabricated quote.
#   - Claims Bash denied without quoting `[prusik-gate]` AND ledger has no
#     gate_blocked Bash event — pure fabrication (B26 canonical shape).
#   - Claims Bash denied without quoting `[prusik-gate]` BUT ledger DOES
#     have a real deny — incomplete report; agent should quote it.
#
# The detector is informational, not blocking. It surfaces a warning to
# stderr and emits a `reviewer_fabrication_suspected` ledger event so
# `prusik digest` can surface counts. Operator decides whether to
# re-dispatch the reviewer or accept the FAIL.

# Reviewer artifacts the detector inspects.
_REVIEWER_ARTIFACTS = (
    ("regression-sentinel", "regression.txt"),
    ("conventions-enforcer", "conventions.txt"),
)

# Phrases in artifact text that indicate the agent is claiming Bash deny.
# Lowercased for matching.
_DENY_CLAIM_PATTERNS = (
    "bash denied",
    "bash tool access was denied",
    "bash tool access denied",
    "permissions.allow",
    "permission set",
    "unable to run tests under current permission",
)


def _has_real_bash_gate_block(feature: str) -> bool:
    """True if the ledger contains any gate_blocked event with tool=Bash
    for this feature. Used to distinguish real denies (which the agent
    should be quoting) from fabricated ones (no event in ledger)."""
    from prusik import ledger
    for r in ledger.read_all():
        if r.get("event") != "gate_blocked":
            continue
        if r.get("tool") != "Bash":
            continue
        if r.get("feature") != feature:
            continue
        return True
    return False


def detect_reviewer_fabrication(project_root: Path, feature: str) -> list[dict]:
    """Cross-check reviewer artifacts against the ledger for fabricated
    Bash-denial claims (v0.8.2, B26 detector).

    Returns a list of suspect dicts. Each:
      {
        "role": "regression-sentinel" | "conventions-enforcer",
        "artifact": "reports/<feature>/regression.txt",
        "feature": "<feature>",
        "shape": "fabricated_quote" | "pure_fabrication" | "incomplete_report",
        "reason": "<human-readable explanation>",
      }

    Empty list = no suspects.
    """
    suspects: list[dict] = []
    has_real_block = _has_real_bash_gate_block(feature)

    for role, fname in _REVIEWER_ARTIFACTS:
        artifact = project_root / "reports" / feature / fname
        if not artifact.exists():
            continue
        try:
            text = artifact.read_text()
        except OSError:
            continue
        lines = text.splitlines()
        if not lines or lines[0].strip() != "FAIL":
            continue
        text_lower = text.lower()
        claims_deny = any(p in text_lower for p in _DENY_CLAIM_PATTERNS)
        if not claims_deny:
            continue
        # Did the agent quote a real `[prusik-gate]` deny message?
        # The v0.8.1 role spec requires this — quote verbatim.
        quotes_kit_gate = "[prusik-gate]" in text

        artifact_rel = str(artifact.relative_to(project_root))
        if quotes_kit_gate and has_real_block:
            # Real deny + agent quoted it: legitimate. No suspect.
            continue
        if quotes_kit_gate and not has_real_block:
            suspects.append({
                "role": role,
                "artifact": artifact_rel,
                "feature": feature,
                "shape": "fabricated_quote",
                "reason": (
                    f"{role} claims `[prusik-gate]` deny but the ledger has no "
                    f"gate_blocked event with tool=Bash for feature '{feature}'. "
                    f"Prusik logs every real deny to the ledger before returning "
                    f"deny — absence of the event means the agent fabricated the "
                    f"`[prusik-gate]` quote (B26)."
                ),
            })
            continue
        # Doesn't quote `[prusik-gate]`
        if has_real_block:
            suspects.append({
                "role": role,
                "artifact": artifact_rel,
                "feature": feature,
                "shape": "incomplete_report",
                "reason": (
                    f"{role} claims Bash denied without quoting the `[prusik-gate]` "
                    f"message; the ledger DOES show a real gate_blocked Bash event "
                    f"for feature '{feature}'. Agent should quote prusik's deny "
                    f"message verbatim per v0.8.1 role-spec discipline. Re-dispatch "
                    f"or operator-verify before treating as legitimate FAIL."
                ),
            })
        else:
            suspects.append({
                "role": role,
                "artifact": artifact_rel,
                "feature": feature,
                "shape": "pure_fabrication",
                "reason": (
                    f"{role} claims Bash denied but: (a) no `[prusik-gate]` message "
                    f"quoted in the artifact, AND (b) no gate_blocked Bash event "
                    f"in the ledger for feature '{feature}'. Prusik has no "
                    f"subagent-aware deny path — this matches B26 fabrication "
                    f"shape exactly. Likely the running CC session is on cached "
                    f"pre-v0.8.1 prompts; restart the session and re-dispatch the "
                    f"reviewer. If recurrence persists post-restart, escalate via "
                    f"bridge."
                ),
            })
    return suspects


def emit_fabrication_warnings(suspects: list[dict]) -> None:
    """Print human-readable warnings to stderr and log ledger events.

    Caller passes the result of `detect_reviewer_fabrication`. Caller
    decides whether to BLOCK the action — the detector itself is
    informational. Non-empty suspects produce stderr output formatted
    for terminal visibility.
    """
    if not suspects:
        return
    import sys
    from prusik import ledger

    print("", file=sys.stderr)
    print(
        f"[prusik-gate] reviewer-fabrication detector flagged "
        f"{len(suspects)} suspect(s) (B26):",
        file=sys.stderr,
    )
    for s in suspects:
        print(f"  ! {s['artifact']} [{s['shape']}]", file=sys.stderr)
        # Wrap the reason at ~78 cols so terminal output stays readable.
        reason_lines = []
        words = s["reason"].split()
        line = "    "
        for w in words:
            if len(line) + len(w) + 1 > 78:
                reason_lines.append(line)
                line = "    " + w
            else:
                line += (" " if line.strip() else "") + w
        if line.strip():
            reason_lines.append(line)
        for rl in reason_lines:
            print(rl, file=sys.stderr)
        print("", file=sys.stderr)
        ledger.append(
            "reviewer_fabrication_suspected",
            role=s["role"],
            artifact=s["artifact"],
            feature=s["feature"],
            shape=s["shape"],
        )
