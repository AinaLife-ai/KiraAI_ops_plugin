"""Functional test part 2: provider / persona / mcp / config-write / control.

Shares the stub context style of test_live_ops.py but focuses on the domains
that were not covered there.

Run:
    python data/plugins/kira_ops/tests/test_live_ops2.py
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import tempfile
import types
from pathlib import Path

FILE = Path(__file__).resolve()
ROOT = FILE.parents[4]
PLUGIN_DIR = FILE.parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if "plugins" not in sys.modules:
    pkg = types.ModuleType("plugins")
    pkg.__path__ = [str(ROOT / "data" / "plugins")]
    sys.modules["plugins"] = pkg

main_mod = importlib.import_module("plugins.kira_ops.main")


class FakeConfig(dict):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.saved = 0

    def save_config(self):
        self.saved += 1

    def get_config(self, path, default=None):
        cur = self
        for part in str(path or "").split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur


class FakeProviderMgr:
    def __init__(self):
        self._models = {"llm": {"m1": {"model_id": "m1"}}}
        self._provider = types.SimpleNamespace(
            provider_name="P1", provider_type="OpenAI",
            provider_config={"api_key": "SECRET", "base_url": "https://api.test"})

    def get_all_providers(self):
        return {"p1": {"format": "OpenAI"}}

    def get_provider_info(self, pid):
        return self._provider if pid == "p1" else None

    def get_models(self, pid):
        return self._models if pid == "p1" else None

    def register_model(self, pid, mtype, mid, cfg):
        self._models.setdefault(mtype, {})[mid] = cfg
        return True

    def update_model(self, pid, mtype, mid, cfg):
        self._models.setdefault(mtype, {})[mid] = cfg
        return True

    def delete_model(self, pid, mtype, mid):
        self._models.get(mtype, {}).pop(mid, None)
        return True

    def fetch_remote_models(self, pid, mtype):
        return [{"id": "m1"}, {"id": "m2"}]

    def set_provider(self, pid, cfg):
        return True


class FakePersonaMgr:
    def __init__(self):
        self.personas = {
            "default": types.SimpleNamespace(
                id="default", name="Default", format="yaml",
                content="hello persona", is_active=True),
            "other": types.SimpleNamespace(
                id="other", name="Other", format="text",
                content="x", is_active=False),
        }

    async def list_personas(self):
        return list(self.personas.values())

    async def get_persona(self, pid):
        return self.personas.get(pid)

    async def get_active_persona(self):
        return self.personas["default"]

    async def set_active_persona(self, pid):
        return pid in self.personas

    async def create_persona(self, info):
        self.personas[info.id] = info
        return True

    async def update_persona(self, info):
        return info.id in self.personas

    async def delete_persona(self, pid):
        return self.personas.pop(pid, None) is not None


class FakeMcpServer:
    def __init__(self, sid):
        self.id = sid
        self.name = "srv"
        self.enabled = True
        self.type = "stdio"
        self.description = ""
        self.tools = []
        self.disabled_tools = []


class FakeMcpMgr:
    def __init__(self):
        self.servers = [FakeMcpServer("s1")]

    def get_server_scope(self, sid):
        return {}

    def get_server_config_for_editor(self, sid):
        return {"command": "python", "args": ["-m", "x"]}

    def set_tool_enabled(self, sid, tool, enabled):
        return True

    def set_server_scope(self, sid, mode, sessions):
        return True

    async def enable_server(self, sid):
        return True

    async def disable_server(self, sid):
        return True

    async def delete_server(self, sid):
        self.servers = [s for s in self.servers if s.id != sid]

    async def update_server_from_editor(self, sid, name, desc, editor):
        return True

    def add_or_update_server_from_config(self, name, desc, cfg):
        return FakeMcpServer("s2")


class FakePluginMgr:
    def __init__(self):
        self.plugin_dir = PLUGIN_DIR.parent
        self.config_updates = []

    def list_plugins(self):
        return []

    def has_plugin(self, pid):
        return pid in ("agent", "kira_ops")

    def is_plugin_enabled(self, pid):
        return True

    def get_plugin_config(self, pid):
        return {"file_access": {"permission_mode": "allow_list",
                                "session_list": [], "extra_read_paths": ["data"]},
                "exec_access": {"permission_mode": "allow_list", "session_list": []}}

    def get_plugin_info(self, pid):
        return None

    def get_plugin_schema(self, pid):
        return []

    def get_plugin_manifest(self, pid):
        return {}

    def get_plugin_load_errors(self):
        return {}

    def get_plugin_inst(self, pid):
        return None

    async def set_plugin_enabled(self, pid, enabled):
        return True

    async def update_plugin_config(self, pid, cfg):
        self.config_updates.append((pid, cfg))
        return cfg

    async def reload(self, pid):
        return None


class FakeCtx:
    def __init__(self, data_dir):
        self.config = FakeConfig({
            "bot_config": {"bot": {"name": "tester", "timeout": 30}},
            "providers": {"p1": {"format": "OpenAI", "name": "P1",
                                 "provider_config": {"api_key": "SECRET",
                                                     "base_url": "https://api.test"}}},
        })
        self.plugin_mgr = FakePluginMgr()
        self.provider_mgr = FakeProviderMgr()
        self.persona_mgr = FakePersonaMgr()
        self.message_processor = types.SimpleNamespace(
            skills_manager=None,
            mcp_manager=FakeMcpMgr(),
        )
        self.session_mgr = types.SimpleNamespace(
            get_session_info=lambda: [],
            update_session_info=lambda *a, **k: None,
        )
        self._data_dir = data_dir

    def get_plugin_data_dir(self):
        return self._data_dir


class FakeEvent:
    def __init__(self, sid, uid="111"):
        self.sid = sid
        self.messages = [types.SimpleNamespace(sender=types.SimpleNamespace(user_id=uid))]
        self.session = None


FAILED = []
PASSED = []


def check(name, fn):
    try:
        fn()
        PASSED.append(name)
        print(f"  ok  {name}")
    except Exception as exc:
        import traceback
        FAILED.append((name, exc))
        print(f"FAIL  {name}: {exc}")
        traceback.print_exc()


def run():
    print("== kira_ops live ops test (part 2) ==")
    tmp = Path(tempfile.mkdtemp(prefix="kira_ops_live2_"))
    ctx = FakeCtx(tmp / "plugin_data")
    schema = json.loads((PLUGIN_DIR / "schema.json").read_text(encoding="utf-8"))
    defaults = {}
    for section, spec in schema.items():
        if isinstance(spec, dict) and spec.get("type") == "section":
            defaults[section] = {}
            for key, field in (spec.get("fields") or {}).items():
                if isinstance(field, dict) and "default" in field:
                    defaults[section][key] = field["default"]

    plugin = main_mod.KiraOpsPlugin(ctx, defaults)
    ev = FakeEvent("qq:gm:427674145", "769690776")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    call = loop.run_until_complete

    # ---- provider.list ----
    def t_provider_list():
        r = call(plugin.ops_read(ev, "provider", "list"))
        assert r.get("ok") and r["count"] == 1, r
        assert r["items"][0]["name"] == "P1", r
    check("provider.list", t_provider_list)

    # ---- provider.info masks key ----
    def t_provider_info():
        r = call(plugin.ops_read(ev, "provider", "info", target="p1"))
        assert r.get("ok"), r
        assert r["config"]["api_key"] == "***", r
        assert r["config"]["base_url"] == "https://api.test", r
    check("provider.info masks api_key", t_provider_info)

    # ---- provider.add_model blocked on secret field ----
    def t_provider_add_blocked():
        r = call(plugin.ops_action(ev, "provider", "add_model", target="p1",
                                   args={"model_type": "llm", "model_id": "mx",
                                         "config": {"api_key": "x"}}))
        assert not r.get("ok"), r
    check("provider.add_model refuses secret config", t_provider_add_blocked)

    # ---- provider.add_model happy path ----
    def t_provider_add_ok():
        r = call(plugin.ops_action(ev, "provider", "add_model", target="p1",
                                   args={"model_type": "llm", "model_id": "mx",
                                         "config": {"base_url": "https://api.test"}}))
        assert r.get("ok"), r
        assert "mx" in ctx.provider_mgr._models["llm"], ctx.provider_mgr._models
    check("provider.add_model accepts safe config", t_provider_add_ok)

    # ---- provider.fetch_remote ----
    def t_provider_remote():
        r = call(plugin.ops_read(ev, "provider", "fetch_remote", target="p1",
                                 args={"model_type": "llm"}))
        assert r.get("ok") and r["count"] == 2, r
    check("provider.fetch_remote", t_provider_remote)

    # ---- persona.list / info ----
    def t_persona_read():
        r = call(plugin.ops_read(ev, "persona", "list"))
        assert r.get("ok") and r["count"] == 2, r
        r2 = call(plugin.ops_read(ev, "persona", "get_active"))
        assert r2.get("ok") and r2["id"] == "default", r2
    check("persona.list + get_active", t_persona_read)

    # ---- persona write blocked by default (persona_write=false) ----
    def t_persona_blocked():
        r = call(plugin.ops_action(ev, "persona", "create",
                                   args={"persona_id": "newp", "content": "hi"}))
        assert not r.get("ok"), r
        # denied either by the risk gate (standard level) or by persona_write=false
        err = (r.get("error") or "").lower()
        assert "denied" in err or "disabled" in err, r
    check("persona writes blocked by default", t_persona_blocked)

    # ---- persona write still blocked by persona_write even at dangerous level ----
    def t_persona_switch_only():
        plugin.cfg["risk"] = dict(plugin.cfg.get("risk") or {}, level="dangerous",
                                  high_risk_sessions=["qq:gm:427674145"])
        plugin.engine.apply_settings(plugin.cfg)
        r = call(plugin.ops_action(ev, "persona", "update",
                                   args={"persona_id": "default", "content": "hack"}))
        assert not r.get("ok"), r
        assert "disabled" in (r.get("error") or "").lower(), r
    check("persona_write=false blocks even at dangerous level", t_persona_switch_only)

    # ---- persona write allowed when enabled + high-risk token ----
    def t_persona_allowed():
        plugin.cfg["protected"] = dict(plugin.cfg.get("protected") or {},
                                       persona_write=True)
        plugin.cfg["risk"] = dict(plugin.cfg.get("risk") or {}, level="dangerous",
                                  high_risk_sessions=["qq:gm:427674145"])
        plugin.engine.apply_settings(plugin.cfg)
        r = call(plugin.ops_action(ev, "persona", "create",
                                   args={"persona_id": "newp", "content": "hi"}))
        assert r.get("need_confirm") and r.get("token"), r
        r2 = call(plugin.ops_confirm(ev, r["token"]))
        assert r2.get("ok"), r2
        assert "newp" in ctx.persona_mgr.personas, ctx.persona_mgr.personas
    check("persona create works with switch + token", t_persona_allowed)

    # ---- mcp.list / info ----
    def t_mcp():
        r = call(plugin.ops_read(ev, "mcp", "list"))
        assert r.get("ok") and r["count"] == 1, r
        r2 = call(plugin.ops_read(ev, "mcp", "info", target="s1"))
        assert r2.get("ok") and r2["editor_config"]["command"] == "python", r2
    check("mcp.list + info", t_mcp)

    # ---- mcp.delete requires token (high risk) ----
    def t_mcp_delete_token():
        r = call(plugin.ops_action(ev, "mcp", "delete", target="s1"))
        assert r.get("need_confirm"), r
        r2 = call(plugin.ops_confirm(ev, r["token"]))
        assert r2.get("ok"), r2
        assert not ctx.message_processor.mcp_manager.servers, "server not deleted"
    check("mcp.delete gated by token then executes", t_mcp_delete_token)

    # ---- config.set happy path (non-secret field) ----
    def t_config_write():
        before = ctx.config.saved
        r = call(plugin.ops_config(ev, "config",
                                   {"timeout": 99},
                                   path="bot_config.bot"))
        assert r.get("ok"), r
        assert ctx.config["bot_config"]["bot"]["timeout"] == 99, ctx.config
        assert ctx.config.saved > before, "save_config not called"
    check("config.set writes non-secret fields", t_config_write)

    # ---- agent policy: protected path rejected ----
    def t_agent_path_guard():
        r = call(plugin.ops_config(ev, "agent", {
            "file_access": {"extra_read_paths": ["core"]}
        }))
        assert not r.get("ok"), r
        assert "blocked" in (r.get("error") or "").lower(), r
    check("agent policy refuses protected paths", t_agent_path_guard)

    # ---- agent policy: harmless change accepted ----
    def t_agent_ok():
        r = call(plugin.ops_config(ev, "agent", {
            "file_access": {"extra_read_paths": ["data/files"]}
        }))
        assert r.get("ok"), r
    check("agent policy accepts safe paths", t_agent_ok)

    # ---- control: restart disabled by default ----
    def t_control_off():
        r = call(plugin.ops_action(ev, "control", "restart"))
        assert not r.get("ok"), r
        assert "disabled" in (r.get("error") or "").lower() or \
               "denied" in (r.get("error") or "").lower(), r
    check("control.restart off by default", t_control_off)

    # ---- control: enabled -> confirm token, never actually exits in test ----
    def t_control_gated():
        plugin.cfg["control"] = {"allow_restart": True, "allow_shutdown": False}
        plugin.cfg["risk"] = dict(plugin.cfg.get("risk") or {}, level="full",
                                  high_risk_sessions=["qq:gm:427674145"])
        plugin.engine.apply_settings(plugin.cfg)
        r = call(plugin.ops_action(ev, "control", "restart"))
        assert r.get("need_confirm") and r.get("token"), r
        # deliberately do NOT confirm: confirming would schedule a real exit
        ok, _ = plugin.confirm.take(r["token"], ev.sid, "769690776")
        assert ok
    check("control.restart gated behind switch+token", t_control_gated)

    loop.close()
    print()
    print(f"passed: {len(PASSED)}  failed: {len(FAILED)}")
    if FAILED:
        for name, exc in FAILED:
            print(f"  FAILED: {name}: {exc}")
        sys.exit(1)
    print("ALL OK")


if __name__ == "__main__":
    run()
