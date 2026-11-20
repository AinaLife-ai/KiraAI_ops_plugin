"""3.2.1 metadata-only resolution, mixed prompts, and session-aware LLM selection."""

from __future__ import annotations

import asyncio
import functools
import json
import os
import re
import shutil
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

version_text = (ROOT / "core" / "config" / "default.py").read_text(encoding="utf-8")
VERSION = re.search(r'^VERSION\s*=\s*["\']([^"\']+)', version_text, re.MULTILINE).group(1)
if Version(VERSION.lstrip("v")) < Version("3.2.1"):
    print(f"SKIP: 3.2.1 supplement suite on {VERSION}")
    raise SystemExit(0)


async def main():
    original_cwd = Path.cwd()
    workspace = Path(tempfile.mkdtemp(prefix="kira_ops_321_suite_"))
    (workspace / "data").mkdir()
    os.chdir(workspace)
    passed = 0
    try:
        plugins_pkg = types.ModuleType("plugins")
        plugins_pkg.__path__ = [str(PLUGIN_DIR.parent)]
        sys.modules["plugins"] = plugins_pkg

        from core.agent.config import ToolExecutionConfig
        from core.agent.subagent_executor import SubAgentExecutionHooks
        from core.agent.subagent_presets import SubAgentPresetStore
        from core.chat.session_manager import SessionManager
        from core.config import KiraConfig
        from core.db.db_mgr import DatabaseManager
        from core.db.service import DatabaseService
        from core.persona.model import PersonaInfo
        from plugins.kira_ops.caps.session_cap import SessionCap, _resolve_sid, _sm_get

        dbm = DatabaseManager(f"sqlite+aiosqlite:///{(workspace / 'data' / 'test.db').as_posix()}")
        await dbm.init()
        db = DatabaseService(dbm)
        await db.init_tables()
        sm = SessionManager(db, KiraConfig())
        await sm.update_session(
            "napcat:gm:parent", title="Parent",
            content=[[{"role": "user", "content": "large-content-marker"}]],
        )
        parent = await sm.get_session("napcat:gm:parent", include_content=False)
        child = await sm.create_subagent_session(
            title="Child", parent_id=parent.internal_id,
            data={"subagent": {"status": "done", "settings": {}}},
        )

        # 1) The new optional argument is actually used on every metadata-only path.
        resolve_calls = []
        original_resolve = sm.resolve_session
        @functools.wraps(original_resolve)
        def resolve_probe(*args, **kwargs):
            resolve_calls.append(kwargs)
            return original_resolve(*args, **kwargs)
        sm.resolve_session = resolve_probe
        resolved = await _resolve_sid(sm, child.internal_id)
        sm.resolve_session = original_resolve
        assert resolved == child.sid
        assert resolve_calls == [{"include_content": False}], resolve_calls

        get_calls = []
        original_get = sm.get_session
        @functools.wraps(original_get)
        def get_probe(*args, **kwargs):
            get_calls.append(kwargs)
            return original_get(*args, **kwargs)
        sm.get_session = get_probe
        metadata = await _sm_get(sm, child.sid, include_content=False)
        cap = object.__new__(SessionCap)
        exists = await cap._exists(sm, child.sid)
        sm.get_session = original_get
        assert metadata is not None and metadata.content == [] and exists is True
        assert get_calls == [
            {"ensure_session": False, "include_content": False},
            {"ensure_session": False, "include_content": False},
        ], get_calls
        passed += 1
        print("  ok  internal-ID resolution, detail loading and existence checks are metadata-only")

        # 2) OPS accepts mixed presets at standard and preserves BOTH prompt sources.
        from plugins.kira_ops.main import KiraOpsPlugin
        store = SubAgentPresetStore(workspace / "data" / "config" / "subagent_presets.json")
        await store.initialize()

        class Personas:
            async def get_persona(self, persona_id):
                assert persona_id == "mixed_persona"
                return PersonaInfo(id=persona_id, name="Mixed", content="PERSONA-BASE")

        subagent_mgr = types.SimpleNamespace(presets=store)
        ctx = types.SimpleNamespace(
            config=KiraConfig(), session_mgr=sm, provider_mgr=None,
            subagent_mgr=subagent_mgr, message_processor=types.SimpleNamespace(),
            skills_mgr=None, mcp_mgr=None, plugin_mgr=None,
            get_plugin_data_dir=lambda: workspace / "data" / "plugin_data" / "kira_ops",
        )
        schema = json.loads((PLUGIN_DIR / "schema.json").read_text(encoding="utf-8"))
        defaults = {
            section: {key: field.get("default") for key, field in (spec.get("fields") or {}).items()}
            for section, spec in schema.items() if spec.get("type") == "section"
        }
        plugin = KiraOpsPlugin(ctx, defaults)
        event = types.SimpleNamespace(
            sid="napcat:gm:parent", session=None,
            messages=[types.SimpleNamespace(sender=types.SimpleNamespace(user_id="1"))],
        )
        created = await plugin.ops_action(event, "subagent", "preset_save", args={"config": {
            "name": "Mixed researcher", "instruction_source": "mixed",
            "persona_id": "mixed_persona", "instruction": "CUSTOM-TASK",
            "description": "3.2.1 mixed prompt", "tool_mode": "inherit",
        }})
        assert created["ok"] and not created.get("need_confirm"), created
        preset_id = created["preset_id"]
        stored = store.get(preset_id)
        assert stored.instruction_source == "mixed"
        assert stored.persona_id == "mixed_persona" and stored.instruction == "CUSTOM-TASK"
        execution = stored.execution_config(ToolExecutionConfig())
        assert execution.persona_id == "mixed_persona" and execution.instruction == "CUSTOM-TASK"
        hooks = SubAgentExecutionHooks(
            config=execution, persona_manager=Personas(), skills_manager=None,
        )
        prompt_ctx = types.SimpleNamespace(messages=[])
        message = await hooks._refresh_persona(prompt_ctx)
        assert message.content == "PERSONA-BASE\n\nCUSTOM-TASK", message.content

        updated = await plugin.ops_action(event, "subagent", "preset_save", target=preset_id,
                                          args={"patch": {"instruction": "CUSTOM-UPDATED"}})
        assert updated["ok"] and not updated.get("need_confirm"), updated
        assert store.get(preset_id).persona_id == "mixed_persona"
        assert store.get(preset_id).instruction == "CUSTOM-UPDATED"
        deleted = await plugin.ops_action(event, "subagent", "preset_delete", target=preset_id)
        assert deleted["ok"] and not deleted.get("need_confirm"), deleted
        passed += 1
        print("  ok  mixed preset CRUD stays standard and composes persona + custom instruction")

        # 3) OPS's stored override is consumed by the new session-aware LLM lookup.
        from core.provider import ProviderManager

        class SessionProviders:
            def __init__(self):
                self.selected = []

            def get_model_info(self, provider_id, model_id, model_type):
                return types.SimpleNamespace(provider_id=provider_id, model_id=model_id)

            def get_llm(self, reference):
                self.selected.append(reference)
                return {"client": reference}

        providers = SessionProviders()
        ctx.provider_mgr = providers
        changed = await plugin.ops_action(
            event, "session", "llm_model", target=parent.sid,
            args={"llm_model": "test_provider:model_one"},
        )
        assert changed["ok"] and not changed.get("need_confirm"), changed
        selected_session = await sm.get_session(parent.sid, include_content=False)
        client = ProviderManager.get_default_llm(providers, session=selected_session)
        assert client == {"client": "test_provider:model_one"}
        assert providers.selected == ["test_provider:model_one"]
        cleared = await plugin.ops_action(
            event, "session", "llm_model", target=parent.sid, args={"llm_model": None},
        )
        assert cleared["ok"] and cleared["llm_model"] is None, cleared
        assert (await sm.get_session(parent.sid, include_content=False)).data.get("llm_model") is None
        passed += 1
        print("  ok  OPS session model override is selected by 3.2.1 session-aware LLM lookup")
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(workspace, ignore_errors=True)

    print(f"\npassed: {passed}  failed: 0")
    print("ALL OK")


if __name__ == "__main__":
    asyncio.run(main())
