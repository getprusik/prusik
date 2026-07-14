"""Schema loading + artifact validation.

Brief, scope, and triage-decision schemas live as YAML under templates/.claude/schemas/.
This module parses markdown artifacts into sections and validates them against the schemas.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

KIT_ROOT = Path(__file__).parent
SCHEMA_DIR = KIT_ROOT / "templates" / ".claude" / "schemas"


def load_schema(name: str) -> dict:
    path = SCHEMA_DIR / f"{name}-schema.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def parse_sections(text: str) -> dict[str, str]:
    """Parse markdown into {heading: body}. Uses '## ' as section delimiter."""
    sections: dict[str, str] = {}
    current: str | None = None
    body: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(body).strip()
            current = line.strip()
            body = []
        else:
            if current is not None:
                body.append(line)
    if current is not None:
        sections[current] = "\n".join(body).strip()
    return sections


# v0.10.0 (Fix 3, design-passes/v0.10.0-derive-and-delta-regate.md):
# sections that are non-gating / derived / volatile and MUST be excluded
# from the substantive hash so a rewind that only touches them does not
# bust a still-valid critic verdict. `## Modules touched` is derived as of
# Fix 1 — it is no longer something a critic judges, so hashing it would
# re-trigger the exact 13-scope-critic-rerun waste this fix eliminates.
_NON_SUBSTANTIVE_SECTIONS: tuple[str, ...] = (
    "## Modules touched",
)


def substantive_hash(path: Path, extra_exclude: tuple[str, ...] = ()) -> str:
    """Deterministic 16-hex hash of an artifact's *substantive* sections.

    Content-addresses what a critic actually judges: section bodies with
    whitespace canonicalized (so trivial reformatting across a rewind does
    not bust a valid verdict) and non-gating/derived sections excluded.
    Stable: sections are sorted, whitespace collapsed. A change here means
    a *substantive* change — exactly when a re-gate is warranted; no change
    means the prior verdict carries forward (Tier 3 cure).
    """
    text = Path(path).read_text()
    sections = parse_sections(text)
    excluded = set(_NON_SUBSTANTIVE_SECTIONS) | set(extra_exclude)
    parts: list[str] = []
    for name in sorted(sections):
        if name in excluded:
            continue
        norm = " ".join(sections[name].split())
        parts.append(f"{name}\n{norm}")
    canonical = "\n\n".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _extract_enum_value(body: str) -> str | None:
    """First non-whitespace token of the body.

    v0.4.1: strips markdown wrappers (**S**, *S*, `S`, _S_) before matching
    so a scoping agent that bolds a single-letter enum value doesn't cost
    a round-trip.
    """
    stripped = body.strip()
    if not stripped:
        return None
    from prusik import artifact_variants as av
    tok = av.strip_markdown_wrappers(stripped.split()[0])
    return tok.rstrip(",.")


def extract_list_items(body: str) -> list[str]:
    """Parse markdown TOP-LEVEL bullet items from a section body.

    Recognized bullet forms (must start at column 0 — no leading whitespace):
      - `- item` / `-item`        (canonical and tight forms)
      - `+ item`                  (CommonMark §5.2 alternate marker; v0.6.1 — was
                                   silently dropped, masking new-file bullets
                                   authors wrote at column 0 without `- ` prefix)
      - `* item`                  (CommonMark §5.2 alternate marker)

    Skipped (NOT counted as items):
      - Pure markdown horizontal rules: `---`, `----+`, `* * *`, `___` etc.
        Pre-v0.6.1 these matched the bare `-` prefix and produced phantom
        list items with body `--`, `**`, etc.
      - **Indented sub-bullets** (v0.6.7). Lines starting with whitespace
        are nested under their parent — they describe a single top-level
        item, not separate ones. Pre-v0.6.7 the helper called `.strip()`
        on each line BEFORE checking the bullet marker, which collapsed
        indentation and treated sub-bullets as new top-level entries.
        Surfaced when an M1.S4 plan.md had a `## Modules touched` bullet
        with 9 nested sub-bullets describing test coverage; the validator
        reported "modules not in scope.md: ['25', '6', 'Reissue', ...]" —
        the first words of each sub-bullet, treated as phantom modules.
    """
    items = []
    for line in body.splitlines():
        # Skip empty / whitespace-only lines.
        if not line.strip():
            continue
        # v0.6.7 (B13): top-level only. A leading space or tab marks this
        # as a nested sub-bullet (or other indented content); it belongs
        # to the parent item, not as a separate entry.
        if line[0] in (" ", "\t"):
            continue
        s = line.strip()
        # Markdown horizontal rule: 3+ of the same marker, optionally space-
        # separated, with no other content. Per CommonMark §4.1.
        if _is_md_hr(s):
            continue
        if s.startswith("- "):
            items.append(s[2:].strip())
        elif s.startswith("-"):
            items.append(s[1:].strip())
        elif s.startswith("+ "):
            # `+ ` at column 0 is BOTH a bullet marker AND the new-file mark.
            # PRESERVE the `+` so extract_module_token downstream still sees the
            # new-file intent — a bare `+ path` is equivalent to `- + path`.
            # (Finding #8: stripping it made a scoping agent's `+ path` new-file
            # bullet validate as a MISSING existing file, tripping scope→advance.
            # A scope→advance must accept the natural diff-style `+ new/file`.)
            items.append("+ " + s[2:].strip())
        elif s.startswith("* "):
            # `* ` is only an alternate CommonMark bullet marker — no new-file
            # meaning — so strip it like `- `.
            items.append(s[2:].strip())
    return items


# Subsection headings (###/####) under `## Modules touched` whose bullets are
# NON-declarations — exclusions or commentary, NOT touched modules. parse_sections
# splits on `## ` only, so without this a "### NOT in the touch-list (deliberate)"
# subsection's bullets were extracted as declared modules → a false "plan.md adds
# modules not in scope.md" that took mechanical rephrasing to satisfy (it flagged the
# excluded backtick-paths, then the bullet-leading word "The"). fb-085135ece453.
# Module-GROUPING subsections (### Backend, ### API) don't match and are kept.
_NON_DECLARATION_HEADING = re.compile(
    r"\b(not|exclud\w*|deliberate\w*|untouched|non-?goals?|notes?|commentary"
    r"|out[ -]of[ -]scope|do\s+not|won'?t)\b", re.IGNORECASE)


def strip_non_declaration_subsections(body: str) -> str:
    """Drop content under a `###`/`####` subsection that lists NON-declarations
    (exclusions / commentary), leaving only genuinely declared bullets. Used on
    `## Modules touched` so an 'excluded modules' subsection can't masquerade as
    declared modules (fb-085135ece453). A subsection whose heading doesn't match
    the exclusion/commentary pattern (e.g. `### Backend`) is kept."""
    kept: list[str] = []
    skipping = False
    for line in body.splitlines():
        if re.match(r"^#{3,}\s", line):
            skipping = bool(_NON_DECLARATION_HEADING.search(line))
            continue                              # heading itself is never a bullet
        if not skipping:
            kept.append(line)
    return "\n".join(kept)


def _is_md_hr(line: str) -> bool:
    """True iff line is a markdown horizontal rule (3+ of `-`, `*`, or `_`,
    optionally separated by whitespace, with no other characters)."""
    stripped = line.strip()
    if len(stripped) < 3:
        return False
    for marker in ("-", "*", "_"):
        # Strip the marker and whitespace; if nothing remains AND we had
        # at least 3 markers, it's a horizontal rule.
        if marker in stripped and set(stripped) <= {marker, " ", "\t"}:
            if stripped.count(marker) >= 3:
                return True
    return False


def extract_path_token(s: str) -> str:
    """Extract the first whitespace-separated token from a bullet body and
    strip common markdown wrappers + trailing punctuation.

    The wrapper/punctuation stripping is the SAME knowledge the identifier
    near-miss uses — both route through `artifact_variants` so a benign variant
    learned once is known on every comparison surface (the cluster's anti-drift
    fix). Here only the path-safe variants apply (no case/separator folding).

    Examples:
      '`scripts/foo.py`  — does stuff'  → 'scripts/foo.py'
      '**api/billing/** — touched'       → 'api/billing/'
      'web/checkout/, related'           → 'web/checkout/'
      '[see here](api/foo.py) — note'    → 'api/foo.py'
    """
    if not s:
        return ""
    from prusik import artifact_variants as av
    # Markdown link [label](target) — check BEFORE whitespace-split because
    # the label can contain spaces.
    m = re.match(r"^\s*\[[^\]]*\]\(([^)]+)\)", s)
    tok = m.group(1) if m else s.split()[0]
    return av.strip_trailing_punct(av.strip_markdown_wrappers(tok))


def extract_module_token(bullet_body: str) -> tuple[str, bool]:
    """Parse a `## Modules touched` bullet into (path_token, is_new_file).

    v0.5.5: single source of truth for bullet parsing in this section,
    shared by `validate_scope` (existence checks) and
    `consistency._modules_from` (plan ⊆ scope + builder ⊆ plan cross-refs).
    Previously the two diverged — `validate_scope` stripped the `+ ` marker
    and markdown wrappers inline; `_modules_from` called `extract_path_token`
    raw, which captured `+` as a standalone path token for
    "+ `path`" bullets. That produced thousands of false builder-out-of-plan
    violations and deadlocked an otherwise-clean sprint at building → reviewing.

    Handles:
      `path/x.py` — desc                 → ('path/x.py', False)
      path/x.py                           → ('path/x.py', False)
      + `new.py` — desc                   → ('new.py', True)
      + new.py                            → ('new.py', True)
      **+ new.py** — desc                 → ('new.py', True)
      `+ new.py` — desc                   → ('new.py', True)

    Returns ('', False) when no path token can be extracted.
    """
    if not bullet_body:
        return "", False
    from prusik import artifact_variants as av
    # Strip surrounding markdown wrappers so `+ path` inside code/bold/italic
    # spans still registers as a new-file marker (shared wrapper knowledge).
    working = av.strip_markdown_wrappers(bullet_body.lstrip()).lstrip()
    is_new = False
    # `+ ` or bare `+` prefix marks the path as new-this-sprint.
    if working.startswith("+ ") or working.startswith("+"):
        is_new = True
        working = working.lstrip("+").lstrip()
    token = extract_path_token(working)
    return token, is_new


def _validate_field(name: str, body: str, spec: dict) -> list[str]:
    errors: list[str] = []
    ftype = spec.get("type", "text")
    if ftype == "enum":
        val = _extract_enum_value(body)
        allowed = spec.get("values", [])
        if val not in allowed:
            errors.append(f"{name}: value '{val}' not in {allowed}")
    elif ftype == "text":
        words = body.split()
        if "min_words" in spec and len(words) < spec["min_words"]:
            errors.append(f"{name}: needs ≥{spec['min_words']} words (got {len(words)})")
        if "max_words" in spec and len(words) > spec["max_words"]:
            errors.append(f"{name}: exceeds {spec['max_words']} words")
        if "must_contain_any" in spec:
            lower = body.lower()
            ok = any(tok.lower() in lower for tok in spec["must_contain_any"])
            # A numeric/exit-code/threshold success ("the suite exits 0", "0 new
            # failures", "run it 100 times with 0 failures") is concretely measurable
            # but carries none of the prose tokens — the structural gate rejected it
            # while brief-critic PASSED it, so the two disagreed and authors inserted
            # filler ("at least") that added no information (fb-acdc57e48ea9).
            # A regex alternative accepts these directly.
            if not ok and "must_match_any_regex" in spec:
                import re as _re
                ok = any(_re.search(pat, body, _re.IGNORECASE)
                         for pat in spec["must_match_any_regex"])
            if not ok:
                desc = spec.get("must_contain_any_description",
                                f"must contain one of {spec['must_contain_any']}")
                errors.append(f"{name}: {desc}")
    elif ftype == "list":
        items = extract_list_items(body)
        if "min_items" in spec and len(items) < spec["min_items"]:
            errors.append(f"{name}: needs ≥{spec['min_items']} bullet items (got {len(items)})")
        if "allowed_values" in spec:
            bad = [i for i in items if i.split()[0] not in spec["allowed_values"]]
            if bad:
                errors.append(f"{name}: values not allowed: {bad} (allowed: {spec['allowed_values']})")
    return errors


def _validate_sections(text: str, schema: dict) -> list[str]:
    sections = parse_sections(text)
    errors: list[str] = []
    for name, spec in schema.get("required_fields", {}).items():
        section = spec["section"]
        body = sections.get(section, "").strip()
        if not body:
            errors.append(f"Missing required section: {section}")
            continue
        errors.extend(_validate_field(name, body, spec))
    for name, spec in schema.get("optional_fields", {}).items():
        body = sections.get(spec["section"], "").strip()
        if body:
            errors.extend(_validate_field(name, body, spec))
    return errors


def validate_brief(path) -> tuple[bool, list[str]]:
    p = Path(path)
    if not p.exists():
        return False, [f"Brief not found: {path}"]
    text = p.read_text()
    errors = _validate_sections(text, load_schema("brief"))
    return len(errors) == 0, errors


# v0.9.0 — success_criteria sibling-file validation.
#
# Lives at briefs/<feature>.criteria.yaml. Operator decision: sibling-file
# (not brief frontmatter) per separation of concerns — brief is prose,
# criteria are machine-readable test contracts. Validated by brief-lint
# when present; warn-only during v0.9.0→v0.10.0 deprecation window.

CRITERIA_SCHEMA_VERSION = "1.0"


def criteria_path_for_brief(brief_path: Path | str) -> Path:
    """Compute the sibling criteria.yaml path for a brief.md."""
    p = Path(brief_path)
    return p.with_suffix(".criteria.yaml")


# A verify_command is run by the gate as a shell string (`bash -c "<vc>"`) unless
# it is an absolute path — see gate.run_success_criteria. So `pnpm test`,
# `pytest -k x`, `a && b` are all valid and run as commands. Lint must AGREE with
# that exec contract: the only thing we can usefully catch at lint time is a bare
# token that plainly LOOKS like a project script path but doesn't exist (a typo'd
# `scripts/check.sh`). "Real proof, not prose" is NOT enforced here — it is
# enforced at review time by the execution-evidence gate (executed_count), which
# is ungameable; a path-exists check at lint time was a brittle proxy for it.
_SHELL_META = set(" \t&|;<>()$`*?#\n")
_KNOWN_RUNNERS = frozenset({
    "pnpm", "npm", "npx", "yarn", "bun", "deno", "node",
    "python", "python3", "pytest", "tox", "uv", "poetry", "ruff", "mypy",
    "bash", "sh", "zsh", "make", "just", "task", "cargo", "go",
    "tsc", "vitest", "jest", "eslint", "prusik",
})
_SCRIPT_SUFFIXES = (".sh", ".bash", ".py", ".js", ".mjs", ".cjs", ".ts")


def _verify_command_is_bare_path(vc: str) -> bool:
    """True when `vc` is a bare token that clearly denotes a project script path
    (so a missing file is a typo worth flagging), as opposed to a shell command
    the gate will run verbatim. Conservative: anything with shell metacharacters,
    or whose first token is a known runner, is a COMMAND (never path-checked)."""
    if any(c in _SHELL_META for c in vc):
        return False
    first = vc.split("/", 1)[0]
    if first in _KNOWN_RUNNERS:
        return False
    # Looks like a path only if it has a directory part or a script-like suffix;
    # a lone bare word (`check`, an on-PATH alias) is treated as a command.
    return ("/" in vc) or vc.endswith(_SCRIPT_SUFFIXES)


DEFAULT_DEFER_MARKERS = ("browser_smoke",)

# A behaviour/e2e/acceptance test DIRECTORY used as a broad target (not a specific
# file), by CONVENTION rather than one project's layout — so this applies to any
# customer (`tests/e2e`, `acceptance/`, `features/`, …), at a token boundary so
# `tests/behavioral` and `tests/e2e/test_x.py` do NOT match. The convention set is
# deliberately the dirs that plausibly hold live-server/browser smokes.
_DEFER_DIR_RE = re.compile(
    r"(?:^|[\s=])(?:[\w.-]+/)*(?:behaviou?r|e2e|acceptance|smoke|features)/?(?=\s|$)")


def _verify_excludes_markers(vc: str, markers: tuple[str, ...]) -> bool:
    """True when a pytest `-m` expression EXCLUDES every deferred marker (a quoted
    `not (...)` covering each). Scans all `-m` tokens so `python -m pytest … -m
    'not browser_smoke'` is recognised (the module flag is not the marker flag)."""
    for m in re.finditer(r"-m\s+(?:(['\"])(.+?)\1|(\S+))", vc):
        expr = m.group(2) or m.group(3) or ""
        if "not" in expr and all(mk in expr for mk in markers):
            return True
    return False


def _behavior_run_unscoped(vc: str,
                           defer_markers: tuple[str, ...] = DEFAULT_DEFER_MARKERS) -> bool:
    """A verify_command that runs a behaviour/e2e test DIRECTORY broadly WITHOUT
    excluding the project's deferred markers (`reviewing_defer_markers`) and WITHOUT
    delegating to the prusik gate. Such a command runs live-server/browser smokes that
    can't reliably pass at criterion-verify time (fb-cc918dfe40b8). Convention-general
    and config-driven — applies to any customer, not one project's path/marker. A
    specific test FILE, a `not <markers>` scope, or any `prusik` invocation is fine."""
    if re.search(r"\bprusik\b", vc):                       # goes through the 6-phase gate
        return False
    if not _DEFER_DIR_RE.search(vc):                       # not a broad behaviour-dir run
        return False
    return not _verify_excludes_markers(vc, defer_markers or DEFAULT_DEFER_MARKERS)


def validate_criteria_file(path, project_root: Path | None = None,
                           defer_markers: tuple[str, ...] = DEFAULT_DEFER_MARKERS
                           ) -> tuple[bool, list[str]]:
    """Validate briefs/<feature>.criteria.yaml shape + verify_command runnability.

    Returns (ok, errors). Schema:
        schema_version: "1.0"
        criteria:
          - id: <slug>
            description: <prose>
            verify_command: <shell command OR path to an existing script>
            expected_exit: <int 0-255, default 0>
            kind: tests|lint|types           # optional — arms execution-evidence
            min_executed: <int ≥ 1, default 1>  # optional — min real work required
            prove_red: true                  # optional — require a captured RED baseline
                                             #   (acceptance-TDD: the verify must FAIL
                                             #   without the change, proving it's
                                             #   load-bearing, not vacuous-green)

    Rules (errors, not warnings):
        - schema_version must be exactly "1.0"
        - criteria must be a non-empty list
        - each entry: id, description, verify_command all required non-empty
        - id values unique within file
        - verify_command: a shell command (`pnpm test`, `pytest -k x`, `a && b`)
          is accepted as-is — it runs at review time via the gate's `bash -c`,
          and the execution-evidence gate is what enforces a real proof. ONLY a
          bare token that looks like a project script path (has a dir part or a
          .sh/.py/… suffix) is required to exist at lint time, to catch typos.
        - expected_exit if present must be int in [0, 255]
    """
    p = Path(path)
    if not p.exists():
        return False, [f"Criteria file not found: {path}"]
    try:
        data = yaml.safe_load(p.read_text())
    except yaml.YAMLError as e:
        return False, [f"YAML parse error: {e}"]
    if data is None:
        return False, ["Criteria file is empty"]
    if not isinstance(data, dict):
        return False, ["Top-level must be a mapping (got "
                       f"{type(data).__name__})"]

    errors: list[str] = []

    sv = data.get("schema_version")
    if sv != CRITERIA_SCHEMA_VERSION:
        errors.append(f"schema_version must be '{CRITERIA_SCHEMA_VERSION}'; "
                      f"got {sv!r}")

    criteria = data.get("criteria")
    if criteria is None:
        errors.append("Missing required key: criteria")
        return False, errors
    if not isinstance(criteria, list):
        errors.append(f"criteria must be a list (got {type(criteria).__name__})")
        return False, errors
    if not criteria:
        errors.append("criteria list must be non-empty")
        return False, errors

    root = project_root or Path.cwd()
    seen_ids: set[str] = set()
    for idx, entry in enumerate(criteria):
        prefix = f"criteria[{idx}]"
        if not isinstance(entry, dict):
            errors.append(f"{prefix}: entry must be a mapping (got "
                          f"{type(entry).__name__})")
            continue
        cid = entry.get("id")
        desc = entry.get("description")
        vc = entry.get("verify_command")
        exp_exit = entry.get("expected_exit", 0)
        blocked = bool(entry.get("blocked_external"))

        if not cid or not isinstance(cid, str):
            errors.append(f"{prefix}: 'id' is required and must be a non-empty string")
        elif cid in seen_ids:
            errors.append(f"{prefix}: duplicate id {cid!r}")
        else:
            seen_ids.add(cid)

        if not desc or not isinstance(desc, str):
            errors.append(f"{prefix} (id={cid!r}): 'description' is required and "
                          "must be a non-empty string")

        # v0.81.0 (#16) — a blocked-external criterion is deferred, not run, so it
        # needs a justification, not a verify_command. Everything else still does.
        if blocked:
            if not entry.get("blocked_reason") or \
                    not isinstance(entry.get("blocked_reason"), str):
                errors.append(f"{prefix} (id={cid!r}): blocked_external requires a "
                              "non-empty 'blocked_reason' (what external setup is "
                              "needed) — a deferral must be justified, not silent.")
        elif not vc or not isinstance(vc, str):
            errors.append(f"{prefix} (id={cid!r}): 'verify_command' is required "
                          "and must be a non-empty string")
        elif _verify_command_is_bare_path(vc) and not (root / vc).resolve().exists():
            errors.append(
                f"{prefix} (id={cid!r}): verify_command looks like a script path "
                f"but no file exists at {vc!r} (relative to project root). A "
                f"verify_command may be EITHER a shell command that proves the "
                f"criterion (e.g. 'pnpm --filter web test', 'pytest tests/x.py "
                f"-k case', 'make check') OR a path to an existing script (e.g. "
                f"'scripts/verify-x.sh'). It runs at review time and the "
                f"execution-evidence gate is what enforces a real proof — so if "
                f"you meant a command, write it as one; if you meant a script, "
                f"create it first (acceptance-TDD: the failing proof precedes the "
                f"sprint that greens it).")

        if (not blocked and isinstance(vc, str) and vc
                and _behavior_run_unscoped(vc, defer_markers)):
            mk = " or ".join(defer_markers)
            errors.append(
                f"{prefix} (id={cid!r}): verify_command runs a behaviour/e2e test "
                f"directory broadly without excluding the deferred markers "
                f"({', '.join(defer_markers)}). These markers (reviewing_defer_markers "
                f"in sprint-config) drive a live server/browser and can't reliably pass "
                f"at criterion-verify time. Add `-m 'not ({mk})'`, target a specific "
                f"test file, or delegate to the prusik gate which applies the markers.")

        if not isinstance(exp_exit, int) or not (0 <= exp_exit <= 255):
            errors.append(f"{prefix} (id={cid!r}): expected_exit must be int in "
                          f"[0, 255]; got {exp_exit!r}")

        # Execution-evidence (optional): kind makes the criterion prove REAL WORK
        # ran, not just exit 0 — a test/lint/type verify that exits clean with
        # nothing executed then FAILS (the false-clean prove-it-fires catches).
        kind = entry.get("kind")
        if kind is not None and kind not in ("tests", "lint", "types"):
            errors.append(f"{prefix} (id={cid!r}): kind must be one of "
                          f"tests|lint|types (or omitted); got {kind!r}")
        min_exec = entry.get("min_executed")
        if min_exec is not None and (not isinstance(min_exec, int) or min_exec < 1):
            errors.append(f"{prefix} (id={cid!r}): min_executed must be an int ≥ 1; "
                          f"got {min_exec!r}")
        pr = entry.get("prove_red")
        if pr is not None and not isinstance(pr, bool):
            errors.append(f"{prefix} (id={cid!r}): prove_red must be a boolean; "
                          f"got {pr!r}")

    errors.extend(_validate_requires(data.get("requires")))
    return len(errors) == 0, errors


def _validate_requires(requires) -> list[str]:
    """Validate the OPTIONAL pre-flight infra `requires:` block (v0.65.0). Each
    entry needs a non-empty `name` and exactly one of `tcp` (host:port) / `http`
    (a URL); `expect_status` if present is an int in [100, 599]."""
    if requires is None:
        return []
    if not isinstance(requires, list):
        return [f"requires must be a list (got {type(requires).__name__})"]
    errs: list[str] = []
    for i, r in enumerate(requires):
        pre = f"requires[{i}]"
        if not isinstance(r, dict):
            errs.append(f"{pre}: entry must be a mapping")
            continue
        if not r.get("name") or not isinstance(r.get("name"), str):
            errs.append(f"{pre}: 'name' is required and must be a non-empty string")
        targets = [k for k in ("tcp", "http") if k in r]
        if len(targets) != 1:
            errs.append(f"{pre}: need exactly one of 'tcp' / 'http' (got {targets})")
        if "tcp" in r:
            tcp = str(r["tcp"])
            host, sep, port = tcp.partition(":")
            if not sep or not port.isdigit():
                errs.append(f"{pre}: tcp must be 'host:port' (got {tcp!r})")
        if "http" in r and not str(r["http"]).startswith(("http://", "https://")):
            errs.append(f"{pre}: http must be a URL (got {r['http']!r})")
        es = r.get("expect_status")
        if es is not None and (not isinstance(es, int) or not (100 <= es <= 599)):
            errs.append(f"{pre}: expect_status must be int in [100, 599]; got {es!r}")
    return errs


def load_criteria(path) -> list[dict]:
    """Load and return the criteria list (no validation — caller validates).
    Returns [] on missing file or empty list."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = yaml.safe_load(p.read_text())
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []
    criteria = data.get("criteria") or []
    return criteria if isinstance(criteria, list) else []


