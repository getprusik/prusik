"""Map content re-verify — the map must not lie about what a sprint touched.

fb-cda76f07f7e8: a "refreshed-map" commit left the auth section describing
the pre-refactor storage model as current — age + fingerprint freshness
passed while the CONTENT was wrong in the exact subsystem the sprint
changed (the other face of closed fb-76ff51b273de's age-vs-content drift).

Mechanical and honest: sprint-start snapshots a per-section hash of
design/map.md; sprint-complete flags every section that REFERENCES a
touched file (path or basename in the section body) yet stayed
byte-identical — by name, with its evidence, as an ADVISORY plus a
`map_reverify_flagged` ledger event. Advisory because prose truth is not
gateable (honest-limits): unchanged-while-referencing is a smell demanding
human re-verification, not a provable defect. No map / no snapshot / no
references ⇒ silent — never a false flag."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

_SNAPSHOT = "map-sections.json"
_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)


def _map_path(root: Path) -> Path:
    return root / "design" / "map.md"


def sections(root: Path) -> dict[str, str]:
    """{heading: body} for every `## ` section of design/map.md."""
    p = _map_path(root)
    if not p.is_file():
        return {}
    text = p.read_text(errors="ignore")
    heads = list(_HEADING_RE.finditer(text))
    out: dict[str, str] = {}
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out[m.group(1)] = text[m.end():end]
    return out


def snapshot(root: Path) -> None:
    """Record per-section content hashes at sprint-start. Best-effort: no
    map means no snapshot file (and no complaint) — dormancy, not failure."""
    secs = sections(root)
    if not secs:
        return
    data = {name: hashlib.sha256(body.encode()).hexdigest()
            for name, body in secs.items()}
    d = root / ".sprint"
    d.mkdir(parents=True, exist_ok=True)
    (d / _SNAPSHOT).write_text(json.dumps(data, indent=2) + "\n")


def _references(body: str, touched: set[str]) -> list[str]:
    refs = []
    for f in sorted(touched):
        base = f.rsplit("/", 1)[-1]
        if f in body or (len(base) > 6 and base in body):
            refs.append(f)
    return refs


def stale_sections(root: Path, touched: set[str]) -> list[dict]:
    """Sections referencing touched files whose content is byte-identical to
    the sprint-start snapshot: [{section, references}]. Empty when the map,
    the snapshot, or the references are absent."""
    snap_path = root / ".sprint" / _SNAPSHOT
    if not snap_path.is_file() or not touched:
        return []
    try:
        snap = json.loads(snap_path.read_text())
    except (OSError, ValueError):
        return []
    out = []
    for name, body in sections(root).items():
        before = snap.get(name)
        if not before:
            continue                      # new section: it changed by existing
        refs = _references(body, touched)
        if not refs:
            continue
        if hashlib.sha256(body.encode()).hexdigest() == before:
            out.append({"section": name, "references": refs})
    return out
