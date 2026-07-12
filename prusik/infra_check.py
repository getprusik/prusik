"""Pre-flight infra gate (v0.65.0, an adopter enabler #1).

A verify_command that errors because Postgres or the dev server is down reads as
a false skip/fail — a silent degradation with a misleading exit. Before running a
sprint's verify_commands, health-check the infra the criteria DECLARE (a top-level
`requires:` block in criteria.yaml) and FAIL CLOSED with a clear "unreachable"
message, instead of letting the commands skip or error. No-silent-fallbacks:
required infra down → rc≠0 and the gate blocks, never a green-by-accident.

requires block (all optional — absent = nothing to check, current behavior):

    requires:
      - name: postgres
        tcp: localhost:5432
      - name: app-server
        http: http://localhost:8000/healthz
        expect_status: 200        # optional; default = any HTTP response = up

`tcp` proves a port is listening (DB/redis/etc.) without needing credentials;
`http` proves the server answers (any response = reachable unless expect_status
is set). Both fail fast on a short timeout so the gate doesn't hang on a dead host.
"""

from __future__ import annotations

import socket
import urllib.error
import urllib.request
from pathlib import Path

import yaml

DEFAULT_TIMEOUT = 3.0


def parse_requires(data: dict) -> list[dict]:
    """The `requires:` list from a parsed criteria.yaml (empty if absent/malformed)."""
    reqs = data.get("requires") if isinstance(data, dict) else None
    if not isinstance(reqs, list):
        return []
    return [r for r in reqs if isinstance(r, dict)]


def _check_tcp(target: str, timeout: float) -> tuple[bool, str]:
    host, sep, port = target.partition(":")
    if not sep or not port.isdigit():
        return False, f"bad tcp target {target!r} (want host:port)"
    try:
        with socket.create_connection((host or "localhost", int(port)),
                                      timeout=timeout):
            return True, "reachable"
    except OSError as e:
        return False, f"unreachable ({e.__class__.__name__})"


def _check_http(url: str, expect_status, timeout: float) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            status = resp.status
    except urllib.error.HTTPError as e:
        status = e.code                       # server answered (4xx/5xx) = reachable
    except (urllib.error.URLError, OSError, ValueError) as e:
        return False, f"unreachable ({e.__class__.__name__})"
    if expect_status is not None and status != expect_status:
        return False, f"status {status} != expected {expect_status}"
    return True, f"status {status}"


def check_requirement(req: dict, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Health-check one requirement → {name, kind, target, up, detail}."""
    name = str(req.get("name", "?"))
    if "tcp" in req:
        up, detail = _check_tcp(str(req["tcp"]), timeout)
        return {"name": name, "kind": "tcp", "target": str(req["tcp"]),
                "up": up, "detail": detail}
    if "http" in req:
        up, detail = _check_http(str(req["http"]), req.get("expect_status"), timeout)
        return {"name": name, "kind": "http", "target": str(req["http"]),
                "up": up, "detail": detail}
    return {"name": name, "kind": "?", "target": "",
            "up": False, "detail": "requirement has no tcp/http target"}


def check_all(requires: list[dict], timeout: float = DEFAULT_TIMEOUT) -> list[dict]:
    return [check_requirement(r, timeout) for r in requires]


def verify_criteria_infra(criteria_path,
                          timeout: float = DEFAULT_TIMEOUT) -> tuple[bool, list[dict]]:
    """Load criteria.yaml's `requires:` block and check it. Returns (all_up,
    results). No requires block (or no file) → (True, []) — nothing to gate."""
    p = Path(criteria_path)
    if not p.exists():
        return True, []
    try:
        data = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError:
        return True, []
    requires = parse_requires(data)
    if not requires:
        return True, []
    results = check_all(requires, timeout)
    return all(r["up"] for r in results), results


def run(feature: str, root: Path | None = None, json_output: bool = False,
        timeout: float = DEFAULT_TIMEOUT) -> int:
    from prusik import ledger, schema
    root = root or ledger.project_root()
    criteria_path = schema.criteria_path_for_brief(root / "briefs" / f"{feature}.md")
    ok, results = verify_criteria_infra(criteria_path, timeout)
    if results:
        ledger.append("infra_preflight", feature=feature, ok=ok,
                      down=[r["name"] for r in results if not r["up"]])
    if json_output:
        import json
        print(json.dumps({"ok": ok, "results": results}, indent=2))
        return 0 if ok else 1
    if not results:
        print(f"[infra-check] no `requires:` block in {criteria_path.name} "
              f"— nothing to check.")
        return 0
    for r in results:
        print(f"  {'✓' if r['up'] else '✗'} {r['name']} [{r['kind']}] "
              f"{r['target']} — {r['detail']}")
    if ok:
        print("[infra-check] all required infra reachable.")
        return 0
    print("[infra-check] FAIL — required infra unreachable; verify_commands "
          "would false-skip/error. Bring it up, then retry.")
    return 1
