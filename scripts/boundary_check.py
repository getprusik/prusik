#!/usr/bin/env python3
"""Open-core boundary gate — no adopter identity in the public surface.

THE INVARIANT
    The public engine surface (what ships to PyPI and the public repo) must
    contain ZERO adopter identities. Adopter identity lives only in the private
    plane (hq/, findings/, the bridge). A future public reader may see the
    engineering *lesson* behind a check and its content-addressed handle
    (`fb-<hash>`), but never *who* reported it.

WHY A GATE (not a per-customer scrub)
    Anonymising names after the fact is remediation that does not scale — at
    N adopters it is N cleanups, each a chance to miss one. This gate makes the
    leak structurally impossible instead: it reads the *private adopter
    registry* (hq/products.local.json — the same file the fleet already uses)
    and fails the commit if ANY registered label/alias appears in the public
    surface. Onboarding an adopter into the registry — which you already do to
    run the fleet — automatically arms the gate against leaking that adopter's
    name. No per-adopter maintenance, ever. Driven by a system-computed signal,
    not a hand-kept denylist, and fail-closed: a missing registry is an error,
    not a silent pass.

PROVENANCE CONVENTION (how to cite a finding in public code)
    Cite the finding by its content-addressed id — `fb-<hash>` — never by
    adopter name. The id reveals nothing; the id->adopter crosswalk stays in the
    private finding record. Keep the lesson, drop the name:
        BAD :  # false confidence (acme fb-90cfcfa8b918)
        GOOD:  # false confidence (fb-90cfcfa8b918)

This is a maintainer/pre-commit tool and is intentionally NOT part of the
shipped `prusik` package (adopters have no reason to audit our boundary).

Exit codes:  0 clean · 1 leak found · 2 registry missing/malformed (strict)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = REPO_ROOT / "hq" / "products.local.json"

# Files/dirs that ship publicly (PyPI sdist/wheel + the public repo). Anything
# NOT listed here is private-by-default and is not scanned — that is where full
# adopter context (findings/, hq/, docs/design-passes/, benchmarks/) lives.
PUBLIC_SURFACE = (
    "prusik",           # the engine package (ships in the wheel)
    "tests",            # ship in the public repo (not the wheel, but public)
    "scripts",          # public maintainer tooling (regen_closures, this gate itself)
    "benchmarks",       # eval corpus the public test suite reads from
    "examples",
    "README.md",
    "CHANGELOG.md",
    "action.yml",
    "pyproject.toml",
    "MANIFEST.in",
    ".gitignore",       # public + can name fixture paths (a stale adopter path leaks)
    ".github",          # public CI / workflows
)

# Tokens that look like adopter names but are generic protocol/role vocabulary,
# never customer identities — exempt by construction so the gate can never be
# tricked into demanding they be scrubbed (doing so would break the bridge).
EXEMPT = {"live-cc", "prusik-author", "prusik"}

# Registry labels shorter than this are too generic to word-match safely; a
# label that short must carry an explicit alias instead (or boundary_ignore).
_MIN_TOKEN_LEN = 3

_SKIP_SUFFIXES = {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
                  ".lock", ".whl", ".gz", ".zip"}
_SKIP_DIRS = {"__pycache__", ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache"}


def load_denylist(registry_path: Path) -> list[str]:
    """Build the identity denylist from the private registry: each product's
    `label` plus any `aliases`. Entries with `boundary_ignore: true` (for a
    label that is an unavoidable common word) are skipped. Raises
    FileNotFoundError / ValueError so callers can fail closed."""
    if not registry_path.is_file():
        raise FileNotFoundError(
            f"no adopter registry at {registry_path}. This gate is driven by the "
            f"private registry (hq/products.local.json); run it from the monorepo, "
            f"or pass --registry. Fail-closed by design — it will not pass blind.")
    data = json.loads(registry_path.read_text())
    products = data.get("products") if isinstance(data, dict) else None
    if not isinstance(products, list) or not products:
        raise ValueError("registry 'products' must be a non-empty list")
    tokens: set[str] = set()
    for i, pr in enumerate(products):
        if not isinstance(pr, dict) or not pr.get("label"):
            raise ValueError(f"products[{i}] needs a non-empty 'label'")
        if pr.get("boundary_ignore"):
            continue
        names = [pr["label"], *pr.get("aliases", [])]
        for name in names:
            name = str(name).strip().lower()
            if name in EXEMPT or len(name) < _MIN_TOKEN_LEN:
                continue
            tokens.add(name)
    return sorted(tokens)


def _imports_hq(path: Path) -> bool:
    """True if a test imports the private `hq` package. sync-public.sh drops
    exactly these tests ('they import hq/, absent in public'), so they are the
    private HQ plane — the control room, which legitimately holds adopter identity
    — and must NOT be scanned as public surface. Same criterion as the sync, so
    the two can't drift."""
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return False
    return bool(re.search(r"(?m)^\s*(from\s+hq[\s.]|import\s+hq\b)", text))


