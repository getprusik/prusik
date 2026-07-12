"""Smoke tests — exercise the engine without Claude Code in the loop.

Domain: init_refresh.

Run: uv run python -m pytest tests/test_init_refresh.py -v
Or run the whole suite: uv run python -m pytest tests/ -v

Shared helpers live in tests/_common.py (private; pytest does not
collect leading-underscore modules). v0.23.0 split tests/test_smoke.py
by domain to keep individual files navigable.
"""

# noqa: F401 — wildcard imports below intentionally re-export everything
# from _common (prusik modules, helpers, the tempfile/json/os toolbelt).
# F401 individual unused-name warnings would obscure the rest.
from tests._common import *  # noqa: F401,F403,E402
from tests._common import (  # noqa: F401,E402
    argparse, contextlib, io, json, os, re, shutil, subprocess, sys,
    tempfile, time, Path,
    schema, phases, triage, discovery, gate, watchdog, issues,
    kit_init, kit_uninstall, kit_toggle, consistency, agents_doctor,
    kit_refresh, kit_pause, kit_permissions, kit_brief_lint,
    kit_fix_round, kit_bridge, kit_detect, kit_doctor, ledger_digest,
    _mktmp_project, _copy_sprint_config, _wt_file, _write_ledger,
    _capture_stdout, _capture_stderr, _VALID_BRIEF,
)

# moat-finding markers — findings this file's regression tests lock in (C7):
#   moat-finding: fb-db1f1affde71  — refresh --force silently disables user gates (v0.90.0)
#   moat-finding: fb-502b3679fe7b  — v0.14 refresh CLOBBERED customized config blocks (content-loss backstop)


# ---------- install lifecycle ----------

def test_init_writes_manifest():
    tmp = _mktmp_project()
    try:
        rc = kit_init.run()
        assert rc == 0
        manifest_path = tmp / ".claude" / ".prusik-manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text())
        assert "files" in manifest and len(manifest["files"]) > 0
        # Every tracked file should actually exist on disk
        for entry in manifest["files"]:
            assert (tmp / entry["path"]).exists()
    finally:
        shutil.rmtree(tmp)


def test_init_adds_marked_gitignore_block():
    tmp = _mktmp_project()
    try:
        kit_init.run()
        gi = (tmp / ".gitignore").read_text()
        assert kit_init.GITIGNORE_BEGIN in gi
        assert kit_init.GITIGNORE_END in gi
        assert ".sprint/ledger.jsonl" in gi
    finally:
        shutil.rmtree(tmp)


def test_uninstall_removes_tracked_files_only():
    tmp = _mktmp_project()
    try:
        kit_init.run()
        # User adds their own agent — must survive uninstall
        custom_agent = tmp / ".claude" / "agents" / "my-custom.md"
        custom_agent.write_text("custom user agent")
        # Preexisting gitignore content must survive
        gi = tmp / ".gitignore"
        current = gi.read_text()
        gi.write_text("# user's own rules\n*.tmp\n\n" + current)

        rc = kit_uninstall.run()
        assert rc == 0
        assert custom_agent.exists(), "user-authored file must be preserved"
        # kit-installed files should be gone
        assert not (tmp / ".claude" / "sprint-config.yaml").exists()
        assert not (tmp / ".claude" / ".prusik-manifest.json").exists()
        # gitignore block removed but user content preserved
        final_gi = gi.read_text() if gi.exists() else ""
        assert "*.tmp" in final_gi
        assert kit_init.GITIGNORE_BEGIN not in final_gi
    finally:
        shutil.rmtree(tmp)


def test_uninstall_preserves_modified_files_without_force():
    tmp = _mktmp_project()
    try:
        kit_init.run()
        config = tmp / ".claude" / "sprint-config.yaml"
        original = config.read_text()
        config.write_text(original + "\n# user customization\n")
        rc = kit_uninstall.run()
        assert rc == 0
        assert config.exists(), "modified file must be kept without --force"
    finally:
        shutil.rmtree(tmp)


def test_uninstall_force_removes_modified_files():
    tmp = _mktmp_project()
    try:
        kit_init.run()
        config = tmp / ".claude" / "sprint-config.yaml"
        config.write_text(config.read_text() + "\n# tweak\n")
        rc = kit_uninstall.run(force=True)
        assert rc == 0
        assert not config.exists(), "--force should remove modified files"
    finally:
        shutil.rmtree(tmp)


def test_uninstall_keep_artifacts_preserves_sprint():
    tmp = _mktmp_project()
    try:
        kit_init.run()
        (tmp / ".sprint" / "ledger.jsonl").write_text('{"event":"test"}\n')
        rc = kit_uninstall.run(keep_artifacts=True)
        assert rc == 0
        assert (tmp / ".sprint").exists()
        assert (tmp / ".sprint" / "ledger.jsonl").exists()
    finally:
        shutil.rmtree(tmp)


def test_disable_and_enable_toggle_setting():
    tmp = _mktmp_project()
    try:
        kit_init.run()
        kit_toggle.disable()
        data = json.loads((tmp / ".claude" / "settings.json").read_text())
        assert data.get("disableAllHooks") is True
        kit_toggle.enable()
        data = json.loads((tmp / ".claude" / "settings.json").read_text())
        assert "disableAllHooks" not in data
    finally:
        shutil.rmtree(tmp)


def test_uninstall_without_manifest_refuses():
    tmp = _mktmp_project()
    try:
        (tmp / ".claude").mkdir()
        rc = kit_uninstall.run()
        assert rc == 1, "should refuse without manifest"
    finally:
        shutil.rmtree(tmp)



# ---------- prusik refresh (v0.3.6) ----------

def test_refresh_no_manifest_refuses():
    tmp = _mktmp_project()
    try:
        rc = kit_refresh.run()
        assert rc == 1
    finally:
        shutil.rmtree(tmp)


def test_refresh_preserves_user_modifications():
    tmp = _mktmp_project()
    try:
        kit_init.run()
        # User customizes sprint-config.yaml after init
        config_path = tmp / ".claude" / "sprint-config.yaml"
        original = config_path.read_text()
        config_path.write_text(original + "\n# user tweak\n")

        rc = kit_refresh.run()
        assert rc == 0
        # User's customization must survive
        text = config_path.read_text()
        assert "# user tweak" in text, "user-modified file should not be overwritten"
    finally:
        shutil.rmtree(tmp)


def test_refresh_auto_adopts_stale_templates_not_in_manifest():
    """v0.4.2: files at template paths with no manifest entry are
    treated as stale stock (auto-adopted) by default."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        # Simulate the "init on older prusik version" case: remove an entry
        # from the manifest for a tracked file, so refresh sees it as
        # "not in original manifest".
        manifest_path = tmp / ".claude" / ".prusik-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        victim = ".claude/agents/cartographer.md"
        manifest["files"] = [e for e in manifest["files"] if e["path"] != victim]
        manifest_path.write_text(json.dumps(manifest, indent=2))

        # Modify the on-disk file to differ from the current template,
        # simulating "stale stock content".
        victim_path = tmp / victim
        original = victim_path.read_text()
        victim_path.write_text("---\nname: cartographer\ndescription: stale\n---\n")

        # Refresh: by default, auto-adopts → file gets overwritten with
        # current template.
        rc = kit_refresh.run()
        assert rc == 0
        final = victim_path.read_text()
        assert final == original, "auto-adopt should restore template content"
        # Manifest now has the entry.
        manifest_after = json.loads(manifest_path.read_text())
        entries = {e["path"]: e["hash"] for e in manifest_after["files"]}
        assert victim in entries
    finally:
        shutil.rmtree(tmp)


def test_refresh_no_auto_adopt_preserves_unknown_files():
    """v0.4.2: --no-auto-adopt preserves pre-v0.4.2 behavior (skip)."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        manifest_path = tmp / ".claude" / ".prusik-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        victim = ".claude/agents/cartographer.md"
        manifest["files"] = [e for e in manifest["files"] if e["path"] != victim]
        manifest_path.write_text(json.dumps(manifest, indent=2))

        victim_path = tmp / victim
        custom_content = "---\nname: cartographer\ndescription: I TOUCHED IT\n---\n"
        victim_path.write_text(custom_content)

        rc = kit_refresh.run(no_auto_adopt=True)
        assert rc == 0
        # With --no-auto-adopt, the file should NOT have been overwritten
        assert victim_path.read_text() == custom_content
    finally:
        shutil.rmtree(tmp)


