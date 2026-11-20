"""KiraAI 3.2 production-wiring and full-regression tests for kira_ops.

Runs against real framework managers with an isolated temporary data directory.
No network and no user data are touched.  Older framework versions skip this
suite; their legacy behavior remains covered by the existing dual-generation
suites.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import sys
import tempfile
import types
from pathlib import Path

FILE = Path(__file__).resolve()
ROOT = FILE.parents[4]
PLUGIN_DIR = FILE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packaging.version import Version

# Importing core.config before chdir would bind global config paths to the source
# tree.  Read the constant textually so the temporary data root stays hermetic.
version_text = (ROOT / "core" / "config" / "default.py").read_text(encoding="utf-8")
VERSION = re.search(r'^VERSION\s*=\s*["\']([^"\']+)', version_text, re.MULTILINE).group(1)

if Version(VERSION.lstrip("v")) < Version("3.2.0"):
    print(f"SKIP: 3.2 production contract suite on {VERSION}")
    raise SystemExit(0)

PASSED, FAILED = [], []


async def check(name, fn):
    try:
        result = fn()
        if hasattr(result, "__await__"):
            result = await result
        assert result is not False
    except Exception as exc:
        import traceback
        FAILED.append((name, exc))
        print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
        traceback.print_exc()
    else:
        PASSED.append(name)
        print(f"  ok  {name}")


class Ev:
    def __init__(self, sid="napcat:gm:parent", uid="1"):
        self.sid = sid
        self.messages = [types.SimpleNamespace(sender=types.SimpleNamespace(user_id=uid))]
        self.session = None


async def run():
    original_cwd = Path.cwd()
    workspace = Path(tempfile.mkdtemp(prefix="kira_ops_320_suite_"))
    (workspace / "data").mkdir()  # logging initializes before the manager fixtures
    os.chdir(workspace)
    try:
        from core.agent.func_tool_manager import FuncToolManager
        from core.agent.mcp_mgr import MCPManager
        from core.agent.skills_mgr import SkillsManager
        from core.agent.subagent_manager import SubAgentManager
        from core.chat.session_manager import SessionManager
        from core.config import KiraConfig
        from core.db.db_mgr import DatabaseManager
        from core.db.service import DatabaseService
        from core.event_bus import EventBus
        from core.persona import PersonaManager
        from core.plugin import PluginManager
        from core.plugin.plugin_context import PluginContext
        from core.provider import ProviderManager
        from core.statistics import Statistics

        (workspace / "data" / "skills").mkdir(parents=True)
        (workspace / "data" / "config").mkdir(parents=True)
        for name in ("skill_one", "skill_two"):
            folder = workspace / "data" / "skills" / name
            folder.mkdir()
            (folder / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {name}\n---\nbody\n", encoding="utf-8")
        (workspace / "data" / "config" / "mcp.json").write_text(json.dumps({
            "mcpServers": {
                "mcp_one": {"type": "sse", "url": "https://example.com/one/sse", "enabled": False},
                "mcp_two": {"type": "sse", "url": "https://example.com/two/sse", "enabled": False},
            }
        }), encoding="utf-8")

        kc = KiraConfig()
        dbm = DatabaseManager(f"sqlite+aiosqlite:///{(workspace / 'data' / 'test.db').as_posix()}")
        await dbm.init()
        db = DatabaseService(dbm)
        await db.init_tables()
        event_bus = EventBus(Statistics(), asyncio.Queue())
        provider_mgr = ProviderManager(db, kc)
        session_mgr = SessionManager(db, kc, event_bus)
        skills_mgr = SkillsManager()
        tool_mgr = FuncToolManager(kc)
        mcp_mgr = MCPManager(tool_mgr)
        mcp_mgr.mcp_config = mcp_mgr.load_config()
        mcp_mgr.load_servers()  # disabled fixtures: do not open any connections

        # One legacy-shape model is enough to exercise the official model resolver.
        kc["providers"]["test_provider"] = {
            "format": "OpenAI", "name": "Test", "provider_config": {},
            "model_config": {"llm": {"model_one": {"model_id": "model_one"}}},
        }
        await session_mgr.update_session(
            "napcat:gm:parent", title="Parent",
            content=[[{"role": "user", "content": "hello"}]],
        )
        parent = await session_mgr.get_session("napcat:gm:parent", include_content=False)
        child = await session_mgr.create_subagent_session(
            title="Child", parent_id=parent.internal_id,
            data={"subagent": {
                "status": "done", "result": "finished", "scope_sid": parent.sid,
                "settings": {}, "turn": 1, "step_index": 2,
            }},
        )
        subagent_mgr = SubAgentManager(
            session_mgr, provider_mgr, message_history=None, event_bus=event_bus,
            persona_manager=PersonaManager(db), skills_manager=skills_mgr,
        )
        await subagent_mgr.initialize()

        # Exact 3.2 production wiring: modern managers present; legacy attributes absent.
        ctx = PluginContext(
            db=db, config=kc, event_bus=event_bus, provider_mgr=provider_mgr,
            tool_mgr=tool_mgr, adapter_mgr=None, persona_mgr=PersonaManager(db),
            sticker_mgr=None, session_mgr=session_mgr, prompt_mgr=None,
            skills_mgr=skills_mgr, mcp_mgr=mcp_mgr,
            message_processor=types.SimpleNamespace(), message_history=None,
            image_desc_cache=None, session_buffer_mgr=None, message_delivery=None,
            message_router=None, subagent_mgr=subagent_mgr, _main_agent_runs=None,
        )
        pm = PluginManager(ctx)
        ctx.plugin_mgr = pm
        loaded = await pm.load_plugin_from_dir(PLUGIN_DIR, auto_install=False)
        assert loaded == "kira_ops", loaded
        await pm.init_plugin("kira_ops")
        inst = pm.get_plugin_inst("kira_ops")
        ev = Ev()

        async def manager_counts_match_everywhere():
            status = await inst.ops_status(ev, include="skills,mcp,sessions,subagents,providers")
            panel = await inst.api_overview()
            skill_list = inst.caps["skill"].handle_read("list", {})
            mcp_list = await inst.caps["mcp"].handle_read("list", {})
            assert skill_list["total"] == 2 and mcp_list["total"] == 2
            assert status["skills"]["total"] == 2 and status["mcp"]["total"] == 2
            assert panel["counts"]["skills"] == 2 and panel["counts"]["mcp"] == 2
            assert status["sessions"]["im"] == 1 and status["sessions"]["subagents"] == 1
            assert status["subagents"]["total"] == 1
            assert panel["counts"]["subagents"] == 1
            assert status["providers"]["active"] == 0
            assert status["providers"]["configured"] == 1
            assert panel["counts"]["providers_configured"] == 1
        await check("2 skills + 2 MCP agree across caps/status/panel; provider and subagent counts",
                    manager_counts_match_everywhere)

        async def skill_install_refreshes_modern_manager():
            import io
            import zipfile
            from plugins.kira_ops.store import store_client as store_mod
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("skill_three/SKILL.md",
                                 "---\nname: skill_three\ndescription: third\n---\nbody\n")
            payload = buffer.getvalue()
            original_download = store_mod.download_direct_file
            original_validator = store_mod.StoreClient.validate_direct_url
            async def fake_download(url, destination, **kwargs):
                Path(destination).write_bytes(payload)
            store_mod.download_direct_file = fake_download
            store_mod.StoreClient.validate_direct_url = staticmethod(lambda url: "")
            try:
                result = await inst.install_skill_from_url(
                    "https://public.test/skill_three.zip", "skill_three")
            finally:
                store_mod.download_direct_file = original_download
                store_mod.StoreClient.validate_direct_url = staticmethod(original_validator)
            assert result["ok"], result
            assert (workspace / "data" / "skills" / "skill_three" / "SKILL.md").is_file()
            assert len(skills_mgr.skills_info) == 3
            status = await inst.ops_status(ev, include="skills")
            panel = await inst.api_overview()
            assert status["skills"]["total"] == 3 and panel["counts"]["skills"] == 3
        await check("skill install refreshes the modern manager, status and panel immediately",
                    skill_install_refreshes_modern_manager)

        async def skill_normal_writes_keep_working():
            disabled = await inst.ops_action(ev, "skill", "disable", target="skill_one")
            assert disabled["ok"] and not disabled.get("need_confirm"), disabled
            scoped = await inst.ops_action(ev, "skill", "set_scope", target="skill_one",
                                           args={"mode": "allow", "sessions": [parent.sid]})
            assert scoped["ok"] and scoped["scope"]["allow"] == [parent.sid], scoped
            refreshed = await inst.ops_action(ev, "skill", "refresh")
            assert refreshed["ok"] and refreshed["count"] == 3, refreshed
            enabled = await inst.ops_action(ev, "skill", "enable", target="skill_one")
            assert enabled["ok"] and enabled["enabled"], enabled
            cleared = await inst.ops_action(ev, "skill", "set_scope", target="skill_one",
                                            args={"mode": None, "sessions": []})
            assert cleared["ok"] and cleared["scope"] == {}, cleared
        await check("skill enable/disable/scope/refresh remain ordinary standard writes",
                    skill_normal_writes_keep_working)

        async def provider_model_add_uses_320_contract():
            added = await inst.ops_action(ev, "provider", "add_model", target="test_provider",
                                          args={"model_type": "llm", "model_id": "new-model",
                                                "config": {}})
            assert added["ok"] and added["registered"] and not added.get("need_confirm"), added
            assert added["model_id"] != "new-model"  # 3.2 generated immutable internal ID
            models = await inst.ops_read(ev, "provider", "models", target="test_provider")
            assert models["ok"] and models["counts"]["llm"] == 2, models
            assert any("new-model" in item for item in models["models"]["llm"])
        await check("provider add_model returns the real 3.2 internal ID and readable name",
                    provider_model_add_uses_320_contract)

        async def manager_absence_is_not_empty_inventory():
            probe = types.SimpleNamespace(ctx=types.SimpleNamespace(
                skills_mgr=None, message_processor=types.SimpleNamespace()))
            assert inst._manager("skills_mgr", "skills_manager") is not None
            assert inst._manager.__get__(probe, type(probe))("skills_mgr", "skills_manager") is None
            legacy = object()
            probe.ctx.message_processor.skills_manager = legacy
            assert inst._manager.__get__(probe, type(probe))("skills_mgr", "skills_manager") is legacy
        await check("modern-first and legacy fallback remain distinct from unavailable",
                    manager_absence_is_not_empty_inventory)

        async def session_list_is_compact_and_batched():
            original_get = session_mgr.get_session
            calls = []
            def wrapped(*args, **kwargs):
                calls.append(kwargs)
                return original_get(*args, **kwargs)
            session_mgr.get_session = wrapped
            try:
                result = await inst.ops_read(ev, "session", "list")
            finally:
                session_mgr.get_session = original_get
            assert result["total"] == 2, result
            assert calls == [], calls
            rows = {row["session_id"]: row for row in result["items"]}
            assert rows[parent.sid]["kind"] == "im"
            assert rows[child.sid]["kind"] == "subagent"
            assert rows[child.sid]["parent_id"] == parent.internal_id
            only = await inst.ops_read(ev, "session", "list", args={"kind": "subagent"})
            assert only["total"] == 1 and only["items"][0]["internal_id"] == child.internal_id
        await check("session.list supports IM/subagent filters without N full-content reads",
                    session_list_is_compact_and_batched)

        async def session_internal_id_and_model_flow():
            info = await inst.ops_read(ev, "session", "info", target=child.internal_id)
            assert info["session_id"] == child.sid and info["kind"] == "subagent", info
            assert info["subagent"]["status"] == "done"
            changed = await inst.ops_action(
                ev, "session", "llm_model", target=parent.sid,
                args={"llm_model": "test_provider:model_one"},
            )
            assert changed["ok"] and not changed.get("need_confirm"), changed
            stored = await session_mgr.get_session(parent.sid, include_content=False)
            # A prior model migration may map the readable name to a generated ID;
            # the official resolver normalizes the stored reference accordingly.
            assert stored.data["llm_model"] == changed["llm_model"]
            cleared = await inst.ops_action(
                ev, "session", "llm_model", target=parent.sid, args={"llm_model": None})
            assert cleared["ok"] and cleared["llm_model"] is None, cleared
            forbidden = await inst.ops_action(
                ev, "session", "llm_model", target=child.internal_id,
                args={"llm_model": "test_provider:model_one"},
            )
            assert not forbidden["ok"] and "preset" in forbidden["error"], forbidden
            bad = await inst.ops_action(
                ev, "session", "llm_model", target=parent.sid,
                args={"llm_model": "test_provider:missing"},
            )
            assert not bad["ok"] and "not found" in bad["error"], bad
        await check("session internal IDs and per-session model set/clear follow official validation",
                    session_internal_id_and_model_flow)

        async def existing_session_writes_and_clear_preserve_metadata():
            cfg = copy.deepcopy(inst.cfg)
            cfg["risk"] = dict(cfg.get("risk") or {}, level="dangerous",
                               high_risk_sessions=[ev.sid])
            inst.engine.apply_settings(cfg)
            try:
                titled = await inst.ops_action(ev, "session", "title", target=parent.sid,
                                               args={"title": "Updated", "description": "desc"})
                assert titled["ok"] and not titled.get("need_confirm"), titled
                capable = await inst.ops_action(ev, "session", "caps", target=parent.sid,
                                                args={"capabilities": {"tts": {"enabled": True}}})
                assert capable["ok"] and capable["capabilities"]["tts"]["enabled"], capable
                pending = await inst.ops_action(ev, "session", "memory_clear", target=parent.sid)
                assert pending.get("need_confirm") and pending.get("token"), pending
                cleared = await inst.ops_confirm(ev, pending["token"])
                assert cleared["ok"] and cleared["cleared_count"] == 1, cleared
                stored = await session_mgr.get_session(parent.sid)
                assert stored.content == []
                assert stored.session_title == "Updated" and stored.session_description == "desc"
                assert stored.data["capabilities"]["tts"]["enabled"] is True
            finally:
                inst.engine.apply_settings(inst.cfg)
        await check("existing title/caps update and confirmed clear preserve all session metadata",
                    existing_session_writes_and_clear_preserve_metadata)

        async def session_writes_never_create_ghosts():
            for action, args in (
                ("title", {"title": "ghost"}),
                ("caps", {"capabilities": {"tts": {"enabled": True}}}),
                ("llm_model", {"llm_model": None}),
                ("memory_clear", {}),
            ):
                before = await session_mgr.get_session("napcat:gm:ghost", ensure_session=False)
                result = await inst.caps["session"].handle_write(
                    action, {"session_id": "napcat:gm:ghost", **args})
                after = await session_mgr.get_session("napcat:gm:ghost", ensure_session=False)
                assert not result["ok"] and before is None and after is None, (action, result)
            malformed = await inst.caps["session"].handle_write(
                "title", {"session_id": "bad", "title": "x"})
            assert not malformed["ok"] and "must look like" in malformed["error"], malformed
        await check("title/caps/model/clear reject ghosts; malformed SID fails for the right reason",
                    session_writes_never_create_ghosts)

        async def subagent_global_read():
            listed = await inst.ops_read(ev, "subagent", "list")
            assert listed["total"] == 1 and "result" not in listed["items"][0], listed
            info = await inst.ops_read(ev, "subagent", "info", target=child.internal_id)
            assert info["ok"] and info["result"] == "finished", info
            assert info["sid"] == child.sid
            renamed = await inst.ops_action(ev, "session", "title", target=child.internal_id,
                                            args={"title": "Child updated"})
            assert renamed["ok"] and not renamed.get("need_confirm"), renamed
            stored = await session_mgr.get_session(child.sid, include_content=False)
            assert stored.session_title == "Child updated"
        await check("subagent list/info use public manager and bounded result payloads",
                    subagent_global_read)

        async def subagent_preset_crud_is_standard():
            created = await inst.ops_action(ev, "subagent", "preset_save", args={"config": {
                "name": "Researcher", "description": "research", "instruction_source": "custom",
                "instruction": "Investigate thoroughly", "tool_mode": "inherit",
                "skills": ["skill_one"], "model": "test_provider:model_one",
            }})
            assert created["ok"] and created["created"] and not created.get("need_confirm"), created
            preset_id = created["preset_id"]
            listed = await inst.ops_read(ev, "subagent", "preset_list")
            assert listed["total"] == 1 and listed["items"][0]["skills"] == 1, listed
            updated = await inst.ops_action(ev, "subagent", "preset_save", target=preset_id,
                                            args={"patch": {"description": "updated", "max_steps": 8}})
            assert updated["ok"] and not updated["created"] and not updated.get("need_confirm"), updated
            info = await inst.ops_read(ev, "subagent", "preset_info", target=preset_id)
            assert info["description"] == "updated" and info["max_steps"] == 8
            assert info["instruction"] == "Investigate thoroughly"  # patch did not erase fields
            scoped = await inst.ops_action(ev, "subagent", "preset_set_scope", target=preset_id,
                                           args={"mode": "allow", "sessions": [parent.sid]})
            assert scoped["ok"] and scoped["scope"]["allow"] == [parent.sid], scoped
            cleared = await inst.ops_action(ev, "subagent", "preset_set_scope", target=preset_id,
                                            args={"mode": None, "sessions": []})
            assert cleared["ok"] and cleared["scope"] == {}, cleared
            deleted = await inst.ops_action(ev, "subagent", "preset_delete", target=preset_id)
            assert deleted["ok"] and deleted["deleted"] and not deleted.get("need_confirm"), deleted
            assert subagent_mgr.presets.list() == []
        await check("subagent preset create/update/scope/delete need no confirmation at standard",
                    subagent_preset_crud_is_standard)

        async def post_config_is_masked():
            response = await inst.api_set_config({"config": {"confirm": {
                "enabled": False, "sessions": [], "password": "test-only-secret"}}})
            blob = json.dumps(response)
            assert "test-only-secret" not in blob, blob
            assert response["config"]["confirm"]["password"] == "***"
            fresh = pm.get_plugin_inst("kira_ops")
            assert fresh.confirm_cfg["password"] == "test-only-secret"
            assert (await fresh.api_get_config())["config"]["confirm"]["password"] == "***"
        await check("POST and GET config share password masking; stored secret remains functional",
                    post_config_is_masked)

        async def log_prefix_sibling_is_blocked():
            outside = workspace / "data_private"
            outside.mkdir()
            (outside / "secret.log").write_text("canary", encoding="utf-8")
            live = pm.get_plugin_inst("kira_ops")
            result = await live.ops_read(ev, "log", "read_file",
                                         args={"path": "../data_private/secret.log"})
            assert not result["ok"] and "escapes" in result["error"], result
            normal = workspace / "data" / "normal.log"
            normal.write_text("normal", encoding="utf-8")
            allowed = await live.ops_read(ev, "log", "read_file",
                                          args={"path": "normal.log"})
            assert allowed["ok"] and allowed["tail"] == "normal", allowed
        await check("log reader blocks prefix siblings while retaining normal logs",
                    log_prefix_sibling_is_blocked)

        async def skill_atomic_swap_and_rollback():
            from plugins.kira_ops.store import installer as installer_mod
            root = workspace / "data" / "atomic_skills"
            old = root / "demo"
            new = workspace / "new_skill"
            old.mkdir(parents=True)
            new.mkdir()
            (old / "SKILL.md").write_text("old", encoding="utf-8")
            (new / "SKILL.md").write_text("new", encoding="utf-8")
            target = installer_mod.install_skill_dir(root, new, "demo", overwrite=True)
            assert (target / "SKILL.md").read_text() == "new"
            assert not list(root.glob("_kira_ops_*"))
            (new / "SKILL.md").write_text("replacement", encoding="utf-8")
            original_replace = installer_mod.os.replace
            def fail_staging(src, dst):
                if Path(src).name.startswith("_kira_ops_install_"):
                    raise OSError("simulated swap failure")
                return original_replace(src, dst)
            installer_mod.os.replace = fail_staging
            try:
                try:
                    installer_mod.install_skill_dir(root, new, "demo", overwrite=True)
                except OSError as exc:
                    assert "simulated" in str(exc)
                else:
                    raise AssertionError("simulated failure did not fire")
            finally:
                installer_mod.os.replace = original_replace
            assert (target / "SKILL.md").read_text() == "new"
            assert not list(root.glob("_kira_ops_*"))

            # Two worker-thread replacements of the same target must serialize.
            sources = []
            for value in ("concurrent-a", "concurrent-b"):
                source = workspace / value
                source.mkdir()
                (source / "SKILL.md").write_text(value, encoding="utf-8")
                sources.append(source)
            await asyncio.gather(*[
                asyncio.to_thread(installer_mod.install_skill_dir,
                                  root, source, "demo", True)
                for source in sources
            ])
            assert (target / "SKILL.md").read_text() in {"concurrent-a", "concurrent-b"}
            assert not list(root.glob("_kira_ops_*"))
        await check("skill overwrite swaps atomically and restores old version on failure",
                    skill_atomic_swap_and_rollback)

        async def update_without_staged_backup_is_refused():
            from plugins.kira_ops.store import installer as installer_mod
            plugin_root = workspace / "data" / "plugins" / "demo"
            plugin_root.mkdir(parents=True)
            (plugin_root / "main.py").write_text("old", encoding="utf-8")
            pm_fake = types.SimpleNamespace(plugin_dir=plugin_root.parent)
            original_stage = installer_mod._stage_backup
            called = False
            async def no_backup(dest):
                return None
            async def runner(dest):
                nonlocal called
                called = True
                return dest
            installer_mod._stage_backup = no_backup
            try:
                result = await installer_mod._install_and_activate(
                    pm_fake, "demo", update=True, runner=runner)
            finally:
                installer_mod._stage_backup = original_stage
            assert not result["ok"] and not called, result
            assert (plugin_root / "main.py").read_text() == "old"
        await check("plugin update refuses to touch files when rollback staging fails",
                    update_without_staged_backup_is_refused)

        async def direct_download_rechecks_redirects():
            import httpx
            from plugins.kira_ops.store.store_client import download_direct_file
            validated, requested = [], []
            def validator(url):
                validated.append(url)
                if url.startswith("https://127.0.0.1/"):
                    return "private address"
                if not url.startswith("https://"):
                    return "https only"
                return ""
            def handler(request):
                requested.append(str(request.url))
                if request.url.path == "/start":
                    return httpx.Response(302, headers={"Location": "https://127.0.0.1/private"})
                return httpx.Response(200, content=b"ok")
            transport = httpx.MockTransport(handler)
            def factory(**kwargs):
                return httpx.AsyncClient(transport=transport, **kwargs)
            target = workspace / "data" / "redirect.bin"
            try:
                await download_direct_file("https://public.test/start", target,
                                           validate_url=validator, _client_factory=factory)
            except ValueError as exc:
                assert "private address" in str(exc)
            else:
                raise AssertionError("private redirect was followed")
            assert requested == ["https://public.test/start"], requested
            assert len(validated) == 2 and not target.exists()

            validated.clear(); requested.clear()
            def safe_handler(request):
                if request.url.path == "/start":
                    return httpx.Response(302, headers={"Location": "/final"})
                return httpx.Response(200, content=b"ok")
            safe_transport = httpx.MockTransport(safe_handler)
            def safe_factory(**kwargs):
                return httpx.AsyncClient(transport=safe_transport, **kwargs)
            result = await download_direct_file("https://public.test/start", target,
                                                validate_url=validator, _client_factory=safe_factory)
            assert result.read_bytes() == b"ok" and len(validated) == 2

            big_transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"12345"))
            def big_factory(**kwargs):
                return httpx.AsyncClient(transport=big_transport, **kwargs)
            try:
                await download_direct_file("https://public.test/big", target,
                                           validate_url=validator, max_bytes=4,
                                           _client_factory=big_factory)
            except ValueError as exc:
                assert "byte limit" in str(exc)
            else:
                raise AssertionError("oversize response accepted")
            assert not target.exists()
        await check("direct downloads revalidate every redirect and enforce size/cleanup",
                    direct_download_rechecks_redirects)

        async def tool_surface_stays_merged():
            assert len(pm.get_plugin_tools("kira_ops")) == 7
            assert len(inst.caps) == 13, sorted(inst.caps)
            panel = (PLUGIN_DIR / "web" / "app.js").read_text(encoding="utf-8")
            assert "appendTextCells(tr" in panel
            assert "box.innerHTML = warnings.map" not in panel
        await check("still 7 tools / 13 domains; dynamic table and warning text stay inert",
                    tool_surface_stays_merged)

        await pm.terminate("kira_ops")
    finally:
        os.chdir(original_cwd)
        import shutil
        shutil.rmtree(workspace, ignore_errors=True)

    print(f"\npassed: {len(PASSED)}  failed: {len(FAILED)}")
    if FAILED:
        raise SystemExit(1)
    print("ALL OK")


if __name__ == "__main__":
    asyncio.run(run())
