"""The README's checkable claims are held to their source.

A verification tool's own page went stale: it said 98 pinned findings when the closure manifest
held 122, and its Action example pinned `@main`. These two facts have a source in the repo, so a
release that moves the source without the README fails here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import prusik

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_pinned_findings_count_is_stated_once_and_matches_the_manifest():
    closures = json.loads((ROOT / "prusik" / "_closures.json").read_text())
    stated = re.findall(r"pinned to (\d[\d,]*) findings", README)
    assert stated == [str(len(closures))], (
        f"README states {stated}; prusik/_closures.json holds {len(closures)} ids. "
        "State the count once, and update it when the manifest changes.")


def test_action_example_pins_this_release():
    pins = re.findall(r"uses:\s*getprusik/prusik@(\S+)", README)
    assert pins == [f"v{prusik.__version__}"], (
        f"README Action example pins {pins}; this release is v{prusik.__version__}. "
        "A moving branch is not a pin.")