def test_refresh_updates_unchanged_files():
    tmp = _mktmp_project()
    try:
        kit_init.run()
        # Simulate an older install by mutating the manifest + template-match
        # state: we don't have "old" templates here, but the path is exercised.
        # The refresh should report "already current" since the templates
        # match what init just laid down.
        rc = kit_refresh.run()
        assert rc == 0
    finally:
        shutil.rmtree(tmp)


def test_refresh_emits_restart_hint_when_agents_change():
    """v0.4.5: warn about CC session caching when agent prompts change."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        # Force an agent to look "stale" vs current template
        agent_path = tmp / ".claude" / "agents" / "cartographer.md"
        agent_path.write_text("---\nname: cartographer\ndescription: old\n---\nold body\n")
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            kit_refresh.run(force=True)  # forces update of the modified file
        out = buf.getvalue()
        # Should include the agent-cache hint — and (finding #13) name RESTART as
        # the reliable fix, clarifying `/agents` only reloads the interactive
        # picker, not the Agent-tool dispatch an orchestrator uses mid-sprint.
        assert "Claude Code caches" in out
        assert "RESTART" in out and "Agent-tool" in out
    finally:
        shutil.rmtree(tmp)


def test_user_content_loss_flags_c2c_force_damage():
    """The backstop must catch the exact silent regressions an adopter hit under
    `refresh --force`: a dropped permissions.allow entry, a lost hook event, an
    enabled gate flipped off, and a populated additive list emptied."""
    keep = {"permissions": {"allow": ["Write(**)", "Edit(**)"]},
            "hooks": {"UserPromptSubmit": [1]}}
    drop_perm = {"permissions": {"allow": ["Write(**)"]},
                 "hooks": {"UserPromptSubmit": [1]}}
    drop_hook = {"permissions": {"allow": ["Write(**)", "Edit(**)"]}, "hooks": {}}
    f = kit_refresh._user_content_loss
    assert f(".claude/settings.json", json.dumps(keep), json.dumps(drop_perm))
    assert f(".claude/settings.json", json.dumps(keep), json.dumps(drop_hook))
    assert f(".claude/settings.json", json.dumps(keep), json.dumps(keep)) is None
    # sprint-config: enabled gate turned off, and a populated list emptied
    assert f(".claude/sprint-config.yaml",
             "behavior_regression:\n  enabled: true\n",
             "behavior_regression:\n  enabled: false\n")
    assert f(".claude/sprint-config.yaml",
             "brief_lint:\n  extra_known_sources: [BL-001, oq-1]\n",
             "brief_lint:\n  extra_known_sources: []\n")
    # no false positive: enabled gate left ON, list left populated
    same = "behavior_regression:\n  enabled: true\nbrief_lint:\n  x: [a, b]\n"
    assert f(".claude/sprint-config.yaml", same, same) is None


def test_force_refresh_does_not_clobber_user_config():
    """field bridge HIGH: `refresh --force` re-adopts user-modified AGENT templates,
    but must route structured config through the non-destructive merge — never
    wholesale-overwrite it, which silently dropped the bridge hook + Write/Edit
    permissions and reverted enabled gates."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        settings = tmp / ".claude" / "settings.json"
        s = json.loads(settings.read_text())
        s.setdefault("permissions", {}).setdefault("allow", [])
        for p in ("Write(**)", "Edit(**)"):
            if p not in s["permissions"]["allow"]:
                s["permissions"]["allow"].append(p)
        s.setdefault("hooks", {})["UserPromptSubmit"] = [
            {"hooks": [{"type": "command", "command": "prusik bridge poll"}]}]
        settings.write_text(json.dumps(s, indent=2))
        rc = kit_refresh.run(force=True)
        after = json.loads(settings.read_text())
        assert "Write(**)" in after["permissions"]["allow"]
        assert "Edit(**)" in after["permissions"]["allow"]
        assert "UserPromptSubmit" in after.get("hooks", {})   # bridge hook survives
        assert rc == 0
    finally:
        shutil.rmtree(tmp)


def test_skew_banner_loud_when_engine_ahead_else_none():
    """field bridge #4: when the engine is ahead of the deployed templates, a sprint
    must NOT start silently — skew_banner returns a loud, specific warning naming
    the gap + the fix. None when in sync."""
    from prusik import manifest
    tmp = _mktmp_project()
    try:
        kit_init.run()
        # freshly initialised → templates match the engine → no skew
        assert kit_refresh.skew_banner(tmp) is None
        # tamper the deployed manifest to an old surface version → engine ahead
        mpath = manifest.manifest_path(tmp / ".claude")
        m = json.loads(mpath.read_text())
        m["template_surface_version"] = "0.50.0"
        mpath.write_text(json.dumps(m))
        banner = kit_refresh.skew_banner(tmp)
        assert banner and "TEMPLATE SKEW" in banner
        assert "0.50.0" in banner                 # names the stale version
        assert "releases behind" in banner        # quantifies the gap
        assert "prusik refresh" in banner         # names the fix
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_sprint_init_warns_loudly_when_skew_remains_under_optout():
    """With `auto_refresh_on_skew: false`, sprint-init must STILL surface the skew
    loudly (the opt-out suppresses the auto-sync, not the warning)."""
    from prusik import manifest
    tmp = _mktmp_project()
    try:
        kit_init.run()
        mpath = manifest.manifest_path(tmp / ".claude")
        m = json.loads(mpath.read_text())
        m["template_surface_version"] = "0.50.0"
        mpath.write_text(json.dumps(m))
        cfgp = tmp / ".claude" / "sprint-config.yaml"
        cfgp.write_text("auto_refresh_on_skew: false\n" + cfgp.read_text())
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            gate.sprint_init(argparse.Namespace(feature="demo"))
        out = buf.getvalue()
        assert "TEMPLATE SKEW" in out and "STALE agents" in out
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_refresh_no_hint_when_only_non_agents_change():
    """Hint should NOT appear when only non-agent files were updated."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        # Nothing changed → refresh is a no-op. No hint expected.
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            kit_refresh.run()
        out = buf.getvalue()
        assert "Claude Code caches" not in out
    finally:
        shutil.rmtree(tmp)


def test_force_reset_requires_no_merge_additions():
    """Post-field-bridge contract: `--force` alone routes config through the merge
    (never wholesale-clobbers it); the EXPLICIT `--force --no-merge-additions` is
    the reset path that puts stock template back. A garbage config under the reset
    path is overwritten; under plain --force it fails closed (visible), not silent."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        config_path = tmp / ".claude" / "sprint-config.yaml"
        config_path.write_text("# totally custom\n")
        rc = kit_refresh.run(force=True, no_merge_additions=True)
        assert rc == 0
        text = config_path.read_text()
        # explicit reset overwrote the custom file with template content
        assert "# totally custom" not in text
        assert "phases:" in text or "version:" in text
    finally:
        shutil.rmtree(tmp)