def iter_public_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for entry in PUBLIC_SURFACE:
        p = root / entry
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            for f in p.rglob("*"):
                if f.is_file() and not (set(f.parts) & _SKIP_DIRS) \
                        and f.suffix.lower() not in _SKIP_SUFFIXES:
                    if entry == "tests" and f.suffix == ".py" and _imports_hq(f):
                        continue  # private HQ-plane test — dropped from public sync
                    files.append(f)
    return files


def _flex(token: str) -> str:
    """Regex body matching `token`'s parts with an OPTIONAL separator, so a compound
    registry label (`acme-corp`) is caught glued (`acmecorp`), underscored,
    or spaced — the identity-leak hole a plain `\\btoken\\b` leaves open."""
    parts = re.split(r"[-_ ]+", token)
    return r"[-_ ]?".join(re.escape(p) for p in parts if p)


def scan(root: Path, denylist: list[str]) -> list[dict]:
    """Return one violation record per matching line."""
    if not denylist:
        return []
    # Boundary that treats `_` as a SEPARATOR (unlike `\b`, for which `_` is a word
    # char) and allows a trailing alpha suffix — so an adopter token glued into a
    # snake_case or camelCase identifier is caught: `_acme_`, `acme_like`, `acmeShape`,
    # `acmes` (plural). The old `\b(token)\b` MISSED every one of these — `\b` does not
    # fire at a `_`/token seam, and a letter suffix defeats the trailing `\b` — which is
    # exactly how adopter names leaked into public test identifiers (an all-caps
    # `_<NAME>_SHAPE` constant, a `_<name>_like_project` helper) and went undetected.
    # Each token still matches its parts with an OPTIONAL separator so a compound label
    # (`acme-corp`) is caught glued (`acmecorp`) too. Longer tokens first, so a compound
    # label reports as itself.
    ordered = sorted(denylist, key=len, reverse=True)
    body = "|".join(_flex(t) for t in ordered)
    pats = [re.compile(r"(?<![A-Za-z0-9])(" + body + r")[A-Za-z]*(?![A-Za-z0-9])",
                       re.IGNORECASE)]
    # Also flag the AUTHOR's local identity leaking into the public surface — the
    # machine username, which rides in absolute home paths (`/Users/<user>/…`)
    # baked into committed fixtures/logs (PII + local dir structure). Precise to
    # the real current user, so intentional test canaries (`/Users/secret`,
    # `/Users/x`) never false-positive. Case-sensitive (usernames are).
    local_user = os.path.basename(os.path.expanduser("~")) or os.environ.get("USER", "")
    if local_user and len(local_user) >= 3:
        pats.append(re.compile(r"\b" + re.escape(local_user) + r"\b"))

    violations: list[dict] = []
    for f in iter_public_files(root):
        rel = str(f.relative_to(root))
        for pattern in pats:
            # the PATH itself leaks (a file named test_<adopter>_x.py, a fixture dir)
            pm = pattern.search(rel)
            if pm:
                violations.append({"file": rel, "line": 0, "token": pm.group(1) if pm.groups() else pm.group(0),
                                   "snippet": f"<path> {rel}"})
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for pattern in pats:
                for m in pattern.finditer(line):
                    violations.append({
                        "file": rel,
                        "line": lineno,
                        "token": m.group(1) if m.groups() else m.group(0),
                        "snippet": line.strip()[:120],
                    })
    return violations


def run(registry_path: Path = DEFAULT_REGISTRY, json_output: bool = False,
        strict: bool = True, root: Path = REPO_ROOT) -> int:
    try:
        denylist = load_denylist(registry_path)
    except (FileNotFoundError, ValueError) as e:
        msg = f"boundary-check: {e}"
        if strict:
            print(msg, file=sys.stderr)
            return 2
        print(msg + "\n  (non-strict: treated as pass)", file=sys.stderr)
        return 0

    violations = scan(root, denylist)

    if json_output:
        print(json.dumps({
            "denylist_size": len(denylist),
            "violations": violations,
            "ok": not violations,
        }, indent=2))
    else:
        if not violations:
            print(f"boundary-check: OK — public surface clean against "
                  f"{len(denylist)} registered adopter token(s).")
        else:
            print(f"boundary-check: FAIL — {len(violations)} adopter-identity "
                  f"leak(s) in the public surface:\n")
            for v in violations:
                print(f"  {v['file']}:{v['line']}  «{v['token']}»  {v['snippet']}")
            print("\nFix: drop the adopter name; keep the lesson and cite the "
                  "finding by its `fb-<id>` handle. The name lives in the private "
                  "finding record, not in public source.")
    return 1 if violations else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY,
                    help="path to the private adopter registry "
                         "(default: hq/products.local.json)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--no-strict", action="store_true",
                    help="treat a missing registry as a pass (NOT for CI/publish)")
    ap.add_argument("--root", type=Path, default=REPO_ROOT,
                    help="tree to scan (default: this repo; the sync passes the "
                         "STAGED public tree so it gates the exact bytes it ships)")
    args = ap.parse_args()
    return run(registry_path=args.registry, json_output=args.json,
               strict=not args.no_strict, root=args.root)


if __name__ == "__main__":
    raise SystemExit(main())
