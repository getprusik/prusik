"""Project-shape detection (v0.8.4).

`prusik init` runs `detect_project()` unconditionally to learn what the
project's stack looks like before scaffolding the harness. Detected
facts inform:

  - Copy-paste snippets printed at the end of `prusik init` (so the
    operator doesn't have to read sprint-config.yaml's commented blocks
    to figure out what to enable for THEIR project).
  - The prusik manifest (`.claude/.prusik-manifest.json`), which records
    what was detected at install time so future `prusik doctor` (Phase 1.1)
    can flag drift.

Design principle: detect what's safely inferable from project state;
ask the operator only for what requires human judgment (pain points,
structure tolerance, sprint-mode preference). This module covers the
"safely inferable" part.

What we deliberately DON'T detect in v0.8.4:
  - Pytest plugins / markers — too granular, varies wildly.
  - LOC / file-size metrics — slow on large repos; covered by `prusik
    discovery` separately.
  - Git remote / repo metadata — requires `gh` CLI or git operations;
    cleaner as a separate diagnostic.
  - Token-budget calibration — needs a real signal; defer.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


def detect_project(root: Path) -> dict:
    """Inspect a project root and return a structured detection result.

    The dict shape is documented inline. Callers should treat all fields
    as optional — projects vary, and "we couldn't detect this" is a
    valid result (returned as None or empty list).
    """
    return {
        "stacks": _detect_stacks(root),
        "test_commands": _detect_test_commands(root),
        "linters": _detect_linters(root),
        "pre_commit": _detect_pre_commit(root),
        "ci": _detect_ci(root),
        "claude_config": _detect_claude_config(root),
        "behavior_tests": _detect_behavior_tests(root),
        "monorepo": _detect_monorepo(root),
        "ts_prove": _detect_ts_prove(root),
    }


def _detect_ts_prove(root: Path) -> dict:
    """Detect the TS scope-emitting capability for `prusik prove` (v0.53.2).

    tsc/eslint are SILENT on a clean run, so bare `tsc --noEmit` / `eslint`
    give prove no files-checked signal → unproven. This reports what's present
    plus the monorepo wrapper, so init can suggest the verified scope-emitting
    recipe (the one validated on the first TS adopter) — turning a clean check
    from unprovable into TRUE-proven. Empty dict when not a TS-checker project.
    """
    has_tsc = (root / "tsconfig.json").exists()
    has_eslint = (_any_glob_match(root, (".eslintrc*", "eslint.config.*"))
                  or _file_exists_with(root / "package.json", '"eslint"'))
    if not (has_tsc or has_eslint):
        return {}
    wrapper = ("turbo" if (root / "turbo.json").exists()
               else "pnpm" if (root / "pnpm-workspace.yaml").exists()
               else None)
    return {"tsc": has_tsc, "eslint": has_eslint, "wrapper": wrapper}


def _has_workspaces(package_json: Path) -> bool:
    return _file_exists_with(package_json, '"workspaces"')


def worktree_setup_commands(root: Path) -> list[str]:
    """Ordered commands to make a fresh worktree BUILDABLE for the detected stack
    (field findings #10 + #11, seam #2). A JS/TS worktree needs deps installed,
    and in a monorepo the workspace packages BUILT (`dist/`) — because
    cross-package imports resolve to dist, which a bare `tsc` doesn't produce but
    turbo's `^build` does. Empty for stacks that don't need it (a Python
    partial-mirror sprint runs tools from the project root, so no worktree setup).
    `--prefer-offline` keeps install ~10s via the shared store (field-measured)."""
    pj = root / "package.json"
    if not pj.exists():
        return []
    if (root / "pnpm-workspace.yaml").exists() or (root / "pnpm-lock.yaml").exists():
        install, pm = "pnpm install --prefer-offline", "pnpm"
    elif (root / "yarn.lock").exists():
        install, pm = "yarn install", "yarn"
    elif (root / "package-lock.json").exists():
        install, pm = "npm ci", "npm"
    else:
        install, pm = "npm install", "npm"
    cmds = [install]
    monorepo = ((root / "pnpm-workspace.yaml").exists()
                or (root / "turbo.json").exists() or _has_workspaces(pj))
    if monorepo:
        if (root / "turbo.json").exists():
            # fb-1a95785eddf3: bare `turbo` isn't on PATH in a fresh worktree
            # (it's a local node_modules bin) → exit 127. Run it through the package
            # manager so the local binary resolves. `^build` resolves cross-pkg dist/.
            runner = {"pnpm": "pnpm exec", "yarn": "yarn"}.get(pm, "npx")
            cmds.append(f"{runner} turbo run build")
        elif pm == "pnpm":
            cmds.append("pnpm -r build")
        elif pm == "yarn":
            cmds.append("yarn workspaces run build")
        else:
            cmds.append("npm run build --workspaces --if-present")
    return cmds


# ---------- Tech stacks ----------

_STACK_MARKERS = (
    ("python", ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg")),
    ("typescript", ("tsconfig.json",)),
    ("javascript", ("package.json",)),
    ("rust", ("Cargo.toml",)),
    ("go", ("go.mod",)),
    ("ruby", ("Gemfile",)),
    ("java", ("pom.xml", "build.gradle", "build.gradle.kts")),
    ("elixir", ("mix.exs",)),
    ("dotnet", ("*.csproj", "*.fsproj", "global.json")),
)


def _detect_stacks(root: Path) -> list[str]:
    """Return list of detected stacks. Order matches _STACK_MARKERS so a
    multi-stack project's primary lang appears first if its marker is
    earlier in the tuple. TypeScript subsumes JavaScript — if both
    package.json and tsconfig.json exist, only 'typescript' is reported."""
    found: list[str] = []
    for stack, markers in _STACK_MARKERS:
        if _any_glob_match(root, markers):
            found.append(stack)
    # TypeScript dominates JavaScript — drop the redundant 'javascript'
    # entry when both surfaced.
    if "typescript" in found and "javascript" in found:
        found.remove("javascript")
    return found


def _any_glob_match(root: Path, markers: tuple[str, ...]) -> bool:
    """True if any marker exists at root. Markers are either bare
    filenames (exact match) or globs (e.g. '*.csproj')."""
    for m in markers:
        if "*" in m:
            if any(root.glob(m)):
                return True
        else:
            if (root / m).exists():
                return True
    return False


# ---------- Test commands ----------

def _detect_test_commands(root: Path) -> dict:
    """Return {'general': str|None, 'behavior': str|None}. The 'general'
    command is the project's primary test invocation; 'behavior' is set
    only if tests/behavior/ exists with at least one test file."""
    general = _detect_general_test_command(root)
    behavior_dir = root / "tests" / "behavior"
    behavior = None
    if behavior_dir.is_dir() and any(behavior_dir.rglob("test_*.py")):
        # Prefer mirroring the general command's runner if it's pytest;
        # otherwise default to the canonical pytest invocation.
        if general and "pytest" in general:
            behavior = "pytest tests/behavior/ -v"
        elif _file_exists_with(root / "pyproject.toml", "[tool.pytest"):
            behavior = "pytest tests/behavior/ -v"
        else:
            behavior = "pytest tests/behavior/ -v"  # canonical default
    return {"general": general, "behavior": behavior}


def _detect_general_test_command(root: Path) -> str | None:
    """Heuristic: prefer pyproject.toml / pytest.ini / package.json /
    Makefile, in that order. Return the most likely invocation."""
    if (root / "pyproject.toml").exists():
        text = (root / "pyproject.toml").read_text(errors="ignore")
        if "[tool.pytest" in text or "pytest" in text:
            return "pytest"
    if (root / "pytest.ini").exists():
        return "pytest"
    pkg_path = root / "package.json"
    if pkg_path.exists():
        try:
            pkg = json.loads(pkg_path.read_text(errors="ignore"))
            scripts = pkg.get("scripts", {})
            if "test" in scripts:
                # Don't echo the full script body — return the npm
                # invocation. Operator can override.
                return "npm test"
        except (json.JSONDecodeError, OSError):
            pass
    if (root / "Makefile").exists():
        text = (root / "Makefile").read_text(errors="ignore")
        if re.search(r"^test\s*:", text, re.MULTILINE):
            return "make test"
    if (root / "Cargo.toml").exists():
        return "cargo test"
    if (root / "go.mod").exists():
        return "go test ./..."
    return None


def _file_exists_with(path: Path, needle: str) -> bool:
    if not path.exists():
        return False
    try:
        return needle in path.read_text(errors="ignore")
    except OSError:
        return False


# ---------- Linters ----------

_LINTER_PROBES = (
    # (name, [marker paths or pyproject sections])
    ("ruff", [("ruff.toml",), ("pyproject.toml", "[tool.ruff")]),
    ("mypy", [("mypy.ini",), ("pyproject.toml", "[tool.mypy")]),
    ("black", [("pyproject.toml", "[tool.black")]),
    ("isort", [("pyproject.toml", "[tool.isort")]),
    ("eslint", [("eslint.config.js",), ("eslint.config.mjs",), ("eslint.config.cjs",),
                (".eslintrc.json",), (".eslintrc.js",), (".eslintrc.cjs",), (".eslintrc.yml",),
                ("package.json", "\"eslint\"")]),
    ("biome", [("biome.json",), ("biome.jsonc",)]),
    ("prettier", [(".prettierrc",), (".prettierrc.json",), (".prettierrc.js",),
                  (".prettierrc.cjs",), (".prettierrc.yaml",), (".prettierrc.yml",),
                  ("prettier.config.js",), ("prettier.config.cjs",)]),
    ("clippy", [("Cargo.toml", "")]),  # clippy ships with cargo; presence of Cargo.toml implies availability
    ("rustfmt", [("rustfmt.toml",)]),
)


def _detect_linters(root: Path) -> list[str]:
    """Return list of detected linter / formatter names."""
    found: list[str] = []
    for name, probes in _LINTER_PROBES:
        for probe in probes:
            if len(probe) == 1:
                # Marker file presence
                if (root / probe[0]).exists():
                    found.append(name)
                    break
            else:
                # Marker file + content needle
                marker, needle = probe
                if _file_exists_with(root / marker, needle):
                    found.append(name)
                    break
    return found


# ---------- Pre-commit pipeline ----------

def _detect_pre_commit(root: Path) -> dict:
    """Return {'type': str|None, 'command': str|None}. Type is one of:
    'pre-commit-framework', 'husky', 'lint-staged', 'custom-scripts'.
    Command is a suggested invocation suitable for project_policy.command."""
    if (root / ".pre-commit-config.yaml").exists():
        return {
            "type": "pre-commit-framework",
            "command": "pre-commit run --all-files",
        }
    if (root / ".husky").is_dir():
        # husky usually pairs with lint-staged; check for that
        if (root / ".lintstagedrc.js").exists() or (root / ".lintstagedrc.json").exists() \
                or (root / ".lintstagedrc.cjs").exists() or _has_lint_staged_in_pkg(root):
            return {
                "type": "husky+lint-staged",
                "command": "npx lint-staged --diff main",
            }
        return {
            "type": "husky",
            "command": ".husky/pre-commit",
        }
    if (root / "scripts" / "git-hooks").is_dir():
        # Custom scripts pattern (e.g. AssetSolvo)
        return {
            "type": "custom-scripts",
            "command": "bash scripts/git-hooks/pre-commit",
        }
    if (root / ".githooks").is_dir():
        return {
            "type": "custom-scripts",
            "command": "bash .githooks/pre-commit",
        }
    return {"type": None, "command": None}


def _has_lint_staged_in_pkg(root: Path) -> bool:
    pkg = root / "package.json"
    if not pkg.exists():
        return False
    try:
        data = json.loads(pkg.read_text(errors="ignore"))
        return "lint-staged" in data
    except (json.JSONDecodeError, OSError):
        return False


# ---------- CI ----------

def _detect_ci(root: Path) -> dict:
    """Return {'present': bool, 'providers': list[str]}."""
    providers: list[str] = []
    gh_dir = root / ".github" / "workflows"
    if gh_dir.is_dir() and any(gh_dir.glob("*.yml")):
        providers.append("github-actions")
    if (root / ".gitlab-ci.yml").exists():
        providers.append("gitlab-ci")
    if (root / ".circleci" / "config.yml").exists():
        providers.append("circleci")
    if (root / "azure-pipelines.yml").exists():
        providers.append("azure-pipelines")
    return {"present": bool(providers), "providers": providers}


# ---------- Existing .claude config ----------

def _detect_claude_config(root: Path) -> dict:
    """Document what's already in .claude/. Used by merge-aware init
    (v0.8.0) to decide whether to additive-merge or just copy."""
    cdir = root / ".claude"
    if not cdir.is_dir():
        return {
            "exists": False,
            "settings_json": False,
            "settings_local_json": False,
            "agents_dir_populated": False,
            "projects_history_present": False,
        }
    agents = cdir / "agents"
    return {
        "exists": True,
        "settings_json": (cdir / "settings.json").exists(),
        "settings_local_json": (cdir / "settings.local.json").exists(),
        "agents_dir_populated": agents.is_dir() and any(agents.glob("*.md")),
        "projects_history_present": (cdir / "projects").is_dir(),
    }


# ---------- Behavior tests ----------

def _detect_behavior_tests(root: Path) -> dict:
    """Detect tests/behavior/ presence + count.

    Returns `dir=None` when the directory doesn't exist OR exists but is
    empty (no test files). The caller's snippet generator and gate-default
    logic both depend on test_count > 0, so collapsing 'absent' and
    'empty' avoids edge cases where a placeholder directory triggers
    a behavior_regression block that would immediately fail the gate."""
    bdir = root / "tests" / "behavior"
    if not bdir.is_dir():
        return {"dir": None, "test_count": 0}
    test_files = list(bdir.rglob("test_*.py")) + list(bdir.rglob("*.test.ts")) \
                  + list(bdir.rglob("*.test.tsx")) + list(bdir.rglob("*.spec.ts"))
    if not test_files:
        return {"dir": None, "test_count": 0}
    return {"dir": "tests/behavior", "test_count": len(test_files)}


# ---------- Monorepo signals ----------

_MONOREPO_DIRS = ("apps", "packages", "services", "workspaces")


def _detect_monorepo(root: Path) -> dict:
    """Return {'is_monorepo': bool, 'app_dirs': list[str]}."""
    app_dirs: list[str] = []
    for d in _MONOREPO_DIRS:
        path = root / d
        if path.is_dir():
            # Count immediate subdirs that look like apps/services
            subdirs = [s for s in path.iterdir() if s.is_dir() and not s.name.startswith(".")]
            for s in subdirs:
                # Heuristic: contains a manifest file (pyproject, package.json, Cargo.toml, etc.)
                if any((s / m).exists() for m in (
                    "pyproject.toml", "package.json", "Cargo.toml", "go.mod"
                )):
                    app_dirs.append(f"{d}/{s.name}")
    return {"is_monorepo": len(app_dirs) >= 2, "app_dirs": app_dirs}


# ---------- Pretty-print + snippet generators ----------

def format_summary(detection: dict) -> str:
    """Format detection results as a human-readable summary block.

    Used by `prusik init` to print what was found before scaffolding.
    Returns a multi-line string (no trailing newline).
    """
    lines: list[str] = []

    stacks = detection.get("stacks") or []
    if stacks:
        lines.append(f"  ✓ Tech stacks:       {', '.join(stacks)}")
    else:
        lines.append("  ⚠ Tech stacks:       none detected (greenfield repo?)")

    tc = detection.get("test_commands") or {}
    if tc.get("general"):
        lines.append(f"  ✓ Test command:      {tc['general']}")
    else:
        lines.append("  ⚠ Test command:      none detected")
    if tc.get("behavior"):
        lines.append(f"  ✓ Behavior tests:    tests/behavior/ ({tc['behavior']!r})")
    else:
        lines.append("  · Behavior tests:    none (no tests/behavior/ found)")

    linters = detection.get("linters") or []
    if linters:
        lines.append(f"  ✓ Linters:           {', '.join(linters)}")
    else:
        lines.append("  · Linters:           none detected")

    pc = detection.get("pre_commit") or {}
    if pc.get("type"):
        lines.append(f"  ✓ Pre-commit:        {pc['type']} → {pc['command']!r}")
    else:
        lines.append("  · Pre-commit:        none detected")

    ci = detection.get("ci") or {}
    if ci.get("present"):
        lines.append(f"  ✓ CI:                {', '.join(ci.get('providers', []))}")
    else:
        lines.append("  · CI:                none detected")

    cc = detection.get("claude_config") or {}
    if cc.get("exists"):
        bits: list[str] = []
        if cc.get("settings_json"):
            bits.append("settings.json")
        if cc.get("settings_local_json"):
            bits.append("settings.local.json")
        if cc.get("agents_dir_populated"):
            bits.append("agents/")
        if cc.get("projects_history_present"):
            bits.append("projects/ (CC session history)")
        lines.append(f"  ✓ Existing .claude/: {', '.join(bits) or '(empty dir)'}")
    else:
        lines.append("  · Existing .claude/: fresh (will scaffold from scratch)")

    mr = detection.get("monorepo") or {}
    if mr.get("is_monorepo"):
        lines.append(f"  ✓ Monorepo:          {len(mr['app_dirs'])} apps "
                     f"({', '.join(mr['app_dirs'][:4])}"
                     f"{' …' if len(mr['app_dirs']) > 4 else ''})")

    return "\n".join(lines)


def format_snippets(detection: dict) -> list[str]:
    """Generate copy-paste snippets for things detected but not auto-applied.

    v0.8.4 doesn't auto-mutate sprint-config.yaml (regex on commented YAML
    is fragile). Instead, print snippets the operator can paste into
    `.claude/sprint-config.yaml` to enable detected features.

    Returns a list of (already-formatted) snippet strings, each
    self-contained with a leading explanation.
    """
    snippets: list[str] = []

    pc = detection.get("pre_commit") or {}
    if pc.get("command"):
        snippets.append(
            f"# Detected pre-commit pipeline ({pc['type']}). To run it as part of\n"
            f"# the reviewing phase, add to .claude/sprint-config.yaml:\n"
            f"#\n"
            f"# project_policy:\n"
            f"#   enabled: true\n"
            f"#   command: {pc['command']!r}\n"
            f"#   description: \"Commit-time policy detected at prusik init.\""
        )

    bt = detection.get("behavior_tests") or {}
    tc = detection.get("test_commands") or {}
    if bt.get("dir") and tc.get("behavior"):
        snippets.append(
            f"# Detected tests/behavior/ with {bt['test_count']} test file(s). To run\n"
            f"# them in the reviewing phase, add to .claude/sprint-config.yaml:\n"
            f"#\n"
            f"# behavior_regression:\n"
            f"#   enabled: true\n"
            f"#   command: {tc['behavior']!r}\n"
            f"#   description: \"Behavior-regression suite detected at prusik init.\"\n"
            f"#\n"
            f"# Optional pre-sprint gate (fails sprint-start if dir is empty):\n"
            f"# pre_sprint_gates:\n"
            f"#   behavior_regression:\n"
            f"#     enabled: true\n"
            f"#     check: behavior_regression"
        )

    tsp = detection.get("ts_prove") or {}
    if tsp.get("tsc") or tsp.get("eslint"):
        snippets.append(_format_ts_prove_snippet(tsp))

    return snippets


def _format_ts_prove_snippet(tsp: dict) -> str:
    """The verified TS scope-emitting `prove` recipe (forcing-function finding
    #1 from the first TS adopter). Bare tsc/eslint are silent on success → prove
    can't confirm scope; the scope-emitting variants make a clean check
    TRUE-proven. Adapts to a pnpm/turbo monorepo (the three real wrinkles:
    turbo-cache-swallow, -r over-reach, lint-scope divergence)."""
    wrapper = tsp.get("wrapper")
    exec_prefix = "pnpm --filter=<each checked pkg> exec " if wrapper else ""
    lines = [
        "# Detected a TypeScript stack. Bare `tsc --noEmit` / `eslint` are SILENT",
        "# on success, so `prusik prove` can't confirm scope (a wrong include/glob",
        "# also exits 0 having checked nothing). Use the scope-emitting variants —",
        "# they make a clean check TRUE-PROVEN (real files-checked count). Add to",
        "# package.json scripts (verified recipe):",
        "#",
    ]
    if tsp.get("tsc"):
        lines.append(f'#   "type-check:prove": "prusik prove --kind types -- '
                     f'{exec_prefix}tsc --noEmit --extendedDiagnostics"')
    if tsp.get("eslint"):
        lines.append(f'#   "lint:prove": "prusik prove --kind lint -- '
                     f'{exec_prefix}eslint -f json ."')
    if wrapper:
        lines += [
            "#",
            f"# Monorepo ({wrapper}) — three things the recipe must do:",
            "#   1. invoke the tool DIRECTLY (pnpm exec), NOT via `turbo run` — a",
            "#      cache replay emits no tool output, so prove can't read scope.",
            "#   2. scope to the canonically-checked packages (--filter=<pkg>), not",
            "#      a bare `-r exec` (which trips packages turbo doesn't check).",
            "#   3. chain per-package runs (&&) so exit-0 requires all clean.",
        ]
    return "\n".join(lines)