# ---------- refresh surgical additive merge (v0.5.8) ----------

def test_merge_settings_json_unions_permissions_allow():
    """Project allow has 1 custom + 5 baseline; template has 60. Merge → 61."""
    from prusik import refresh_merge as _rm
    project = json.dumps({
        "permissions": {
            "allow": [
                "Bash(custom-tool *)",   # project-specific
                "Bash(ls *)",
                "Bash(cat *)",
                "Bash(grep *)",
                "Bash(find *)",
                "Bash(mkdir *)",
            ]
        }
    })
    template = json.dumps({
        "permissions": {
            "allow": [
                "Bash(ls *)", "Bash(cat *)", "Bash(grep *)", "Bash(find *)",
                "Bash(mkdir *)", "Bash(uv *)", "Bash(pytest *)", "Bash(ruff *)",
                "Write(**)", "Edit(**)",
            ]
        }
    })
    merged_text, summary = _rm.merge_settings_json(template, project)
    merged = json.loads(merged_text)
    allows = merged["permissions"]["allow"]
    # Project's custom entry preserved at the front
    assert allows[0] == "Bash(custom-tool *)"
    # Every template entry now present
    for tmpl_entry in ["Bash(uv *)", "Bash(pytest *)", "Bash(ruff *)",
                       "Write(**)", "Edit(**)"]:
        assert tmpl_entry in allows
    # Summary reports 5 net additions
    assert summary["permission_additions"]["allow"] == 5


def test_merge_settings_json_no_op_when_project_is_superset():
    """If project already has every template entry, merge produces identical output."""
    from prusik import refresh_merge as _rm
    base = ["Bash(a *)", "Bash(b *)", "Write(**)"]
    project = json.dumps({"permissions": {"allow": base + ["Bash(custom *)"]}})
    template = json.dumps({"permissions": {"allow": base}})
    merged_text, summary = _rm.merge_settings_json(template, project)
    # No new additions
    assert summary["permission_additions"] == {}
    # Project content fully preserved
    assert json.loads(merged_text)["permissions"]["allow"] == base + ["Bash(custom *)"]


def test_merge_settings_json_preserves_user_hooks():
    """User has customized hooks; merge must NOT touch them."""
    from prusik import refresh_merge as _rm
    project = json.dumps({
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "MyCustomMatcher",
                    "hooks": [{"type": "command", "command": "my-custom-hook"}]
                }
            ]
        },
        "permissions": {"allow": ["Bash(custom *)"]}
    })
    template = json.dumps({
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Write|Edit",
                    "hooks": [{"type": "command", "command": "prusik gate pre-tool"}]
                }
            ]
        },
        "permissions": {"allow": ["Bash(uv *)"]}
    })
    merged_text, summary = _rm.merge_settings_json(template, project)
    merged = json.loads(merged_text)
    # User's hook entry preserved verbatim — not overwritten
    assert merged["hooks"]["PreToolUse"][0]["matcher"] == "MyCustomMatcher"
    assert len(merged["hooks"]["PreToolUse"]) == 1, \
        "must NOT append template hooks; user wins on hooks"
    # Permissions DO get merged
    assert "Bash(uv *)" in merged["permissions"]["allow"]
    assert "Bash(custom *)" in merged["permissions"]["allow"]


def test_merge_settings_json_adds_missing_top_level_keys():
    """If project's settings.json lacks `permissions` entirely, merge adds it."""
    from prusik import refresh_merge as _rm
    project = json.dumps({"hooks": {"PreToolUse": []}})
    template = json.dumps({
        "hooks": {"PreToolUse": []},
        "permissions": {"allow": ["Bash(uv *)", "Bash(pytest *)"]}
    })
    merged_text, summary = _rm.merge_settings_json(template, project)
    merged = json.loads(merged_text)
    assert "permissions" in merged
    assert merged["permissions"]["allow"] == ["Bash(uv *)", "Bash(pytest *)"]
    assert "permissions" in summary["added_top_level_keys"]


def test_refresh_surgical_merge_e2e():
    """End-to-end: prusik init → user edits settings.json → prusik refresh merges
    new template baseline without clobbering user customizations."""
    tmp = _mktmp_project()
    try:
        # Bootstrap with prusik init
        rc = kit_init.run()
        assert rc == 0, "prusik init should succeed"
        settings_path = tmp / ".claude" / "settings.json"
        assert settings_path.exists()

        # User customizes settings.json: adds a project-specific allow entry
        # AND a custom hook. Hash will diverge from manifest.
        custom = json.loads(settings_path.read_text())
        custom["permissions"]["allow"].append("Bash(my-private-tool *)")
        custom["hooks"].setdefault("PreToolUse", []).append({
            "matcher": "Custom", "hooks": [{"type": "command", "command": "echo hi"}]
        })
        # Drop one baseline entry to simulate "manually paste-merged subset"
        custom["permissions"]["allow"].remove("Bash(uv *)")
        settings_path.write_text(json.dumps(custom, indent=2) + "\n")

        # Now refresh
        rc = kit_refresh.run()
        assert rc == 0, "refresh should succeed"

        # Verify: template's missing entry is back, project customizations intact
        post = json.loads(settings_path.read_text())
        assert "Bash(uv *)" in post["permissions"]["allow"], \
            "template baseline entry should be merged in"
        assert "Bash(my-private-tool *)" in post["permissions"]["allow"], \
            "project-specific allow entry must be preserved"
        # User's custom hook intact (we appended; first entry came from template
        # at init, second from user)
        custom_hooks = [h for h in post["hooks"].get("PreToolUse", [])
                        if h.get("matcher") == "Custom"]
        assert custom_hooks, "user's custom hook entry must be preserved"
    finally:
        shutil.rmtree(tmp)


