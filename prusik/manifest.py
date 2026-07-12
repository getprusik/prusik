"""Single-writer, schema-versioned, self-migrating prusik manifest.

Prusik's own provenance record (`.claude/.prusik-manifest.json`). v0.13.0
replaces the prior accretive, multi-writer, contract-less fields with one
model and one write path so the version invariant cannot be violated by
construction and the manifest can evolve safely.

The bug this fixes: `prusik init` wrote `kit_version`; `prusik refresh` wrote
`last_refresh_version` but never `kit_version`; `prusik doctor` reported
`kit_version` and never read `last_refresh_version` — so a refreshed
project reported its *init* version forever. That is prusik's own
false-clean meta-class (a status signal diverging from deployed reality)
turned on `prusik doctor`. The cure is a defined contract, one writer, and
migration — not a stamped line.

Field contracts (schema 2):
  manifest_schema            int   — schema version of THIS file
  created_with               str   — prusik version that first scaffolded
                                      (immutable; archaeology/repro only)
  template_surface_version   str   — prusik version the CURRENTLY DEPLOYED
                                      template files correspond to. THE
                                      live invariant; what doctor reports.
  history                  list[{command,version,at,files_changed,...}]
                                    — append-only audit; current state is
                                      DERIVED from history[-1], not stored
                                      twice (single source of truth).
  files                    list[{path,hash}]  — preserved byte-for-byte
                                      (refresh + uninstall depend on it).
  directories_created      list[str]          — preserved (uninstall).
  gitignore_block_added    bool               — preserved (uninstall).
  detection                dict|absent        — drift baseline.
  detection_baseline       str (optional)     — provenance of `detection`
                                      when backfilled by refresh.
  installed_at             str                — preserved.

Read/write separation: `load()` migrates the IN-MEMORY dict and never
writes. A read-only command (doctor) must not mutate the manifest as a
side effect of being run. Only `save()` persists, and only commands that
write the template surface call `record_surface_write` first.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any
from pathlib import Path

SCHEMA = 2

# Manifest filename. (Renamed from `.prusik-manifest.json` in prusik→prusik
# rename; the back-compat fallback was removed in v0.33.0 once the sole
# install was migrated — pre-rename installs must rename the file or re-init.)
# The `kit_version` FIELD is intentionally NOT renamed — it is a
# legacy-read-only key on pre-v0.13.0 manifests; renaming it would break the
# in-memory schema migration that reads them.
MANIFEST_NAME = ".prusik-manifest.json"


def manifest_path(claude_dir: Path) -> Path:
    """Canonical manifest path."""
    return Path(claude_dir) / MANIFEST_NAME


def find_manifest(claude_dir: Path) -> Path | None:
    """Path to an EXISTING manifest under `claude_dir`, else None. Used
    everywhere a manifest is read (refresh, uninstall, doctor)."""
    p = manifest_path(claude_dir)
    return p if p.exists() else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_manifest(*, version: str, files: list, directories_created: list,
                 gitignore_block_added: bool, detection: dict,
                 installed_at: str | None = None,
                 settings_restore: dict | None = None) -> dict:
    """Build a fresh schema-2 manifest (called by `prusik init`). init genuinely
    deploys the current template surface, so both created_with and
    template_surface_version are this version.

    `settings_restore` (v0.52.1): when init MERGES into a pre-existing
    settings.json (so the file isn't tracked in `files[]` and uninstall's file
    loop never touches it), this records `{premerge, postmerge_sha}` so uninstall
    can revert it to the exact pre-merge content — making uninstall provably
    clean even for repos already using Claude Code."""
    at = installed_at or _now()
    m = {
        "manifest_schema": SCHEMA,
        "created_with": version,
        "template_surface_version": version,
        "installed_at": at,
        "files": files,
        "directories_created": directories_created,
        "gitignore_block_added": gitignore_block_added,
        "detection": detection,
        "history": [{
            "command": "init", "version": version, "at": at,
            "files_changed": len(files),
        }],
    }
    if settings_restore is not None:
        m["settings_restore"] = settings_restore
    return m


def _migrate(m: dict) -> dict:
    """Idempotent, additive, truth-preserving migration to schema SCHEMA.

    Absent `manifest_schema` ⇒ treat as schema 0 (the pre-v0.13.0 ad-hoc
    shape). Never deletes a key (an older binary reading a newer manifest
    still finds its fields). Crucially the migration must NOT fabricate
    currency: template_surface_version is reconstructed from the best
    EXISTING evidence (last_refresh_version, else kit_version) — never set
    to the running binary's version, because the surface is not current
    until a refresh actually runs. The repair must not reintroduce the
    exact false-clean it exists to remove."""
    if not isinstance(m, dict):
        return m
    if m.get("manifest_schema") == SCHEMA:
        return m  # already current — idempotent no-op

    if "created_with" not in m:
        m["created_with"] = m.get("kit_version")
    if "template_surface_version" not in m:
        m["template_surface_version"] = (
            m.get("last_refresh_version") or m.get("kit_version"))
    if "history" not in m:
        hist: list = []
        if m.get("installed_at") or m.get("kit_version"):
            hist.append({
                "command": "init", "version": m.get("kit_version"),
                "at": m.get("installed_at"),
                "files_changed": len(m.get("files", []) or []),
                "reconstructed": True,
            })
        if m.get("last_refresh_version") or m.get("last_refresh_at"):
            hist.append({
                "command": "refresh",
                "version": m.get("last_refresh_version"),
                "at": m.get("last_refresh_at"),
                "files_changed": len(m.get("files", []) or []),
                "reconstructed": True,
            })
        m["history"] = hist
    m["manifest_schema"] = SCHEMA
    return m


def load(manifest_path: Path) -> dict | None:
    """Read + migrate (in memory; does NOT write). None if absent/unreadable."""
    p = Path(manifest_path)
    if not p.exists():
        return None
    try:
        m = json.loads(p.read_text())
    except (ValueError, OSError):
        return None
    return _migrate(m)


def record_surface_write(m: dict, *, command: str, version: str,
                         files: list) -> dict:
    """THE single choke point. Every command that writes the template
    surface (init, refresh, any future one) records the write here — the
    version stamp and the file list are updated together, so 'a surface
    write that forgot to bump the version' is structurally impossible.

    v0.20.0: dedup last-entry. live-cc [08:29] surfaced a double-refresh
    bookkeeping noise — two identical history entries 18s apart from
    what was effectively one logical refresh. If the last history entry
    has the SAME (command, version, files_changed) tuple within the past
    minute, coalesce instead of appending. A history entry that doesn't
    reflect a distinct mutation is itself a small-false-clean shape; the
    history should mark events, not invocations."""
    m["manifest_schema"] = SCHEMA
    m["template_surface_version"] = version
    m["files"] = files
    new_entry: dict[str, Any] = {
        "command": command, "version": version, "at": _now(),
        "files_changed": len(files),
    }
    history = m.setdefault("history", [])
    if history:
        last = history[-1]
        same_shape = (
            last.get("command") == command
            and last.get("version") == version
            and last.get("files_changed") == len(files)
        )
        if same_shape and _within_last_minute(last.get("at"), new_entry["at"]):
            # Coalesce — update the timestamp on the existing entry instead
            # of appending a duplicate. The entry still records the most
            # recent occurrence; one row per logical event, not per invocation.
            last["at"] = new_entry["at"]
            return m
    history.append(new_entry)
    return m


def _within_last_minute(prev_ts: str | None, curr_ts: str) -> bool:
    """Return True if prev_ts is within 60s of curr_ts (both ISO 8601).
    Used by record_surface_write to coalesce same-shape entries from
    effectively one logical mutation."""
    if not prev_ts:
        return False
    try:
        prev = datetime.fromisoformat(prev_ts.replace("Z", "+00:00"))
        curr = datetime.fromisoformat(curr_ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    return abs((curr - prev).total_seconds()) <= 60


def record_detection_backfill(m: dict, detection: dict, version: str) -> dict:
    """Refresh-time only: establish a drift baseline for a manifest that
    predated install-time detection. Marked so doctor reports honest
    provenance ('baseline established at refresh') instead of nagging
    'predates detection — re-run prusik init' forever."""
    m["detection"] = detection
    m["detection_baseline"] = f"refresh-backfill@{version}"
    return m


def save(m: dict, manifest_path: Path) -> None:
    """Atomic persist (tmp + os.replace), schema stamped."""
    m["manifest_schema"] = SCHEMA
    p = Path(manifest_path)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=2))
    os.replace(tmp, p)


# ---- typed accessors (no consumer reaches into raw keys) ----

def surface_version(m: dict | None) -> str:
    """The version the deployed template surface corresponds to — what
    `prusik doctor` should report. Falls back through the legacy field for a
    not-yet-migrated dict, never to 'unknown' if any evidence exists."""
    if not m:
        return "unknown"
    return (m.get("template_surface_version")
            or m.get("last_refresh_version")
            or m.get("kit_version") or "unknown")


def created_with(m: dict | None) -> str:
    if not m:
        return "unknown"
    return m.get("created_with") or m.get("kit_version") or "unknown"


def detection_baseline(m: dict | None) -> str | None:
    if not m:
        return None
    return m.get("detection_baseline")