# v0.12.0 — reviewer execution-evidence (Candidate F).
#
# The proven v0.9.0 success_criteria pattern (declared command → real exit →
# captured output → ledger), generalized from the *outward* sprint-complete
# gate to the *inward* reviewer gate. A reviewer no longer advances on a PASS
# token alone; the gate honors PASS only against a machine-captured evidence
# manifest prusik's own `prusik gate capture` wrapper produced — never narrated
# by the agent. Closes the false-clean class where a gate emits PASS while the
# work it gates silently did not execute (R1 errored-phase, R2 zero-collected,
# auto-skip, declared-but-empty).
#
# Honest boundary (§4): this proves a phase *executed and was non-empty*. It
# does NOT certify the executed test exercised the *correct* surface (semantic
# adequacy) — that stays irreducibly human (mission boundary). And the
# anti-fabrication guarantee is "an agent following its role-spec cannot ship
# a passing-but-unexecuted phase", not cryptographic forgery-resistance: the
# PASS bar moves from one word to a structurally-consistent prusik-captured
# manifest bound to the worktree hash. Stated, not over-claimed.

EVIDENCE_SCHEMA_VERSION = "1.0"
EVIDENCE_CAPTURED_BY = "kit-gate-capture"


def evidence_path_for(reports_dir, phase: str) -> Path:
    """reports/<feature>/<phase>.evidence.json — sibling of <phase>.txt."""
    return Path(reports_dir) / f"{phase}.evidence.json"