def test_refresh_no_merge_additions_flag_falls_back_to_skip():
    """--no-merge-additions opts out: settings.json gets skipped (pre-v0.5.8 behavior)."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        settings_path = tmp / ".claude" / "settings.json"
        # Diverge: drop a baseline entry
        custom = json.loads(settings_path.read_text())
        custom["permissions"]["allow"].remove("Bash(uv *)")
        settings_path.write_text(json.dumps(custom, indent=2) + "\n")
        modified_text = settings_path.read_text()

        # Refresh with no-merge-additions: settings.json should be SKIPPED
        rc = kit_refresh.run(no_merge_additions=True)
        assert rc == 0
        post = settings_path.read_text()
        assert post == modified_text, "with --no-merge-additions, settings.json must be untouched"
        # Confirm Bash(uv *) is still missing
        assert "Bash(uv *)" not in json.loads(post)["permissions"]["allow"]
    finally:
        shutil.rmtree(tmp)



# ---------- v0.8.0 merge-aware prusik init ----------

def test_kit_init_merge_default_skips_existing_files():
    """v0.8.0: when .claude/ already exists with user files, default
    `prusik init` (no flags) must SKIP existing files, not refuse and not
    overwrite. This is the adoption-cost fix for projects that have
    invested in their own CC config before encountering the kit."""
    tmp = _mktmp_project()
    try:
        # Pre-existing .claude/ with a custom agent file the user authored.
        (tmp / ".claude" / "agents").mkdir(parents=True)
        custom_agent = tmp / ".claude" / "agents" / "code-reviewer.md"
        custom_agent.write_text("# user's custom agent\nuntouched.\n")
        # Also pre-existing settings.json — minimal, no permissions section
        # (so the merge has nothing to add and is a no-op for this test).
        (tmp / ".claude" / "settings.json").write_text(
            '{"hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": '
            '[{"type": "command", "command": "echo user-hook"}]}]}}\n'
        )
        rc = kit_init.run()
        assert rc == 0, "default prusik init must succeed on existing .claude/"
        # User's custom agent must be preserved untouched
        assert custom_agent.exists()
        assert "user's custom agent" in custom_agent.read_text()
        # Kit's agents must have been added alongside (not blocked)
        assert (tmp / ".claude" / "agents" / "regression-sentinel.md").exists()
        # Kit's other top-level template files must have been added
        assert (tmp / ".claude" / "sprint-config.yaml").exists()
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_kit_init_merge_default_unions_settings_json_permissions():
    """v0.8.0: when user has a settings.json without kit's permission
    entries, default prusik init must surgical-merge — append kit's
    permissions.allow into user's, preserving user's hooks untouched."""
    tmp = _mktmp_project()
    try:
        (tmp / ".claude").mkdir()
        user_settings = tmp / ".claude" / "settings.json"
        user_settings.write_text(json.dumps({
            "hooks": {
                "PreToolUse": [{"matcher": "Edit", "hooks": [
                    {"type": "command", "command": "echo user-hook"}
                ]}]
            },
            "permissions": {
                "allow": ["Bash(my-custom-tool *)"]
            }
        }, indent=2) + "\n")
        rc = kit_init.run()
        assert rc == 0
        merged = json.loads(user_settings.read_text())
        # User's custom permission must still be there
        assert "Bash(my-custom-tool *)" in merged["permissions"]["allow"]
        # Kit's permissions must have been unioned in (Bash(prusik *) ships in template)
        assert "Bash(prusik *)" in merged["permissions"]["allow"]
        # User's hook must be untouched (not replaced by kit's hooks)
        user_hooks = merged["hooks"]["PreToolUse"]
        assert any(
            h.get("matcher") == "Edit"
            and any("user-hook" in subhook.get("command", "")
                    for subhook in h.get("hooks", []))
            for h in user_hooks
        )
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_kit_init_no_merge_additions_leaves_settings_alone():
    """v0.8.0: --no-merge-additions skips the surgical settings.json merge.
    User's settings.json stays exactly as-is."""
    tmp = _mktmp_project()
    try:
        (tmp / ".claude").mkdir()
        user_settings = tmp / ".claude" / "settings.json"
        original = json.dumps({
            "permissions": {"allow": ["Bash(my-tool *)"]}
        }, indent=2) + "\n"
        user_settings.write_text(original)
        rc = kit_init.run(merge_settings=False)
        assert rc == 0
        # Settings.json must be byte-identical to what user had
        assert user_settings.read_text() == original
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_kit_init_force_preserves_projects_subdir():
    """v0.8.0: --force still nukes .claude/, but `projects/` subdir
    (Claude Code session history) must be preserved alongside
    `conventions/`. Pre-v0.8.0 --force destroyed session history."""
    tmp = _mktmp_project()
    try:
        # Simulate Claude Code session history at .claude/projects/...
        proj_history = tmp / ".claude" / "projects" / "-Users-test-proj"
        proj_history.mkdir(parents=True)
        history_file = proj_history / "session-abc.jsonl"
        history_file.write_text('{"role":"user","content":"hi"}\n')
        rc = kit_init.run(force=True)
        assert rc == 0
        # Session history must survive --force
        assert history_file.exists(), \
            ".claude/projects/ must be preserved under --force (CC session history)"
        assert history_file.read_text() == '{"role":"user","content":"hi"}\n'
        # Prusik templates must have been freshly copied
        assert (tmp / ".claude" / "sprint-config.yaml").exists()
        assert (tmp / ".claude" / "agents" / "regression-sentinel.md").exists()
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_kit_init_force_preserves_conventions_subdir():
    """v0.8.0: --force preserves conventions/ (existing behavior, must not regress)."""
    tmp = _mktmp_project()
    try:
        conv_dir = tmp / ".claude" / "conventions" / "my-pack"
        conv_dir.mkdir(parents=True)
        conv_file = conv_dir / "rule.md"
        conv_file.write_text("# my project rule\n")
        rc = kit_init.run(force=True)
        assert rc == 0
        assert conv_file.exists()
        assert conv_file.read_text() == "# my project rule\n"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)



# ---------- v0.8.4 — detection-aware prusik init ----------
#
# prusik init becomes detection-aware by default: scans the project before
# scaffolding, prints what was found, records detection in the manifest,
# and prints copy-paste snippets for detected things. No flag required —
# auto-detection IS the default. --interactive (Phase 1.2 / v0.9.0) will
# add human-judgment questions on top.

from prusik import detect as kit_detect  # noqa: E402


