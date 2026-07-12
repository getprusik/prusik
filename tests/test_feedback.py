"""prusik feedback — structured findings capture (Phase 3, Pillar C, v0.97.0).

The capture end + the canonical findings-spine record schema that the export
(C3) and HQ aggregation (C4) consume unchanged.
"""

from __future__ import annotations

import json
import shutil

from tests._common import _mktmp_project  # noqa: F401
from prusik import feedback


def test_record_schema_has_spine_fields():
    r = feedback.build_record("bug", "  Scoped Coverage  False-Fails ",
                              ts="2026-06-05T10:00:00+00:00", severity="high",
                              detail="repro: pytest <2 files> --cov")
    assert r["id"] == "fb-" + r["content_hash"]
    assert r["kind"] == "bug" and r["severity"] == "high"
    assert r["title"] == "Scoped Coverage  False-Fails"   # trimmed, inner kept
    assert r["status"] == "open"                           # HQ-owned lifecycle
    assert r["prusik_version"]                             # auto-filled
    # every field the spine needs is present at capture
    for k in ("id", "ts", "kind", "title", "content_hash", "status",
              "prusik_version", "phase", "feature"):
        assert k in r


def test_content_hash_stable_for_dedup():
    """Same kind+title (whitespace/case-insensitive) → same content_hash, so the
    HQ spine collapses re-files and counts recurrence."""
    a = feedback.content_hash("bug", "Scoped coverage false-fails")
    b = feedback.content_hash("bug", "  scoped   COVERAGE false-fails ")
    c = feedback.content_hash("friction", "Scoped coverage false-fails")
    assert a == b           # normalized
    assert a != c           # kind is part of the key


def test_file_and_load_roundtrip_is_append_only_jsonl():
    tmp = _mktmp_project()
    try:
        feedback.file_feedback(tmp, "friction", "deviations re-stales evidence")
        feedback.file_feedback(tmp, "request", "config-aware test baselines",
                               severity="med")
        lines = (tmp / ".sprint" / "feedback.jsonl").read_text().splitlines()
        assert len(lines) == 2                       # one record per line
        recs = feedback.load(tmp)
        assert [r["kind"] for r in recs] == ["friction", "request"]
        assert all(json.loads(line)["id"].startswith("fb-") for line in lines)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_load_empty_and_append_never_raises():
    tmp = _mktmp_project()
    try:
        assert feedback.load(tmp) == []              # nothing filed yet
        # zero-ceremony: file with no prior .sprint, still works
        rec = feedback.file_feedback(tmp, "bug", "x")
        assert feedback.append(tmp, rec) is True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