def validate_evidence_file(path) -> tuple[bool, list[str]]:
    """Validate a reviewer execution-evidence manifest.

    Returns (ok, errors). Schema:
        schema_version: "1.0"
        entries:
          - phase: <str>
            command: <str>             # verbatim command the wrapper ran
            exit_code: <int>
            nonempty_primitive: {kind: <str>, value: <int >= 0}
            output_sha: <non-empty str>   # sha256 of captured stdout+stderr
            worktree_hash: <non-empty str>  # binds evidence to judged code
            captured_by: "kit-gate-capture" # provenance: prusik wrapper, not agent

    Structural validity only — the gate separately enforces exit/primitive
    consistency against the PASS claim and freshness against the worktree.
    """
    p = Path(path)
    if not p.exists():
        return False, [f"Evidence file not found: {path}"]
    try:
        data = json.loads(p.read_text())
    except (ValueError, OSError) as e:
        return False, [f"JSON parse error: {e}"]
    if not isinstance(data, dict):
        return False, [f"Top-level must be an object (got {type(data).__name__})"]

    errors: list[str] = []
    sv = data.get("schema_version")
    if sv != EVIDENCE_SCHEMA_VERSION:
        errors.append(f"schema_version must be '{EVIDENCE_SCHEMA_VERSION}'; "
                      f"got {sv!r}")

    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        errors.append("entries must be a non-empty list")
        return len(errors) == 0, errors

    for idx, entry in enumerate(entries):
        pfx = f"entries[{idx}]"
        if not isinstance(entry, dict):
            errors.append(f"{pfx}: entry must be an object")
            continue
        if not entry.get("phase") or not isinstance(entry.get("phase"), str):
            errors.append(f"{pfx}: 'phase' required non-empty string")
        if not entry.get("command") or not isinstance(entry.get("command"), str):
            errors.append(f"{pfx}: 'command' required non-empty string")
        if not isinstance(entry.get("exit_code"), int):
            errors.append(f"{pfx}: 'exit_code' required int")
        np = entry.get("nonempty_primitive")
        if not isinstance(np, dict):
            errors.append(f"{pfx}: 'nonempty_primitive' required object")
        else:
            if not np.get("kind") or not isinstance(np.get("kind"), str):
                errors.append(f"{pfx}: nonempty_primitive.kind required string")
            val = np.get("value")
            if not isinstance(val, int) or val < 0:
                errors.append(f"{pfx}: nonempty_primitive.value required int >= 0")
        if not entry.get("output_sha") or not isinstance(entry.get("output_sha"), str):
            errors.append(f"{pfx}: 'output_sha' required non-empty string")
        if not entry.get("worktree_hash") or not isinstance(entry.get("worktree_hash"), str):
            errors.append(f"{pfx}: 'worktree_hash' required non-empty string")
        if entry.get("captured_by") != EVIDENCE_CAPTURED_BY:
            errors.append(f"{pfx}: captured_by must be {EVIDENCE_CAPTURED_BY!r} "
                          f"(evidence must be prusik-captured, not agent-written)")

        # v0.18.0 — F §3.5 companion #1: baseline-honesty.
        # If a `baseline` field is present, its shape is enforced. The
        # gate-side check (in prusik/gate.py) enforces the substantive rule:
        # empty known_failures without declared scope = rejected.
        bl = entry.get("baseline")
        if bl is not None:
            if not isinstance(bl, dict):
                errors.append(f"{pfx}: 'baseline' must be an object if present")
            else:
                if not bl.get("domain") or not isinstance(bl.get("domain"), str):
                    errors.append(f"{pfx}: baseline.domain required non-empty "
                                  f"string when baseline is declared "
                                  f"(false-clean class: empty baseline w/o "
                                  f"declared scope)")
                if not bl.get("source") or not isinstance(bl.get("source"), str):
                    errors.append(f"{pfx}: baseline.source required non-empty "
                                  f"string when baseline is declared")
                kf = bl.get("known_failures_count")
                if kf is not None and (not isinstance(kf, int) or kf < 0):
                    errors.append(f"{pfx}: baseline.known_failures_count "
                                  f"required int >= 0")

        # v0.18.0 — F §3.5 companion #2: per-skip information for the
        # ground-truth flag heuristic. Captured by the wrapper from pytest's
        # SKIPPED lines; the gate-side flag check (in prusik/gate.py) decides
        # which to surface — the adjudication itself stays human (mission
        # boundary; mechanize the flag, not the call).
        skips = entry.get("skips")
        if skips is not None:
            if not isinstance(skips, list):
                errors.append(f"{pfx}: 'skips' must be a list if present")
            else:
                for j, sk in enumerate(skips):
                    if not isinstance(sk, dict):
                        errors.append(f"{pfx}.skips[{j}]: must be an object")
                        continue
                    for required in ("test_id", "reason"):
                        if not sk.get(required) or not isinstance(sk.get(required), str):
                            errors.append(
                                f"{pfx}.skips[{j}]: '{required}' required "
                                f"non-empty string")

    return len(errors) == 0, errors