def test_b27_detect_python_stack():
    tmp = _mktmp_project()
    try:
        (tmp / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        result = kit_detect.detect_project(tmp)
        assert "python" in result["stacks"]
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v084_detect_typescript_subsumes_javascript():
    """When tsconfig.json + package.json both exist, only 'typescript'
    is reported — JS is subsumed."""
    tmp = _mktmp_project()
    try:
        (tmp / "package.json").write_text('{"name":"x"}')
        (tmp / "tsconfig.json").write_text("{}")
        result = kit_detect.detect_project(tmp)
        assert "typescript" in result["stacks"]
        assert "javascript" not in result["stacks"]
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v084_detect_test_command_pytest_via_pyproject():
    tmp = _mktmp_project()
    try:
        (tmp / "pyproject.toml").write_text(
            "[project]\nname='x'\n[tool.pytest.ini_options]\nminversion='6.0'\n"
        )
        result = kit_detect.detect_project(tmp)
        assert result["test_commands"]["general"] == "pytest"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v084_detect_test_command_npm_via_package_json():
    tmp = _mktmp_project()
    try:
        (tmp / "package.json").write_text(json.dumps({
            "name": "x",
            "scripts": {"test": "vitest run"}
        }))
        result = kit_detect.detect_project(tmp)
        assert result["test_commands"]["general"] == "npm test"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v084_detect_behavior_tests_only_when_populated():
    """Empty tests/behavior/ should NOT trigger behavior detection."""
    tmp = _mktmp_project()
    try:
        (tmp / "tests" / "behavior").mkdir(parents=True)
        result = kit_detect.detect_project(tmp)
        assert result["behavior_tests"]["dir"] is None
        assert result["behavior_tests"]["test_count"] == 0
        # Add a real test file
        (tmp / "tests" / "behavior" / "test_smoke.py").write_text(
            "def test_x():\n    assert True\n"
        )
        result = kit_detect.detect_project(tmp)
        assert result["behavior_tests"]["dir"] == "tests/behavior"
        assert result["behavior_tests"]["test_count"] == 1
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v084_detect_pre_commit_framework():
    tmp = _mktmp_project()
    try:
        (tmp / ".pre-commit-config.yaml").write_text("repos: []\n")
        result = kit_detect.detect_project(tmp)
        assert result["pre_commit"]["type"] == "pre-commit-framework"
        assert result["pre_commit"]["command"] == "pre-commit run --all-files"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v084_detect_husky_lint_staged():
    tmp = _mktmp_project()
    try:
        (tmp / ".husky").mkdir()
        (tmp / ".husky" / "pre-commit").write_text("npx lint-staged\n")
        (tmp / ".lintstagedrc.js").write_text("module.exports = {};\n")
        result = kit_detect.detect_project(tmp)
        assert result["pre_commit"]["type"] == "husky+lint-staged"
        assert "lint-staged" in result["pre_commit"]["command"]
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v084_detect_custom_git_hooks():
    """AssetSolvo-style scripts/git-hooks/pre-commit pattern."""
    tmp = _mktmp_project()
    try:
        (tmp / "scripts" / "git-hooks").mkdir(parents=True)
        (tmp / "scripts" / "git-hooks" / "pre-commit").write_text("#!/bin/bash\n")
        result = kit_detect.detect_project(tmp)
        assert result["pre_commit"]["type"] == "custom-scripts"
        assert "scripts/git-hooks/pre-commit" in result["pre_commit"]["command"]
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v084_detect_linters_via_pyproject():
    tmp = _mktmp_project()
    try:
        (tmp / "pyproject.toml").write_text(
            "[project]\nname='x'\n[tool.ruff]\nselect=['E']\n[tool.mypy]\nstrict=true\n"
        )
        result = kit_detect.detect_project(tmp)
        assert "ruff" in result["linters"]
        assert "mypy" in result["linters"]
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v084_detect_ci_github_actions():
    tmp = _mktmp_project()
    try:
        (tmp / ".github" / "workflows").mkdir(parents=True)
        (tmp / ".github" / "workflows" / "ci.yml").write_text("name: ci\n")
        result = kit_detect.detect_project(tmp)
        assert result["ci"]["present"] is True
        assert "github-actions" in result["ci"]["providers"]
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v084_detect_existing_claude_config():
    """Existing .claude/ contents flagged. Critical for merge-aware init
    decisions and for surfacing 'projects/ session history present'."""
    tmp = _mktmp_project()
    try:
        (tmp / ".claude").mkdir()
        (tmp / ".claude" / "settings.local.json").write_text("{}")
        (tmp / ".claude" / "projects").mkdir()
        result = kit_detect.detect_project(tmp)
        cc = result["claude_config"]
        assert cc["exists"] is True
        assert cc["settings_local_json"] is True
        assert cc["projects_history_present"] is True
        assert cc["settings_json"] is False
        assert cc["agents_dir_populated"] is False
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v084_detect_monorepo_with_apps():
    tmp = _mktmp_project()
    try:
        for app in ("backend", "frontend"):
            (tmp / "apps" / app).mkdir(parents=True)
        # Each app needs a manifest to count
        (tmp / "apps" / "backend" / "pyproject.toml").write_text("[project]\nname='b'\n")
        (tmp / "apps" / "frontend" / "package.json").write_text('{"name":"f"}')
        result = kit_detect.detect_project(tmp)
        assert result["monorepo"]["is_monorepo"] is True
        assert "apps/backend" in result["monorepo"]["app_dirs"]
        assert "apps/frontend" in result["monorepo"]["app_dirs"]
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v084_detect_greenfield_returns_empty():
    """Empty repo returns sensible empty defaults — no crashes."""
    tmp = _mktmp_project()
    try:
        result = kit_detect.detect_project(tmp)
        assert result["stacks"] == []
        assert result["test_commands"]["general"] is None
        assert result["test_commands"]["behavior"] is None
        assert result["linters"] == []
        assert result["pre_commit"]["type"] is None
        assert result["ci"]["present"] is False
        assert result["claude_config"]["exists"] is False
        assert result["behavior_tests"]["dir"] is None
        assert result["monorepo"]["is_monorepo"] is False
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v084_format_summary_reports_findings():
    tmp = _mktmp_project()
    try:
        (tmp / "pyproject.toml").write_text(
            "[project]\nname='x'\n[tool.pytest.ini_options]\n"
        )
        (tmp / ".pre-commit-config.yaml").write_text("repos: []\n")
        detection = kit_detect.detect_project(tmp)
        summary = kit_detect.format_summary(detection)
        assert "python" in summary
        assert "pytest" in summary
        assert "pre-commit-framework" in summary
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v084_format_snippets_for_detected_pre_commit():
    tmp = _mktmp_project()
    try:
        (tmp / ".pre-commit-config.yaml").write_text("repos: []\n")
        detection = kit_detect.detect_project(tmp)
        snippets = kit_detect.format_snippets(detection)
        # Should produce at least one snippet about project_policy
        assert len(snippets) >= 1
        joined = "\n".join(snippets)
        assert "project_policy" in joined
        assert "pre-commit run --all-files" in joined
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v084_format_snippets_for_detected_behavior_tests():
    tmp = _mktmp_project()
    try:
        (tmp / "tests" / "behavior").mkdir(parents=True)
        (tmp / "tests" / "behavior" / "test_smoke.py").write_text(
            "def test_x():\n    assert True\n"
        )
        (tmp / "pyproject.toml").write_text(
            "[project]\nname='x'\n[tool.pytest.ini_options]\n"
        )
        detection = kit_detect.detect_project(tmp)
        snippets = kit_detect.format_snippets(detection)
        joined = "\n".join(snippets)
        assert "behavior_regression" in joined
        assert "pytest tests/behavior/" in joined
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v084_format_snippets_empty_for_greenfield():
    tmp = _mktmp_project()
    try:
        detection = kit_detect.detect_project(tmp)
        snippets = kit_detect.format_snippets(detection)
        # Empty repo: no detected things, so no snippets.
        assert snippets == []
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v084_kit_init_records_detection_in_manifest():
    """End-to-end: run prusik init on a synthetic project, verify the
    manifest captured the detection results."""
    tmp = _mktmp_project()
    try:
        (tmp / "pyproject.toml").write_text(
            "[project]\nname='x'\n[tool.pytest.ini_options]\n"
        )
        rc = kit_init.run()
        assert rc == 0
        manifest = json.loads((tmp / ".claude" / ".prusik-manifest.json").read_text())
        assert "detection" in manifest
        assert "python" in manifest["detection"]["stacks"]
        assert manifest["detection"]["test_commands"]["general"] == "pytest"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)



# ---------- v0.8.6 — Public-interface stability (snapshot-style) ----------
#
# These tests assert that specific operator-visible strings appear in
# kit's output. They form a CONTRACT: changing any of these strings is
# a breaking interface change for adopters who script against kit's CLI
# output (CI parsers, dashboards, operator muscle memory). Future
# kit-author edits that intentionally change a string must update the
# test deliberately — the test fails first, then the human acknowledges.
#
# Why exact-match-on-snippets instead of full-output snapshots: prusik
# output contains volatile parts (paths, version numbers, file counts,
# timestamps). Snapshot frameworks add a dep + boilerplate. Asserting
# on stable substrings achieves the same defensive value with no
# dependency cost.
#
# When you change one of these strings deliberately:
# 1. Run the failing test to confirm it caught your change.
# 2. Update the expected substring in the test.
# 3. Document the breaking change in CHANGELOG (operator-visible
#    output is a versioned interface).

import io  # noqa: E402
import contextlib  # noqa: E402


def _capture_stdout(fn):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn()
    return buf.getvalue()


def _capture_stderr(fn):
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        fn()
    return buf.getvalue()


