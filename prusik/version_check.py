"""Update-availability check (v0.84.0).

Read-only, best-effort, PULL not push: queries the public GitHub tags API and
compares the newest released version against the installed one. prusik NEVER
phones home — this is the adopter's own tool checking a public registry, exactly
the way `pip`/`npm` check for updates. Offline / rate-limited / unreachable →
returns None and the caller silently skips; it must never error or block.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from prusik import __version__

_REPO = "getprusik/prusik"
_TAGS_URL = f"https://api.github.com/repos/{_REPO}/tags"
_VER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def _parse(v: str) -> tuple[int, int, int] | None:
    m = _VER_RE.match((v or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def is_newer(latest: str, installed: str = __version__) -> bool:
    lp, ip = _parse(latest), _parse(installed)
    return bool(lp and ip and lp > ip)


def _max_tag(names: list[str]) -> str | None:
    parsed = [(p, n) for n in names if (p := _parse(n))]
    return max(parsed)[1] if parsed else None


def _via_gh(timeout: float) -> str | None:
    """Use the adopter's authenticated `gh` CLI — the reliable path for a PRIVATE
    repo (an unauthenticated API call 404s). None if gh is absent / not authed."""
    if not shutil.which("gh"):
        return None
    try:
        r = subprocess.run(["gh", "api", f"repos/{_REPO}/tags", "--jq", ".[].name"],
                           capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return _max_tag(r.stdout.splitlines())


def _via_http(timeout: float) -> str | None:
    """Direct GitHub API — works unauthenticated only if the repo is public;
    honors GITHUB_TOKEN / GH_TOKEN when set (private-repo fallback without gh)."""
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "prusik-version-check"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(_TAGS_URL, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            tags = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    if not isinstance(tags, list):
        return None
    return _max_tag([t.get("name", "") for t in tags if isinstance(t, dict)])


def _changelog_via_gh(timeout: float) -> str | None:
    if not shutil.which("gh"):
        return None
    try:
        r = subprocess.run(
            ["gh", "api", f"repos/{_REPO}/contents/CHANGELOG.md",
             "--jq", ".content"],
            capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    import base64
    try:
        return base64.b64decode(r.stdout).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def _changelog_via_http(timeout: float) -> str | None:
    url = f"https://raw.githubusercontent.com/{_REPO}/main/CHANGELOG.md"
    headers = {"User-Agent": "prusik-version-check"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read().decode("utf-8")
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def changelog_text(timeout: float = 3.0) -> str | None:
    """The repo's CHANGELOG.md (for `prusik update`'s 'what's new'). Authed `gh`
    first (private repo), then raw/token. None on any failure — best-effort."""
    return _changelog_via_gh(timeout) or _changelog_via_http(timeout)


def latest_release(timeout: float = 3.0) -> str | None:
    """The newest `vX.Y.Z` release tag, or None on any failure. Tries the adopter's
    authenticated `gh` first (works for the private repo), then a direct API call
    (public repos, or with a token). PULL only — prusik never reports out."""
    return _via_gh(timeout) or _via_http(timeout)


def check(timeout: float = 3.0) -> tuple[str, str | None, bool]:
    """(installed, latest|None, newer_available). latest is None when the check
    couldn't reach the registry — the caller treats that as 'unknown', not 'old'."""
    latest = latest_release(timeout)
    return __version__, latest, (is_newer(latest) if latest else False)


def _nudge_from(latest: str | None) -> str | None:
    if latest and is_newer(latest):
        return (f"a newer prusik ({latest}) is available — run `prusik update` "
                f"to upgrade + auto-sync templates.")
    return None


def nudge_if_stale(root: Path, throttle_hours: float = 24.0,
                   timeout: float = 2.0) -> str | None:
    """A THROTTLED staleness nudge for the natural flow (sprint-start) — so you're
    reminded to `prusik update` without a separate `doctor` run and without a
    network hit every time. The remote check happens at most once per
    `throttle_hours` (cached at `.sprint/.update-check.json`); otherwise it reads
    the cached answer. None when up to date, recently-checked-clean, or offline.
    Never raises — a nudge must never slow or break sprint-start."""
    import time
    cache = root / ".sprint" / ".update-check.json"
    now = time.time()
    try:
        if cache.exists():
            data = json.loads(cache.read_text())
            if now - float(data.get("checked_at", 0)) < throttle_hours * 3600:
                return _nudge_from(data.get("latest"))   # use cached verdict
    except (OSError, ValueError, TypeError):
        pass
    latest = latest_release(timeout)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"checked_at": now, "latest": latest}))
    except OSError:
        pass
    return _nudge_from(latest)