def load_evidence(path) -> list[dict]:
    """Load evidence entries (no validation — caller validates). [] if absent."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except (ValueError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    entries = data.get("entries") or []
    return entries if isinstance(entries, list) else []


def _validate_milestone(text: str, project_root: Path) -> list[str]:
    """v0.5.0: when sprint-config.yaml has a roadmap block configured,
    scope.md must declare a '## Milestone' section matching the pattern.
    Silent no-op when roadmap isn't configured."""
    config_path = project_root / ".claude" / "sprint-config.yaml"
    if not config_path.exists():
        return []
    try:
        config = yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError:
        return []
    roadmap = config.get("roadmap") or {}
    # Consider the roadmap "configured" only if a non-empty source OR pattern is set.
    source = (roadmap.get("source") or "").strip()
    pattern = (roadmap.get("milestone_pattern") or "").strip()
    if not source and not pattern:
        return []

    sections = parse_sections(text)
    body = sections.get("## Milestone", "").strip()
    if not body:
        return [
            "Missing `## Milestone` section (required when `roadmap` is configured in "
            "sprint-config.yaml). Declare the milestone this sprint advances, e.g.:\n"
            "    ## Milestone\n    M1.S1"
        ]

    milestone = body.split()[0].strip("`*_")
    if pattern:
        try:
            if not re.match(pattern, milestone):
                return [
                    f"Milestone '{milestone}' does not match roadmap.milestone_pattern "
                    f"'{pattern}' (from sprint-config.yaml)"
                ]
        except re.error:
            return [
                f"Invalid roadmap.milestone_pattern regex in sprint-config.yaml: {pattern!r}"
            ]
    return []