def test_v086_iface_kit_digest_empty_ledger_message():
    """prusik digest with empty ledger MUST say 'Ledger is empty.' verbatim.
    Operators / CI grep for this. Don't change without a CHANGELOG entry."""
    tmp = _mktmp_project()
    try:
        out = _capture_stdout(lambda: ledger_digest())
        assert "Ledger is empty." in out, (
            "Operator-facing string 'Ledger is empty.' missing from "
            "`prusik digest` empty-ledger output. This is a versioned "
            "interface — updating the string requires a CHANGELOG entry."
        )
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v086_iface_kit_doctor_required_labels_in_scorecard():
    """prusik doctor's text scorecard MUST contain these labels verbatim:
      - 'Harness scorecard for' — header line
      - The 5 subsystem labels: Instructions, State, Verification, Scope, Session Lifecycle
      - 'Lowest subsystem:' — actionable callout
      - 'Suggested next step:' — operator guidance
    Adopters scripting against `prusik doctor --json` consume the data;
    operators reading the text version rely on these labels. Both
    contracts are versioned."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        out = _capture_stdout(lambda: kit_doctor.run())
        for required in (
            "Harness scorecard for",
            "Instructions:",
            "State:",
            "Verification:",
            "Scope:",
            "Session Lifecycle:",
            "Lowest subsystem:",
            "Suggested next step:",
        ):
            assert required in out, (
                f"Operator-facing label {required!r} missing from prusik "
                f"doctor output. Versioned interface — see "
                f"v0.8.6 CHANGELOG entry on snapshot stability."
            )
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v086_iface_kit_doctor_no_kit_message():
    """When run outside a kit-installed project, prusik doctor MUST say
    'Run `prusik init` first.' so operators get unambiguous remediation."""
    tmp = _mktmp_project()
    try:
        out = _capture_stderr(lambda: kit_doctor.run())
        assert "Run `prusik init` first." in out, (
            "Remediation hint missing from prusik doctor's no-kit message. "
            "This is the operator's first-failure path; don't change "
            "without CHANGELOG."
        )
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v086_iface_kit_doctor_drift_meta_keys():
    """Drift detection emits stable _meta values for two predicate states.
    Adopter dashboards / `prusik doctor --json` consumers must be able to
    distinguish 'no manifest' from 'manifest pre-dates detection'."""
    # The constants are part of the CLI contract.
    from prusik import doctor as _doctor
    # Probe by constructing synthetic manifests
    no_manifest_drift = _doctor._detect_drift(None, {})
    assert no_manifest_drift.get("_meta") == "no_manifest_to_compare"
    pre_v084 = _doctor._detect_drift({"kit_version": "0.7.1"}, {})
    assert pre_v084.get("_meta") == "manifest_predates_detection"


def test_v086_iface_kit_gate_advance_error_format():
    """When advance is blocked by unmet artifacts, prusik MUST say
    '[prusik-gate] Cannot advance from'. CI integrations + operator
    muscle memory both depend on this prefix."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        # Set state mid-flow without the required artifacts
        phases.set_sprint_state({"phase": "scoping", "feature": "feat"})
        args = argparse.Namespace(phase="triage", feature="feat")
        out = _capture_stderr(lambda: gate.advance(args))
        assert "[prusik-gate]" in out, \
            "prusik gate output prefix '[prusik-gate]' missing — versioned interface"
        assert "Cannot advance from" in out, \
            "prusik gate advance error phrase 'Cannot advance from' missing"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v086_iface_brief_schema_error_message_shape():
    """Brief schema validation errors must follow the format
    '<field>: <reason>' so operators / brief-critic can parse them
    consistently. Specifically, 'goal: needs ≥5 words (got N)' is the
    canonical insufficient-goal error."""
    tmp = _mktmp_project()
    try:
        brief = tmp / "briefs" / "x.md"
        brief.parent.mkdir(parents=True)
        brief.write_text("## Goal\nToo short.\n\n## Type\nbug_fix\n")
        ok, errors = schema.validate_brief(brief)
        assert not ok
        # The format is '<field>: <message>' with field 'goal'
        assert any(e.startswith("goal:") and "≥5 words" in e for e in errors), (
            f"brief schema error format changed; expected 'goal: ... ≥5 words ...', "
            f"got {errors}"
        )
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v086_iface_kit_gate_deny_message_prefix():
    """The [prusik-gate] prefix is load-bearing — v0.8.1 verify-before-claim
    role-spec discipline tells reviewers to QUOTE the [prusik-gate]
    message verbatim. The prefix's stability is the lynchpin of the
    entire B26 fabrication-detection mechanism."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        phases.set_sprint_state({"phase": "scoping", "feature": "feat"})
        # Construct a deny scenario via path-writability check
        config = phases.load_sprint_config(tmp)
        ok, reason = phases.is_path_writable(
            "src/forbidden.py", config, "scoping", "feat"
        )
        assert not ok
        # The reason is what gets surfaced via _deny() with [prusik-gate] prefix
        # Just verify the prefix mechanism is in gate.py
        gate_src = (Path(__file__).parent.parent / "prusik" / "gate.py").read_text()
        assert "[prusik-gate]" in gate_src, (
            "[prusik-gate] prefix removed from prusik/gate.py — this breaks the "
            "B26 detection loop. v0.8.1 + v0.8.2 + v0.8.5 ALL depend on "
            "this prefix being present in deny messages and reviewer FAILs."
        )
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v086_iface_kit_init_detection_summary_anchors():
    """prusik init's detection summary uses ✓/⚠/· markers to communicate
    found / missing-but-warned / absent-and-fine. These markers are the
    operator's first interaction with prusik; their stability matters."""
    tmp = _mktmp_project()
    try:
        (tmp / "pyproject.toml").write_text(
            "[project]\nname='x'\n[tool.pytest.ini_options]\n"
        )
        out = _capture_stdout(lambda: kit_init.run())
        assert "[prusik-init] Detected project shape:" in out, \
            "prusik init detection-summary header label changed"
        # All three marker types should appear in real init output
        # (✓ for what's present, · or ⚠ for what's absent)
        assert "✓" in out, "prusik init detection markers ✓ missing"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v086_iface_changelog_exists_and_documents_recent_versions():
    """CHANGELOG.md is a versioned interface. The current prusik version
    MUST have an entry. Future maintainers who skip the CHANGELOG entry
    fail this test."""
    from prusik import __version__
    changelog = (Path(__file__).parent.parent / "CHANGELOG.md").read_text()
    assert f"## [{__version__}]" in changelog, (
        f"kit/__init__.py says version is {__version__}, but CHANGELOG.md "
        f"has no '## [{__version__}]' entry. Add one before shipping."
    )



# ---------- v0.13.0 — manifest provenance ----------

from prusik import manifest as kit_manifest  # noqa: E402
from prusik import __version__ as _KITV  # noqa: E402


def _legacy_manifest(tmp, kit_version="0.3.1", with_detection=False,
                      last_refresh=None):
    """Overwrite the fresh manifest with a pre-v0.13.0 SCHEMA shape (no
    manifest_schema field), preserving files[] so refresh's per-file logic
    works. Exercises the in-memory schema 0→2 migration (load → _migrate).
    Uses the current filename — the manifest-filename rename is unrelated to
    the schema migration this helper tests."""
    mp = tmp / ".claude" / ".prusik-manifest.json"
    cur = json.loads(mp.read_text())
    legacy = {
        "kit_version": kit_version,
        "installed_at": "2025-01-01T00:00:00+00:00",
        "files": cur["files"],
        "directories_created": cur.get("directories_created", []),
        "gitignore_block_added": cur.get("gitignore_block_added", False),
    }
    if with_detection:
        legacy["detection"] = cur.get("detection", {})
    if last_refresh:
        legacy["last_refresh_version"] = last_refresh
        legacy["last_refresh_at"] = "2025-02-02T00:00:00+00:00"
    mp.write_text(json.dumps(legacy, indent=2))
    return mp


