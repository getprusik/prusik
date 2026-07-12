"""Detector registry.

Mirrors the discovery_plugins convention: an explicit, greppable built-in
registry — no import-time magic. `load()` resolves the active detector set:
built-ins + opt-in project-local detectors from `.claude/detectors/*.py`,
filtered by config enable/disable.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from prusik.detectors import binding, test_reach
from prusik.detectors.base import Finding, ScanContext  # noqa: F401 (re-export)

# name → detector module (each exposes NAME / DESCRIPTION / detect(ctx))
BUILTIN = {m.NAME: m for m in (binding, test_reach)}

LOCAL_DIR = ".claude/detectors"


def _load_local(root: Path) -> dict:
    """Import project-local detectors from `<root>/.claude/detectors/*.py`.
    Each must expose NAME + detect(ctx). Opt-in, explicit-path, the team's own
    committed code (see the trust note in the design doc). Best-effort: a
    broken file is reported and skipped, never fatal."""
    out: dict = {}
    d = Path(root) / LOCAL_DIR
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.py")):
        if f.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                f"prusik_local_detector_{f.stem}", f)
            if spec is None or spec.loader is None:
                raise ImportError(f"no import spec for {f.name}")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:  # noqa: BLE001 — never let a local file crash a scan
            print(f"[prusik] skipped local detector {f.name}: {e}", file=sys.stderr)
            continue
        if hasattr(mod, "NAME") and callable(getattr(mod, "detect", None)):
            out[mod.NAME] = mod
        else:
            print(f"[prusik] {f.name} is not a detector (needs NAME + detect) "
                  f"— skipped", file=sys.stderr)
    return out


def load(root: Path | None = None, config: dict | None = None,
         allow_local: bool = True) -> dict:
    """Resolve the active detector set (name → module).

    config keys (all optional):
      enabled: [names]              — restrict to these (default: all registered)
      disabled: [names]             — drop these
      allow_local_detectors: bool   — default True (CLI --no-local-detectors → allow_local=False)
    """
    config = config or {}
    detectors = dict(BUILTIN)
    if allow_local and config.get("allow_local_detectors", True):
        local = _load_local(root or Path.cwd())
        if local:
            print(f"[prusik] loaded {len(local)} project-local detector(s) "
                  f"from {LOCAL_DIR}/: {', '.join(sorted(local))}", file=sys.stderr)
        detectors.update(local)

    enabled = config.get("enabled")
    if enabled:
        enabled = set(enabled)
        detectors = {k: v for k, v in detectors.items() if k in enabled}
    for name in (config.get("disabled") or []):
        detectors.pop(name, None)
    return detectors
