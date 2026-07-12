"""`prusik agents doctor` — inspect .claude/agents/ for registration-blocking issues.

The #1 landmine in the first trial run was agent frontmatter that parsed as
YAML but didn't match Claude Code's expected shape (tools: [a, b] instead of
tools: a, b). CC silently dropped the agent from its registry. This tool
catches that class of issue and several others before a live session hits them.
"""

from __future__ import annotations

import re
from pathlib import Path

from prusik.ledger import project_root


def doctor(root: Path | None = None) -> int:
    root = root or project_root()
    agents_dir = root / ".claude" / "agents"

    if not agents_dir.exists():
        print(f"[agents doctor] no {agents_dir} directory found.")
        print("  Run `prusik init` to scaffold one.")
        return 1

    files = sorted(agents_dir.glob("*.md"))
    if not files:
        print(f"[agents doctor] no agent files found under {agents_dir}.")
        return 1

    print(f"[agents doctor] inspecting {len(files)} agent file(s):")
    print()

    total_issues = 0
    for f in files:
        text = f.read_text()
        problems = _check_agent(f, text)
        status = "OK" if not problems else "FAIL"
        print(f"  [{status}] {f.name}")
        for p in problems:
            print(f"      - {p}")
            total_issues += 1
    print()

    if total_issues == 0:
        print("All agent definitions look valid.")
        print()
        print("If Claude Code still doesn't see them, the running session cached its")
        print("agent registry at start. RESTART the session to pick them up.")
        print("(`/agents` reloads the interactive picker but NOT the Agent-tool")
        print(" dispatch an orchestrator uses mid-sprint — so a session that")
        print(" dispatches agents must restart to use a new/changed role.)")
        return 0

    print(f"Found {total_issues} issue(s). Most-common fixes:")
    print()
    print("  tools: must be a COMMA-SEPARATED string, not a YAML flow list.")
    print("      Bad:  tools: [Read, Glob, Grep]")
    print("      Good: tools: Read, Glob, Grep")
    print()
    print("  name: should match the filename (without .md).")
    print("      File: cartographer.md  ->  name: cartographer")
    print()
    print("After fixing, RESTART the Claude Code session to pick up the changes")
    print("(`/agents` only reloads the interactive picker, not Agent-tool dispatch).")
    return 1


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _check_agent(path: Path, text: str) -> list[str]:
    problems: list[str] = []

    m = _FRONTMATTER_RE.match(text)
    if not m:
        problems.append("missing YAML frontmatter (file must start with `---\\n...\\n---\\n`)")
        return problems

    fm = m.group(1)

    name_m = re.search(r"^name:\s*(.+?)\s*$", fm, re.MULTILINE)
    if not name_m:
        problems.append("missing required field: name")
    else:
        name = name_m.group(1).strip()
        if not name:
            problems.append("name is empty")
        else:
            if name != path.stem:
                problems.append(
                    f"name '{name}' does not match filename stem '{path.stem}' - "
                    "CC indexes by `name:`, so the filename is cosmetic; but "
                    "mismatches often indicate a copy-paste bug."
                )

    if not re.search(r"^description:\s*\S", fm, re.MULTILINE):
        problems.append("missing required field: description")

    tools_m = re.search(r"^tools:\s*(.*)$", fm, re.MULTILINE)
    if tools_m:
        val = tools_m.group(1).strip()
        if val.startswith("[") or val.endswith("]"):
            problems.append(
                "tools: uses YAML flow-list syntax [a, b, c]. Claude Code's parser "
                "expects comma-separated string: `tools: a, b, c`. Files in this "
                "form are SILENTLY dropped from the Agent-tool registry (see v0.3.2)."
            )

    model_m = re.search(r"^model:\s*(.+?)\s*$", fm, re.MULTILINE)
    if model_m:
        model = model_m.group(1).strip().lower()
        known = {"opus", "sonnet", "haiku", "inherit"}
        if model not in known and not model.startswith("claude-"):
            problems.append(
                f"model: '{model}' is unusual - expected one of {sorted(known)} "
                "or a specific `claude-*` model id"
            )

    # v0.5.2: role-vs-tools mismatch. If the role body declares an Output
    # file but `tools:` lacks Write (and Edit), the agent cannot fulfill
    # its own spec. Silent landmine — agent produces the output as its
    # final message and the parent has to persist it manually. Discovered
    # when live-cc hit this on feature-planner during cli-foundation.
    body = text[m.end():]
    output_anchor = re.search(r"\*\*Output", body, re.IGNORECASE)
    if output_anchor and tools_m:
        # Look for a file-path-with-extension in the next 300 chars of the
        # output declaration block (covers "**Output: foo.md**" and
        # "**Output:** Write to foo.md with:" shapes).
        segment = body[output_anchor.start():output_anchor.start() + 300]
        file_m = re.search(
            r"([\w./{}<>-]+\.(?:md|txt|json|yaml|py))",
            segment,
        )
        if file_m:
            tools_lower = tools_m.group(1).lower()
            if "write" not in tools_lower and "edit" not in tools_lower:
                declared = file_m.group(1)
                problems.append(
                    f"role declares Output: {declared} but `tools:` is read-only "
                    f"(missing Write/Edit). The agent cannot fulfill its spec — "
                    f"add Write to the tools line."
                )

    return problems
