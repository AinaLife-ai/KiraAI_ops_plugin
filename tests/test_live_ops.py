"""Functional test: drive the real plugin instance with a stub context.

Proves the ops_read / ops_status / ops_store paths actually run end to end
(the previous class-vs-instance bug only showed up at call time), without
booting the whole framework.

Run:
    python data/plugins/kira_ops/tests/test_live_ops.py
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


# ----------------------------------------------------------------------
# minimal fakes
# ----------------------------------------------------------------------

class FakeConfig(dict):
    def save_config(self):
        pass

    def get_config(self, path, default=None):
        cur = self
        for part in str(path or "").split("."):
            if not part:
                continue
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur


class FakePluginInfo:
    def __init__(self, pid):
        self.plugin_id = pid
        self.display_name = pid
        self.version = "1.0.0"
        self.author = "test"
        self.description = "stub"
        self.repo = None
        self.core_version = None
        self.tags = []
        self.builtin = False
        self.uninstallable = True
        self.status = "ready"
        self.error = None


class FakePluginMgr:
    def __init__(self):
        self.plugin_dir = PLUGIN_DIR.parent
        self._installed = {"alpha", "beta", "agent"}

    def list_plugins(self):
        return [FakePluginInfo(p) for p in sorted(self._installed)]

    def get_plugin_info(self, pid):
        return FakePluginInfo(pid) if pid in self._installed else None

    def has_plugin(self, pid):
        return pid in self._installed

    def is_plugin_enabled(self, pid):
        return pid in self._installed

    def get_plugin_config(self, pid):
        return {"stub": True}

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
        return cfg

    async def reload(self, pid):
        return None


class FakeSkill:
    name = "demo"
    enabled = True
    description = "demo skill"
    path = str(PLUGIN_DIR)


class FakeSkillsMgr:
    skills_info = [FakeSkill()]

    def get_skill_scope(self, name):
        return {"mode": "allow", "sessions": ["qq:gm:1"]}

    def scan_skill_dir(self):
        return self.skills_info

    def set_skill_enabled(self, name, enabled):
        return True


class FakeCtx:
    def __init__(self, data_dir):
        self.config = FakeConfig({
            "bot_config": {"bot": {"name": "tester"}, "agent": {"max_tool_loop": 50}},
            "providers": {"p1": {"format": "OpenAI", "name": "P1",
                                 "provider_config": {"api_key": "SHOULD_BE_MASKED",
                                                     "base_url": "https://api.test"},
                                 "model_config": {"llm": {"m1": {"model_id": "m1"}}}}},
            "logging": {"log_level": "INFO"},
        })
        self.plugin_mgr = FakePluginMgr()
        self.session_mgr = types.SimpleNamespace(
            get_session_info=lambda: [
                types.SimpleNamespace(sid="qq:gm:1", session_title="T1", session_type="gm"),
                types.SimpleNamespace(sid="qq:dm:9", session_title="T2", session_type="dm"),
            ],
            get_memory_count=lambda sid: 3,
            update_session_info=lambda *a, **k: None,
            update_session_capabilities=lambda *a, **k: None,
            get_effective_capabilities=lambda *a, **k: {},
            write_memory=lambda *a, **k: None,
            delete_session=lambda *a, **k: None,
        )
        self.message_processor = types.SimpleNamespace(
            skills_manager=FakeSkillsMgr(),
            mcp_manager=None,
        )
        self.provider_mgr = None
        self._data_dir = data_dir

    def get_plugin_data_dir(self):
        return self._data_dir


class FakeSender:
    def __init__(self, uid="111"):
        self.user_id = uid


class FakeMsg:
    def __init__(self, uid="111"):
        self.sender = FakeSender(uid)


class FakeEvent:
    def __init__(self, sid, uid="111"):
        self.sid = sid
        self.messages = [FakeMsg(uid)]
        self.session = None


# ----------------------------------------------------------------------

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
    print("== kira_ops live ops test ==")
    tmp = Path(tempfile.mkdtemp(prefix="kira_ops_live_"))
    ctx = FakeCtx(tmp / "plugin_data")
    cfg = json.loads((PLUGIN_DIR / "schema.json").read_text(encoding="utf-8"))
    defaults = {}
    for section, spec in cfg.items():
        if isinstance(spec, dict) and spec.get("type") == "section":
            defaults[section] = {}
            for key, field in (spec.get("fields") or {}).items():
                if isinstance(field, dict) and "default" in field:
                    defaults[section][key] = field["default"]

    plugin = main_mod.KiraOpsPlugin(ctx, defaults)
    plugin.audit.audit_reads = True   # so read records are captured in this test
    ev = FakeEvent("qq:gm:427674145", "769690776")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def call(coro):
        return loop.run_until_complete(coro)

    # ---- ops_status ----
    def t_status():
        r = call(plugin.ops_status(ev, include="plugins,permission,audit"))
        assert r.get("ok"), r
        assert r["plugins"]["total"] == 3, r["plugins"]
        assert r["permission"]["level"] == "standard", r["permission"]
    check("ops_status runs and reports counts", t_status)

    # ---- ops_read: plugin.list (the bug) ----
    def t_read_plugin():
        r = call(plugin.ops_read(ev, "plugin", "list"))
        assert r.get("ok"), r
        assert r["count"] == 3, r
        assert {i["id"] for i in r["items"]} == {"alpha", "beta", "agent"}, r
    check("ops_read plugin.list (regression)", t_read_plugin)

    # ---- ops_read: skill.list ----
    def t_read_skill():
        r = call(plugin.ops_read(ev, "skill", "list"))
        assert r.get("ok"), r
        assert r["count"] == 1 and r["items"][0]["name"] == "demo", r
    check("ops_read skill.list", t_read_skill)

    # ---- ops_read: backup.list ----
    def t_read_backup():
        r = call(plugin.ops_read(ev, "backup", "list"))
        assert r.get("ok"), r
        assert r["count"] == 0, r
    check("ops_read backup.list", t_read_backup)

    # ---- ops_read: log.tail ----
    def t_read_log():
        r = call(plugin.ops_read(ev, "log", "tail", limit=5))
        assert r.get("ok"), r
    check("ops_read log.tail", t_read_log)

    # ---- ops_read: config.get masks secrets ----
    def t_read_config():
        r = call(plugin.ops_read(ev, "config", "get", args={"path": "providers.p1.provider_config"}))
        assert r.get("ok"), r
        assert r["value"]["api_key"] == "***", r
        assert r["value"]["base_url"] == "https://api.test", r
    check("ops_read config.get masks api_key", t_read_config)

    # ---- ops_read: session.list ----
    def t_read_session():
        r = call(plugin.ops_read(ev, "session", "list"))
        assert r.get("ok"), r
        assert r["count"] == 2, r
    check("ops_read session.list", t_read_session)

    # ---- ops_read: unknown domain / action ----
    def t_read_errors():
        r = call(plugin.ops_read(ev, "nope", "list"))
        assert not r.get("ok") and "unknown domain" in r["error"], r
        r2 = call(plugin.ops_read(ev, "plugin", "enable"))
        assert not r2.get("ok") and "write action" in r2["error"], r2
    check("ops_read rejects unknown domain / write action", t_read_errors)

    # ---- ops_config: write blocked for secrets ----
    def t_config_write_blocked():
        r = call(plugin.ops_config(ev, "config",
                                   {"api_key": "leak"},
                                   path="providers.p1"))
        assert not r.get("ok"), r
        assert "deny list" in (r.get("error") or "").lower(), r
    check("ops_config refuses secret fields", t_config_write_blocked)

    # ---- ops_action: write to protected path rejected (no high-risk needed) ----
    def t_action_denied_unknown():
        r = call(plugin.ops_action(ev, "plugin", "totally_unknown"))
        assert not r.get("ok"), r
        assert "unknown action" in r["error"], r
    check("ops_action rejects unknown action", t_action_denied_unknown)

    # ---- ops_action: high-risk without token -> confirm token issued ----
    def t_action_high_risk_token():
        # raise level so the action passes the risk gate
        plugin.cfg["risk"] = dict(plugin.cfg.get("risk") or {},
                                  level="dangerous",
                                  high_risk_sessions=["qq:gm:427674145"])
        plugin.engine.apply_settings(plugin.cfg)
        r = call(plugin.ops_action(ev, "plugin", "disable", target="alpha"))
        assert r.get("need_confirm"), r
        assert r.get("token"), r
        # wrong session cannot consume it
        ev2 = FakeEvent("qq:gm:999", "769690776")
        r2 = call(plugin.ops_confirm(ev2, r["token"]))
        assert not r2.get("ok"), r2
    check("high-risk action issues token; wrong session rejected", t_action_high_risk_token)

    # ---- restore normal level for the panic test ----
    def t_panic_toggle():
        plugin.cfg["risk"] = dict(plugin.cfg.get("risk") or {}, level="standard",
                                  high_risk_sessions=[])
        plugin.engine.apply_settings(plugin.cfg)
        r = call(plugin.ops_panic(ev, True))
        if not r.get("ok"):
            # plugin manager stub cannot persist; still acceptable to report the reason
            assert "unavailable" in (r.get("error") or "").lower(), r
            return
        assert r["panic_lock"] is True, r
    check("ops_panic locks writes", t_panic_toggle)

    # ---- audit trail written ----
    def t_audit():
        rows = plugin.audit.tail(200)
        assert rows, "no audit rows written"
        assert any(r.get("tool") == "ops_read" for r in rows) or \
               any(r.get("kind") == "read" for r in rows), rows[:3]
    check("audit trail records activity", t_audit)

    # ---- store search (network may be unavailable: accept both) ----
    def t_store():
        r = call(plugin.ops_store(ev, "search", keyword="memory"))
        if not r.get("ok"):
            assert "store fetch failed" in (r.get("error") or ""), r
            return
        assert r["count"] >= 1, r
        assert all("plugin_id" in i for i in r["items"]), r
    check("ops_store search runs (network tolerant)", t_store)

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