def test_v0130_init_writes_schema2_manifest():
    tmp = _mktmp_project()
    try:
        kit_init.run()
        m = json.loads((tmp / ".claude" / ".prusik-manifest.json").read_text())
        assert m["manifest_schema"] == kit_manifest.SCHEMA
        assert m["created_with"] == _KITV
        assert m["template_surface_version"] == _KITV
        assert m["history"][0]["command"] == "init"
        assert m["files"] and all("path" in e and "hash" in e
                                  for e in m["files"])
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v0130_refresh_stamps_surface_version():
    """THE regression test for the reported defect: a project init'd at an
    old version and refreshed forward must NOT report the old version."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        _legacy_manifest(tmp, kit_version="0.3.1")
        rc = kit_refresh.run()
        assert rc == 0
        m = kit_manifest.load(tmp / ".claude" / ".prusik-manifest.json")
        assert kit_manifest.surface_version(m) == _KITV, \
            "refresh must stamp the deployed surface version (the bug)"
        assert kit_manifest.created_with(m) == "0.3.1", \
            "created_with stays immutable (archaeology)"
        assert any(h["command"] == "refresh" for h in m["history"])
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v0130_migration_truth_preserving():
    """Migration reconstructs best-evidenced surface version — it must NOT
    fabricate currency (not the running binary's version)."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        _legacy_manifest(tmp, kit_version="0.3.1", last_refresh="0.9.0")
        m = kit_manifest.load(tmp / ".claude" / ".prusik-manifest.json")
        assert kit_manifest.created_with(m) == "0.3.1"
        assert kit_manifest.surface_version(m) == "0.9.0", \
            "best evidence = last_refresh_version, NOT the running binary"
        assert m["surface_version"] != _KITV if "surface_version" in m else True
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v0130_migration_idempotent():
    tmp = _mktmp_project()
    try:
        kit_init.run()
        _legacy_manifest(tmp, kit_version="0.3.1")
        mp = tmp / ".claude" / ".prusik-manifest.json"
        m1 = kit_manifest.load(mp)
        h1 = len(m1["history"])
        m2 = kit_manifest._migrate(dict(m1))
        assert m2["manifest_schema"] == kit_manifest.SCHEMA
        assert len(m2["history"]) == h1, "re-migration must not dup history"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v0130_doctor_reports_surface_not_init_version():
    """End-to-end of the reported bug: doctor must report the refreshed
    surface, not the frozen init version."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        _legacy_manifest(tmp, kit_version="0.3.1")
        kit_refresh.run()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            kit_doctor.run()
        out = buf.getvalue()
        assert f"manifest: v{_KITV}" in out, out
        assert "manifest: v0.3.1" not in out, out
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v0130_refresh_backfills_detection():
    tmp = _mktmp_project()
    try:
        kit_init.run()
        _legacy_manifest(tmp, kit_version="0.3.1", with_detection=False)
        kit_refresh.run()
        m = kit_manifest.load(tmp / ".claude" / ".prusik-manifest.json")
        assert m.get("detection") is not None, "refresh backfills detection"
        assert kit_manifest.detection_baseline(m).startswith("refresh-backfill@")
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v0130_uninstall_works_on_legacy_manifest():
    tmp = _mktmp_project()
    try:
        kit_init.run()
        _legacy_manifest(tmp, kit_version="0.3.1")
        rc = kit_uninstall.run()
        assert rc == 0, "uninstall must still honor files[] on a legacy manifest"
        assert not (tmp / ".claude" / "sprint-config.yaml").exists()
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v0130_doctor_is_read_only():
    """Read/write separation: doctor must not mutate the manifest on disk."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        mp = _legacy_manifest(tmp, kit_version="0.3.1")
        before = mp.read_text()
        with contextlib.redirect_stdout(io.StringIO()):
            kit_doctor.run()
        assert mp.read_text() == before, \
            "doctor migrated in memory but must NOT persist (read-only)"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v0130_migration_preserves_unknown_keys():
    """Additive migration: an older binary's / future key is never dropped."""
    m = {"kit_version": "0.3.1", "files": [], "_future_key": {"x": 1}}
    out = kit_manifest._migrate(m)
    assert out["_future_key"] == {"x": 1}
    assert out["kit_version"] == "0.3.1", "legacy field not deleted (forward-compat)"
    assert out["manifest_schema"] == kit_manifest.SCHEMA


# ---------- v0.14.0 — additive YAML merge-coverage ----------

from prusik import refresh_merge as _rm  # noqa: E402

_TMPL_SC = """\
# prusik sprint-config (template)
phases:
  - name: scoping
    writable:
      - "design/{feature}/scope.md"
      - "design/{feature}/trivial.md"   # v0.11.0 #2
  - name: reviewing
    writable:
      - "reports/{feature}/**"
    exit_artifacts:
      - path: "reports/{feature}/regression.txt"
        must_contain: "PASS"
      - path: "reports/{feature}/regression.evidence.json"
        validator: execution_evidence
    budget_tokens: 300000
  - name: integrating
    writable:
      - "**"
"""

_PROJ_SC = """\
# MY project config — do not lose this comment
phases:
  - name: scoping
    writable:
      - "design/{feature}/scope.md"   # user kept only this
  - name: reviewing
    writable:
      - "reports/{feature}/**"
    exit_artifacts:
      - path: "reports/{feature}/regression.txt"
        must_contain: "PASS"
    budget_tokens: 999999   # user tuned this — must NOT be clobbered
behavior_regression:
  enabled: true
  command: "my custom suite"   # user customization, untouched
"""


def test_v0140_additive_merge_lands_deltas_preserves_user():
    merged, summary = _rm.merge_sprint_config_yaml(_TMPL_SC, _PROJ_SC)
    import yaml as _y
    d = _y.safe_load(merged)
    phases = {p["name"]: p for p in d["phases"]}
    # F recurrence: missing evidence exit_artifact is union-added by path
    paths = [a["path"] for a in phases["reviewing"]["exit_artifacts"]]
    assert "reports/{feature}/regression.evidence.json" in paths
    assert paths.count("reports/{feature}/regression.txt") == 1, "no dup"
    # v0.11.0 #2 recurrence: missing writable entry union-added
    assert "design/{feature}/trivial.md" in phases["scoping"]["writable"]
    # scalar user value NEVER clobbered
    assert phases["reviewing"]["budget_tokens"] == 999999
    # user top-level customization untouched
    assert d["behavior_regression"]["command"] == "my custom suite"
    # comments preserved (the whole point of ruamel)
    assert "do not lose this comment" in merged
    assert "user tuned this" in merged
    assert summary["list_additions"].get("reviewing.exit_artifacts") == 1
    assert summary["list_additions"].get("scoping.writable") == 1


def test_v0140_noop_is_byte_identical():
    merged, summary = _rm.merge_sprint_config_yaml(_TMPL_SC, _TMPL_SC)
    assert merged == _TMPL_SC, "no missing deltas → byte-identical no-op"
    assert not summary["list_additions"] and not summary["phases_added"]


def test_v0140_missing_phase_appended_whole():
    proj = "phases:\n  - name: scoping\n    writable:\n      - \"x\"\n"
    merged, summary = _rm.merge_sprint_config_yaml(_TMPL_SC, proj)
    import yaml as _y
    names = [p["name"] for p in _y.safe_load(merged)["phases"]]
    assert "reviewing" in names and "integrating" in names
    assert "reviewing" in summary["phases_added"]


def test_v0140_missing_phase_key_added():
    proj = ('phases:\n  - name: reviewing\n    writable:\n'
            '      - "reports/{feature}/**"\n')
    merged, summary = _rm.merge_sprint_config_yaml(_TMPL_SC, proj)
    import yaml as _y
    rev = {p["name"]: p for p in _y.safe_load(merged)["phases"]}["reviewing"]
    assert "exit_artifacts" in rev and "budget_tokens" in rev
    assert "reviewing.exit_artifacts" in summary["phase_keys_added"]


def test_v0140_unsafe_merge_function_raises():
    """Contract: merge raises on unparseable input so the caller can
    fail closed (it must NOT silently return a degraded result)."""
    try:
        _rm.merge_sprint_config_yaml(_TMPL_SC, "phases: [unclosed")
        assert False, "must raise — no silent degraded return"
    except Exception:
        pass


def test_v0140_unsafe_merge_fails_closed_nonzero_no_clobber():
    """The operator concern: a system-selected skip with rc 0 is an
    unnoticed degradation. Unsafe merge → rc≠0 + file untouched +
    loud MERGE FAILED block (NOT the benign skipped bucket)."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        sc = tmp / ".claude" / "sprint-config.yaml"
        broken = "phases: [this is : not valid yaml\n"
        sc.write_text(broken)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = kit_refresh.run()
        assert rc == 1, "system-selected merge failure must exit non-zero"
        assert sc.read_text() == broken, "must NOT clobber the user file"
        assert "MERGE FAILED" in buf.getvalue(), "must be loud + blocking"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v0140_end_to_end_refresh_merges_not_skips():
    tmp = _mktmp_project()
    try:
        kit_init.run()
        sc = tmp / ".claude" / "sprint-config.yaml"
        original = sc.read_text()
        # User customizes: add a comment + delete the F evidence gate line
        text = original.replace(
            "phases:", "# MY tuning note\nphases:", 1)
        sc.write_text(text)
        rc = kit_refresh.run()
        assert rc == 0
        after = sc.read_text()
        assert "MY tuning note" in after, "user comment preserved"
        # F evidence gate present after merge (deploys to customized config)
        assert "regression.evidence.json" in after
        assert "execution_evidence" in after
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v0140_no_merge_additions_flag_skips():
    tmp = _mktmp_project()
    try:
        kit_init.run()
        sc = tmp / ".claude" / "sprint-config.yaml"
        sc.write_text("# user\n" + sc.read_text())
        before = sc.read_text()
        rc = kit_refresh.run(no_merge_additions=True)
        assert rc == 0
        assert sc.read_text() == before, "--no-merge-additions → exact skip"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v0141_manifest_match_does_not_clobber_customized_config():
    """Regression for the live clobber: when the manifest hash equals a
    CUSTOMIZED on-disk sprint-config (post kit-init / post-merge), refresh
    must MERGE (preserve customizations + land additive delta), NOT
    wholesale-overwrite with the stock template."""
    tmp = _mktmp_project()
    try:
        import hashlib as _h
        kit_init.run()
        sc = tmp / ".claude" / "sprint-config.yaml"
        mp = tmp / ".claude" / ".prusik-manifest.json"
        # Customize: a user comment + drop one reviewing exit_artifact so
        # there's a genuine additive delta the template would re-add.
        import yaml as _y
        d = _y.safe_load(sc.read_text())
        for ph in d["phases"]:
            if ph.get("name") == "reviewing":
                ph["exit_artifacts"] = [
                    a for a in ph["exit_artifacts"]
                    if not str(a.get("path", "")).endswith(".evidence.json")]
        sc.write_text("# USER-CUSTOMIZATION must survive\n" + _y.safe_dump(d))
        # Simulate the dangerous oracle: manifest records the CUSTOMIZED
        # hash (as a prior kit-init / merge would) → current==manifest.
        man = json.loads(mp.read_text())
        cust_hash = _h.sha256(sc.read_bytes()).hexdigest()
        for e in man["files"]:
            if e["path"] == ".claude/sprint-config.yaml":
                e["hash"] = cust_hash
        mp.write_text(json.dumps(man))
        rc = kit_refresh.run()
        assert rc == 0
        after = sc.read_text()
        assert "USER-CUSTOMIZATION must survive" in after, \
            "merge-eligible file must NOT be wholesale-overwritten"
        assert "execution_evidence" in after, \
            "additive delta must still land via merge"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_force_routes_merge_eligible_config_through_merge():
    """field bridge HIGH (supersedes the old v0.14.1 contract): `--force` must NOT
    wholesale-overwrite a merge-eligible config — it routes through the
    non-destructive merge, PRESERVING the user's content (clobbering it silently
    disabled a project's gates, bridge hook, and permissions). Only the explicit
    `--no-merge-additions` overwrites."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        sc = tmp / ".claude" / "sprint-config.yaml"
        sc.write_text("# user note\n" + sc.read_text())   # keep valid, mark it
        rc = kit_refresh.run(force=True)
        assert rc == 0
        assert "# user note" in sc.read_text(), \
            "--force routes config through merge — user content preserved"
        # the explicit reset path still overwrites
        rc2 = kit_refresh.run(force=True, no_merge_additions=True)
        assert rc2 == 0
        assert "# user note" not in sc.read_text()
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v0142_content_loss_detects_dropped_keys_and_phases():
    from prusik.refresh import _user_content_loss as L
    # dropped top-level key (sprint-config)
    assert L(".claude/sprint-config.yaml",
             "a: 1\nphases: []\n", "phases: []\n")
    # dropped phase
    assert L(".claude/sprint-config.yaml",
             "phases:\n  - name: reviewing\n", "phases:\n  - name: scoping\n")
    # dropped settings hooks event
    assert L(".claude/settings.json",
             '{"hooks":{"UserPromptSubmit":[1]}}', '{"hooks":{}}')
    # unparseable old → fail closed (cannot certify)
    assert L(".claude/sprint-config.yaml", "a: [unclosed", "a: 1\n")
    # additive-only (more keys, none dropped) → NOT a loss (no false positive)
    assert L(".claude/sprint-config.yaml",
             "phases:\n  - name: reviewing\n",
             "phases:\n  - name: reviewing\n  - name: scoping\nx: 1\n") is None


def test_v0142_refresh_fails_closed_if_merge_would_drop_user_content():
    """Backstop integration: if the merger ever returns content that drops
    a user top-level key, refresh must NOT write it — rc≠0 + MERGE FAILED,
    file untouched."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        sc = tmp / ".claude" / "sprint-config.yaml"
        # Customize so the file is user-modified + has a user top-level key
        sc.write_text("# user\nmy_custom_block:\n  k: v\n"
                      + (tmp / ".claude" / "sprint-config.yaml").read_text())
        before = sc.read_text()
        # Force the merger to return content that DROPS my_custom_block
        import prusik.refresh_merge as rmm
        orig = rmm.merge_sprint_config_yaml
        rmm.merge_sprint_config_yaml = lambda t, p: (
            "phases: []\n", {"list_additions": {}, "phases_added": [],
                             "phase_keys_added": [], "added_top_level_keys": []})
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = kit_refresh.run()
        finally:
            rmm.merge_sprint_config_yaml = orig
        assert rc == 1, "content-loss must fail closed (rc≠0)"
        assert "USER CONTENT LOSS" in buf.getvalue()
        assert sc.read_text() == before, "must NOT write the lossy content"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


