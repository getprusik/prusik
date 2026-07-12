"""No-skew guarantee (v0.86.0): the engine auto-syncs templates when it's ahead,
so a sprint never starts on stale agents — closing the package↔templates loophole."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import prusik
from prusik import refresh, manifest


def _proj(surface: str | None):
    d = Path(tempfile.mkdtemp(prefix="kit-skew-"))
    (d / ".claude").mkdir()
    if surface is not None:
        m = {"manifest_schema": manifest.SCHEMA, "created_with": surface,
             "kit_version": surface, "template_surface_version": surface}
        manifest.manifest_path(d / ".claude").write_text(json.dumps(m))
    return d


def test_skew_true_when_engine_ahead_false_when_current_or_no_manifest():
    d = _proj("0.50.0")                     # templates way behind the engine
    try:
        eng, tmpl, skewed = refresh.template_skew(d)
        assert eng == prusik.__version__ and tmpl == "0.50.0" and skewed is True
    finally:
        shutil.rmtree(d, ignore_errors=True)

    d = _proj(prusik.__version__)           # templates match the engine
    try:
        assert refresh.template_skew(d)[2] is False
    finally:
        shutil.rmtree(d, ignore_errors=True)

    d = _proj(None)                         # pre-manifest project → don't touch
    try:
        assert refresh.template_skew(d)[2] is False
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_auto_sync_runs_on_skew_noop_otherwise_and_respects_optout(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(refresh, "run", lambda *a, **k: called.__setitem__("n", called["n"] + 1))

    d = _proj("0.50.0")                     # skewed
    try:
        assert refresh.auto_sync_if_skewed(d, {}) is True
        assert called["n"] == 1
        called["n"] = 0
        assert refresh.auto_sync_if_skewed(d, {"auto_refresh_on_skew": False}) is False
        assert called["n"] == 0             # opt-out honored
    finally:
        shutil.rmtree(d, ignore_errors=True)

    d = _proj(prusik.__version__)           # current → no sync
    try:
        called["n"] = 0
        assert refresh.auto_sync_if_skewed(d, {}) is False
        assert called["n"] == 0
    finally:
        shutil.rmtree(d, ignore_errors=True)
