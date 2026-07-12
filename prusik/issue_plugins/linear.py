"""Linear issue sync — stub.

Production plugins should use Linear's GraphQL API with a LINEAR_API_KEY env var.
This stub keeps the plugin surface honest without hiding the gap.
"""

from __future__ import annotations

from pathlib import Path


def sync(config: dict, root: Path) -> list[dict]:
    from prusik.issue_plugins import PluginUnavailable
    raise PluginUnavailable(
        "Linear plugin is a stub. Implement prusik/issue_plugins/linear.py using "
        "the Linear GraphQL API and a LINEAR_API_KEY env var, then remove this raise."
    )