def validate_scope(path, project_root: Path | None = None) -> tuple[bool, list[str]]:
    p = Path(path)
    if not p.exists():
        return False, [f"Scope not found: {path}"]
    text = p.read_text()
    errors = _validate_sections(text, load_schema("scope"))
    if project_root:
        errors.extend(_validate_milestone(text, project_root))
    # Cross-ref: modules_touched paths must exist relative to project root.
    # v0.4.1: a `+` prefix on a bullet marks a path as expected-to-be-created
    # by this sprint (new-file sprint — e.g. a docs sprint that produces
    # 5 new ADR files). Those skip the existence check; everything else
    # still must exist.
    if project_root:
        sections = parse_sections(text)
        modules = extract_list_items(strip_non_declaration_subsections(
            sections.get("## Modules touched", "")))
        for m in modules:
            # v0.5.5: delegate to shared helper so `validate_scope` and
            # `consistency._modules_from` can't drift on bullet interpretation.
            token, is_new = extract_module_token(m)
            if not token:
                continue
            if is_new:
                # New-file marker: skip existence check. Typo detection moves
                # to builder time (file create fails).
                continue
            if not (project_root / token).exists():
                errors.append(
                    f"modules_touched: path does not exist: {token}\n"
                    f"    If this is a new file this sprint will create, write\n"
                    f"    the bullet as:  - + {token}    (plus OUTSIDE any backticks),\n"
                    f"    or as:          - + `{token}`"
                )
    return len(errors) == 0, errors


