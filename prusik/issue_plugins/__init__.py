"""Issue-tracker sync plugins.

Each plugin exposes `sync(config: dict, root: Path) -> list[dict]` returning
a list of issue records in a normalized shape:

    {"id": str, "title": str, "body": str, "labels": [str],
     "status": str, "updated_at": ISO8601, "url": str}

Plugins handle their own authentication. A plugin should raise
`PluginUnavailable` with a clear message when its prerequisites are missing
(e.g., `gh` CLI not installed) so the caller can no-op gracefully.
"""

from __future__ import annotations


class PluginUnavailable(RuntimeError):
    pass


from prusik.issue_plugins import github as _github  # noqa: E402
from prusik.issue_plugins import linear as _linear  # noqa: E402

PLUGINS = {
    "github": _github.sync,
    "linear": _linear.sync,
}


def get(tracker_name: str):
    fn = PLUGINS.get(tracker_name)
    if fn is None:
        raise PluginUnavailable(f"unknown tracker: {tracker_name}. "
                                f"supported: {list(PLUGINS.keys())}")
    return fn
