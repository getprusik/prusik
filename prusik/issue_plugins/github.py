"""GitHub issue sync via the `gh` CLI — no API tokens needed beyond `gh auth`."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def sync(config: dict, root: Path) -> list[dict]:
    from prusik.issue_plugins import PluginUnavailable

    if not shutil.which("gh"):
        raise PluginUnavailable(
            "`gh` CLI not installed. Install from https://cli.github.com/ and run `gh auth login`."
        )

    repo = config.get("repo")
    filt = config.get("filter", "")
    limit = int(config.get("limit", 500))

    cmd = ["gh", "issue", "list",
           "--json", "number,title,body,labels,state,updatedAt,url",
           "--limit", str(limit)]
    if repo:
        cmd.extend(["--repo", repo])
    if filt:
        # gh passes search via --search; bare state via --state
        cmd.extend(["--search", filt])

    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
    except subprocess.CalledProcessError as e:
        raise PluginUnavailable(f"gh command failed: {e.stderr.strip() or e}")
    except subprocess.TimeoutExpired:
        raise PluginUnavailable("gh command timed out after 60s")

    try:
        issues = json.loads(out.stdout)
    except json.JSONDecodeError as e:
        raise PluginUnavailable(f"gh output not JSON: {e}")

    return [_normalize(i) for i in issues]


def _normalize(gh_issue: dict) -> dict:
    labels = [lbl.get("name") for lbl in gh_issue.get("labels", [])]
    return {
        "id": f"#{gh_issue.get('number')}",
        "title": gh_issue.get("title", ""),
        "body": gh_issue.get("body", "") or "",
        "labels": [lbl for lbl in labels if lbl],
        "status": gh_issue.get("state", "").lower(),
        "updated_at": gh_issue.get("updatedAt", ""),
        "url": gh_issue.get("url", ""),
    }