def validate_plan(path, project_root: Path | None = None) -> tuple[bool, list[str]]:
    """Validate plan.md against plan-schema.yaml.

    Structural checks only. Cross-artifact (plan ⊆ scope) lives in
    consistency.plan_within_scope and runs at /sprint-advance from planning.
    The `prusik gate plan <path>` call surfaces it ad-hoc so plan-critic and the
    planner can catch drift before the advance gate fires.
    """
    p = Path(path)
    if not p.exists():
        return False, [f"Plan not found: {path}"]
    text = p.read_text()
    errors = _validate_sections(text, load_schema("plan"))
    if project_root:
        # Infer feature from path shape design/<feature>/plan.md when possible,
        # then run the plan ⊆ scope check if a scope artifact exists.
        try:
            rel = p.resolve().relative_to(project_root.resolve())
            parts = rel.parts
            if len(parts) >= 3 and parts[0] == "design" and parts[-1] == "plan.md":
                feature = parts[1]
                from prusik import consistency
                errors.extend(consistency.plan_within_scope(project_root, feature))
        except (ValueError, OSError):
            pass
    return len(errors) == 0, errors


def validate_triage_decision(path) -> tuple[bool, list[str]]:
    p = Path(path)
    if not p.exists():
        return False, [f"Decision not found: {path}"]
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        return False, [f"Invalid JSON: {e}"]
    required = ["feature", "mode", "reason"]
    errors = [f"Missing field: {k}" for k in required if k not in data]
    if data.get("mode") not in ("solo", "team", "reject"):
        errors.append(f"Invalid mode: {data.get('mode')}")
    return len(errors) == 0, errors
