"""Integration tests driven by the REAL framework (no stubs).

The other suites use fake managers, which is why a class-vs-instance bug and a
stale-submodule bug once shipped unnoticed: stubs prove the logic, not the
contract. This suite boots the actual KiraAI objects (KiraConfig,
DatabaseService, ProviderManager, FuncToolManager, PersonaManager,
SessionManager, MCPManager, SkillsManager, PluginManager), loads the plugin the
way the framework does, and then exercises the tools against those managers.

Run from the KiraAI root:
    python data/plugins/kira_ops/tests/test_integration_real.py

No network access is required; a throw-away plugin and the plugin's own data
directory are the only things written.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import types
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

FILE = Path(__file__).resolve()
ROOT = FILE.parents[4]
PLUGIN_DIR = FILE.parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if "plugins" not in sys.modules:
    pkg = types.ModuleType("plugins")
    pkg.__path__ = [str(ROOT / "data" / "plugins")]
    sys.modules["plugins"] = pkg

from core.agent.func_tool_manager import FuncToolManager  # noqa: E402
from core.agent.mcp_mgr import MCPManager  # noqa: E402
from core.agent.skills_mgr import SkillsManager  # noqa: E402
from core.chat.message_utils import KiraCustomEvent  # noqa: E402
from core.chat.session_manager import SessionManager  # noqa: E402
from core.config import KiraConfig  # noqa: E402
from core.db.db_mgr import DatabaseManager  # noqa: E402
from core.db.service import DatabaseService  # noqa: E402
from core.event_bus import EventBus  # noqa: E402
from core.persona import PersonaManager  # noqa: E402
from core.plugin import PluginManager  # noqa: E402
from core.plugin.plugin_context import PluginContext  # noqa: E402
from core.provider import ProviderManager  # noqa: E402
from core.statistics import Statistics  # noqa: E402
from core.utils.path_utils import get_data_path  # noqa: E402

FAILED = []
PASSED = []


def _assert(condition, detail=""):
    if not condition:
        raise AssertionError(detail or "assertion failed")


def check(name, fn):
    try:
        fn()
    except AssertionError as exc:
        FAILED.append((name, exc))
        print(f"FAIL  {name}: {exc}")
    except Exception as exc:  # noqa: BLE001
        import traceback
        FAILED.append((name, exc))
        print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
        traceback.print_exc()
    else:
        PASSED.append(name)
        print(f"  ok  {name}")


class Ev:
    """Minimal batch event - the tools only read .sid / .messages."""

    def __init__(self, sid="qq:gm:980001", uid="980001"):
        self.sid = sid
        self.messages = [SimpleNamespace(sender=SimpleNamespace(user_id=uid))]
        self.session = None


def plugin_zip(plugin_id: str, value: int) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{plugin_id}/manifest.json", json.dumps({
            "plugin_id": plugin_id, "display_name": plugin_id, "version": "9.9.9",
            "author": "test", "description": "installer harness",
        }))
        zf.writestr(f"{plugin_id}/main.py",
                    "from core.plugin import BasePlugin\n"
                    "from . import impl\n\n"
                    "class P(BasePlugin):\n"
                    "    async def initialize(self):\n        pass\n"
                    "    async def terminate(self):\n        pass\n")
        zf.writestr(f"{plugin_id}/impl.py", f"VALUE = {value}\n")
    return buf.getvalue()


async def boot():
    kira_config = KiraConfig()
    db_manager = DatabaseManager(
        f"sqlite+aiosqlite:///{(get_data_path() / 'kira_ops_test.db').as_posix()}")
    await db_manager.init()
    db = DatabaseService(db_manager)
    await db.init_tables()

    ctx = PluginContext(
        db=db,
        config=kira_config,
        event_bus=EventBus(Statistics(), asyncio.Queue()),
        provider_mgr=ProviderManager(db, kira_config),
        tool_mgr=FuncToolManager(kira_config),
        adapter_mgr=None,
        persona_mgr=PersonaManager(db),
        session_mgr=SessionManager(db, kira_config),
        sticker_manager=None,
        message_processor=SimpleNamespace(
            mcp_manager=MCPManager(FuncToolManager(kira_config)),
            skills_manager=SkillsManager()),
    )
    pm = PluginManager(ctx)
    ctx.plugin_mgr = pm
    return ctx, pm, kira_config


def main():
    print("== kira_ops real-framework integration ==")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def run(coro):
        return loop.run_until_complete(coro)

    ctx, pm, kira_config = run(boot())

    # ---------------------------------------------------------------- load
    loaded = run(pm.load_plugin_from_dir(PLUGIN_DIR, auto_install=False))
    check("framework loads the plugin from disk",
          lambda: _assert(loaded == "kira_ops", f"loaded={loaded}"))

    info = pm.get_plugin_info("kira_ops")
    check("plugin reports status=ready",
          lambda: _assert(info.status == "ready", f"{info.status} {info.error}"))

    run(pm.init_plugin("kira_ops"))
    inst = pm.get_plugin_inst("kira_ops")
    check("plugin instance exists", lambda: _assert(inst is not None))

    # Hermetic: the plugin config file persists between runs, so pin the runtime
    # settings the assertions depend on instead of trusting whatever is on disk.
    cfg_dump = pm.get_plugin_config("kira_ops")
    cfg_dump["master"] = {"enabled": True, "panic_lock": False}
    cfg_dump["access"] = {"allow_sessions": [], "deny_sessions": [], "readonly_sessions": []}
    cfg_dump["risk"] = dict(cfg_dump.get("risk") or {},
                            level="standard", high_risk_sessions=[])
    inst.cfg = cfg_dump
    inst.refresh_settings(cfg_dump)
    inst.engine.apply_settings(cfg_dump)
    inst.backups.apply_settings(cfg_dump.get("backup") or {})
    inst.audit.apply_settings(cfg_dump.get("audit") or {})
    inst.store.apply_settings(cfg_dump.get("store") or {})
    inst.audit.audit_reads = True

    check("7 tools registered by the framework",
          lambda: _assert(len(pm.get_plugin_tools("kira_ops")) == 7,
                          sorted(pm.get_plugin_tools("kira_ops"))))
    check("12 capability domains", lambda: _assert(len(inst.caps) == 12, sorted(inst.caps)))
    # note: no assertion on *values* here - the config file persists between
    # runs, so only the presence of every schema section is guaranteed.
    check("plugin config filled from schema",
          lambda: _assert(
              {"master", "access", "risk", "control", "protected", "backup", "audit", "store"}
              <= set(cfg_dump.keys()), sorted(cfg_dump.keys())))

    ev = Ev()
    item = {"n": 0}

    # ---------------------------------------------------------------- every read
    reads = [
        ("plugin", "list", {}), ("plugin", "info", {"target": "kira_ops"}),
        ("plugin", "config_get", {"target": "kira_ops"}),
        ("skill", "list", {}), ("provider", "list", {}), ("mcp", "list", {}),
        ("session", "list", {}), ("persona", "list", {}),
        ("config", "get", {}), ("log", "tail", {"limit": 5}), ("log", "history", {}),
        ("store", "sources", {}), ("backup", "list", {}),
        ("agent", "get", {}), ("agent", "info", {}), ("control", "info", {}),
    ]
    for domain, action, args in reads:
        def probe(domain=domain, action=action, args=args):
            r = run(inst.ops_read(Ev(), domain, action, **args))
            _assert(r.get("ok"), json.dumps(r, ensure_ascii=False)[:200])
        check(f"read {domain}.{action}", probe)

    # ---------------------------------------------------------------- output format
    def output_format():
        """The bytes the model sees: compact JSON, not a Python dict repr."""
        from core.agent.tool import ToolResult
        tool = pm.ctx.tool_mgr.tool_set.get("ops_status")
        _assert(tool is not None, "ops_status is missing from the global tool set")
        raw = run(tool.execute(ev))
        _assert(isinstance(raw, dict), type(raw))
        text = run(ToolResult(str(raw)).assemble_result())
        text = text if isinstance(text, str) else str(text)
        _assert(text.startswith('{"ok":true'), text[:80])
        _assert("': " not in text, f"python repr leaked: {text[:80]}")
        _assert("True" not in text and "None" not in text, text[:120])
    check("tool results reach the model as compact JSON", output_format)

    # ---------------------------------------------------------------- S3 gate
    def s3():
        try:
            inst.master = {"enabled": False, "panic_lock": False}
            r = run(inst.ops_status(ev))
            _assert(not r.get("ok"), r)
        finally:
            inst.master = dict(inst.cfg.get("master") or {})
    check("S3 ops_status refuses while master.enabled=false", s3)

    def persona_active():
        r = run(inst.ops_read(ev, "persona", "get_active"))
        _assert(r.get("ok") or "no active persona" in (r.get("error") or ""), r)
    check("read persona.get_active", persona_active)

    # ---------------------------------------------------------------- S4 truncation
    def s4_log():
        import core.logging_manager as lm
        original = lm.log_cache_manager.get_cache
        lm.log_cache_manager.get_cache = lambda: [
            {"time": "t", "level": "INFO", "name": "big", "message": "X" * 4000}
        ] * 30
        try:
            r = run(inst.ops_read(ev, "log", "tail", limit=30))
            size = len(json.dumps(r, ensure_ascii=False))
            _assert(size < 15000, f"log.tail returned {size} chars")
            _assert("…(+" in r["items"][0]["message"], r["items"][0])
        finally:
            lm.log_cache_manager.get_cache = original
    check("S4 log.tail clips every message", s4_log)

    def s4_config():
        r = run(inst.ops_read(ev, "config", "get"))
        _assert(r.get("ok") and "sections" in r, r)
        size = len(json.dumps(r, ensure_ascii=False))
        _assert(size < 4000, f"config.get summary is {size} chars")
        _assert("value" not in r)
    check("S4 config.get without path returns a summary", s4_config)

    def s4_readfile():
        probe_file = get_data_path() / "kira_ops_test.log"
        probe_file.write_text("Z" * 50000, encoding="utf-8")
        try:
            r = run(inst.ops_read(ev, "log", "read_file",
                                  args={"path": "kira_ops_test.log", "limit": 10 ** 9}))
            _assert(r.get("ok"), r)
            _assert(r["limit"] <= 20000, r)
            _assert(len(r["tail"]) <= 20000, len(r["tail"]))
        finally:
            probe_file.unlink(missing_ok=True)
    check("S4 log.read_file clamps limit", s4_readfile)

    # ---------------------------------------------------------------- M3 brief/full
    def m3():
        brief = run(inst.ops_status(ev, include="audit,permission"))
        full = run(inst.ops_status(ev, include="audit,permission", detail="full"))
        _assert(brief.get("detail") == "brief", brief)
        _assert("hint" not in full and full.get("detail") == "full", full)
        trimmed = inst._brief({"plugins": {"errors": [f"e{i}" for i in range(20)]}})
        errs = trimmed["plugins"]["errors"]
        _assert(errs["count"] == 20 and len(errs["items"]) == 5 and errs["truncated"], errs)
        _assert("hint" in trimmed, trimmed)
        plain = inst._brief({"plugins": {"errors": ["only one"]}})
        _assert("hint" not in plain, plain)
        _assert(inst._trim([1, 2, 3]) == [1, 2, 3])
    check("M3 brief trims long lists, full does not", m3)

    # ---------------------------------------------------------------- M1 phantom session
    def m1():
        ghost = f"qq:gm:ghost-{uuid.uuid4().hex[:8]}"
        before = len(ctx.session_mgr.get_session_info() or [])
        r = run(inst.ops_read(ev, "session", "info", target=ghost))
        after = len(ctx.session_mgr.get_session_info() or [])
        _assert(not r.get("ok"), r)
        _assert(before == after, f"session count {before} -> {after}")
    check("M1 session.info never creates the session", m1)

    # ---------------------------------------------------------------- S5 agent truth
    def s5():
        has = pm.has_plugin("agent")
        r = run(inst.ops_read(ev, "agent", "get"))
        _assert(r.get("ok"), r)
        _assert(r["installed"] == has, r)
        _assert(r["plugin_enabled"] == (has and pm.is_plugin_enabled("agent")), r)
    check("S5 agent domain reports installed/enabled truthfully", s5)

    def s5_status():
        r = run(inst.ops_status(ev, include="plugins"))
        ready = pm.has_plugin("agent") and pm.is_plugin_enabled("agent")
        notes = " ".join(r.get("notes") or [])
        _assert((not ready) == ("agent" in notes), (ready, notes))
    check("S5 ops_status note matches the agent plugin state", s5_status)

    # ---------------------------------------------------------------- M7 models
    check("M7 provider.models refuses an unknown provider",
          lambda: _assert(not run(inst.ops_read(
              ev, "provider", "models", args={"provider_id": "nope"})).get("ok")))

    # ---------------------------------------------------------------- M5 limit
    def m5():
        r = run(inst.ops_read(ev, "plugin", "list", limit=1))
        _assert(r.get("ok") and r["count"] <= 1 and "total" in r, r)
    check("M5 list honours limit and reports total", m5)

    # ---------------------------------------------------------------- M2 extension point
    def m2():
        from plugins.kira_ops.caps import Capability, REGISTRY, register_capability

        class ProbeCap(Capability):
            name = "probe"
            ACTIONS = {"ping": ("read", False, "probe")}

            def handle_read(self, action, params):
                return {"ok": True, "pong": True}

        ok, detail = register_capability(ProbeCap)
        _assert(ok, detail)
        _assert(register_capability(ProbeCap)[0] is False, "duplicate accepted")
        _assert(register_capability(object)[0] is False, "non-class accepted")
        _assert(register_capability(type("Bad", (Capability,),
                                         {"name": "bad", "ACTIONS": {}}))[0] is False,
                "empty ACTIONS accepted")
        _assert(register_capability(type("Bad2", (Capability,),
                                         {"name": "BadName",
                                          "ACTIONS": {"x": ("read", False, "d")}}))[0] is False,
                "illegal name accepted")
        _assert(register_capability(type("Bad3", (Capability,),
                                         {"name": "bad3",
                                          "ACTIONS": {"x": ("nope", False, "d")}}))[0] is False,
                "illegal kind accepted")
        try:
            inst.caps["probe"] = ProbeCap(inst)
            r = run(inst.ops_read(ev, "probe", "ping"))
            _assert(r.get("ok") and r.get("pong"), r)
        finally:
            inst.caps.pop("probe", None)
            REGISTRY.pop("probe", None)
    check("M2 register_capability accepts valid classes and rejects junk", m2)

    def m2_event():
        """The event bus only enqueues; dispatch runs in the lifecycle consumer.
        So assert on the registered hook (name filter + live binding) and run it."""
        from core.plugin.plugin_handlers import EventType, event_handler_reg
        from plugins.kira_ops.caps import Capability, REGISTRY

        class EventCap(Capability):
            name = "eventcap"
            ACTIONS = {"ping": ("read", False, "probe")}

            def handle_read(self, action, params):
                return {"ok": True, "via": "event"}

        hooks = [h for h in event_handler_reg.get_handlers(EventType.ON_CUSTOM_EVENT)
                 if getattr(h.handler, "_custom_event_name", None) == "kira_ops.register_capability"]
        _assert(hooks, "no ON_CUSTOM_EVENT handler registered for kira_ops.register_capability")
        _assert(getattr(hooks[0].handler, "__self__", None) is inst, "handler is not bound to the plugin")
        try:
            run(hooks[0].exec_handler(KiraCustomEvent(
                event_name="kira_ops.register_capability",
                source_plugin="test_suite",
                payload={"class": EventCap})))
            _assert("eventcap" in REGISTRY, sorted(REGISTRY))
            _assert("eventcap" in inst.caps, sorted(inst.caps))
            r = run(inst.ops_read(ev, "eventcap", "ping"))
            _assert(r.get("ok") and r.get("via") == "event", r)
        finally:
            inst.caps.pop("eventcap", None)
            REGISTRY.pop("eventcap", None)
    check("M2 the register_capability hook is bound and works", m2_event)

    # ---------------------------------------------------------------- M4 audit
    def deny_count():
        return sum(1 for row in inst.audit.tail(20000) if row.get("kind") == "deny")

    def m4_read_deny():
        inst.audit.audit_reads = False
        try:
            settings = dict(inst.cfg)
            settings["access"] = {"allow_sessions": ["qq:gm:somewhere-else"],
                                  "deny_sessions": [], "readonly_sessions": []}
            settings["master"] = {"enabled": True, "panic_lock": False}
            inst.engine.apply_settings(settings)
            before = deny_count()
            r = run(inst.ops_read(ev, "plugin", "list"))
            _assert(not r.get("ok"), r)
            _assert(deny_count() == before + 1, "denied read was not audited")
            _assert(inst.audit.tail(1)[-1].get("kind") == "deny")
        finally:
            inst.engine.apply_settings(inst.cfg)
            inst.audit.audit_reads = True
    check("M4 a denied read is written to the audit trail", m4_read_deny)

    def m4_confirm_deny():
        before = deny_count()
        r = run(inst.ops_confirm(ev, "deadbeefdeadbeef"))
        _assert(not r.get("ok"), r)
        _assert(deny_count() == before + 1, "rejected token was not audited")
        _assert(inst.audit.tail(1)[-1].get("kind") == "deny")
    check("M4 a rejected confirm token is written to the audit trail", m4_confirm_deny)

    # ---------------------------------------------------------------- S1/S2 installer
    def installer():
        from plugins.kira_ops.store.installer import (
            install_plugin_from_zip_bytes, install_skill_from_zip_bytes)

        pid = "zip_harness_plugin"
        target = Path(pm.plugin_dir) / pid
        try:
            r1 = run(install_plugin_from_zip_bytes(pm, plugin_zip(pid, 1), pid))
            _assert(r1.get("ok"), r1)
            r2 = run(install_plugin_from_zip_bytes(pm, plugin_zip(pid, 2), pid, update=True))
            _assert(r2.get("ok"), r2)
            mod = sys.modules.get(f"plugins.{pid}.impl")
            value = getattr(mod, "VALUE", None)
            _assert(value == 2, (
                f"S1: submodule still shows the previous build (VALUE={value}); "
                "the update path is missing prepare_plugin_reload"))
        finally:
            try:
                run(pm.uninstall_plugin(pid))
            except Exception:
                pass
            shutil.rmtree(target, ignore_errors=True)

        bomb = BytesIO()
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("evil_plugin/manifest.json", json.dumps({
                "plugin_id": "evil_plugin", "display_name": "e", "version": "1",
                "author": "a", "description": "d"}))
            zf.writestr("evil_plugin/big.txt", "0" * (12 * 1024 * 1024))
        r3 = run(install_plugin_from_zip_bytes(pm, bomb.getvalue(), "evil_plugin"))
        _assert(not r3.get("ok"), r3)
        _assert(not (Path(pm.plugin_dir) / "evil_plugin").exists(), "bomb was installed")

        skill_zip = BytesIO()
        with zipfile.ZipFile(skill_zip, "w") as zf:
            zf.writestr("demo/SKILL.md", "---\nname: demo\ndescription: d\n---\nbody\n")
        skills_root = get_data_path() / "skills"
        try:
            installed = run(install_skill_from_zip_bytes(skills_root, skill_zip.getvalue(), "demo"))
            _assert(installed.is_dir() and (installed / "SKILL.md").is_file(), installed)
        finally:
            shutil.rmtree(skills_root / "demo", ignore_errors=True)
    check("S1/S2 installer reloads submodules and rejects hostile archives", installer)

    # ---------------------------------------------------------------- L2 mcp toggle
    def l2():
        inst.engine.apply_settings(inst.cfg)  # guarantee the write gate is open
        r = run(inst.ops_action(ev, "mcp", "add", args={
            "name": "harness", "description": "d",
            "config": {"type": "sse", "url": "https://example.com/sse"}}))
        _assert(r.get("ok"), r)
        sid = r["server_id"]
        try:
            bad = run(inst.ops_action(ev, "mcp", "tool_toggle",
                                      args={"server_id": sid, "tool": "nope", "enabled": False}))
            _assert(not bad.get("ok"), bad)
            _assert("cannot toggle" in (bad.get("error") or ""), bad)
        finally:
            run(inst.ops_action(ev, "mcp", "delete", args={"server_id": sid}))
    check("L2 mcp.tool_toggle fails cleanly on an unknown tool", l2)

    loop.close()
    print()
    print(f"passed: {len(PASSED)}  failed: {len(FAILED)}")
    if FAILED:
        for name, exc in FAILED:
            print(f"  FAILED: {name}: {exc}")
        sys.exit(1)
    print("ALL OK")


if __name__ == "__main__":
    main()
