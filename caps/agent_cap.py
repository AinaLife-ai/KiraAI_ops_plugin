"""Agent-policy domain: read/write the builtin agent plugin's access policy.

kira_ops does NOT execute files or commands itself. It only manages the
policy of the builtin 'agent' plugin, and every write still passes the
protected-data guards (paths and fields), so it cannot widen access to
protected locations.
"""

from __future__ import annotations

from . import Capability, fail, ok, register


@register
class AgentPolicyCap(Capability):
    name = "agent"
    ACTIONS = {
        "get": ("read", False, "读取 agent 插件的文件/命令访问策略（含是否已安装/启用）"),
        "info": ("read", False, "读取 agent 插件可用工具与安装/启用状态"),
        "set": ("write", False, "修改 agent 插件策略（文件/命令访问名单与路径；受字段与路径保护约束）"),
    }

    SECTIONS = ("file_access", "exec_access")

    def _pm(self):
        return getattr(self.ctx, "plugin_mgr", None)

    def _inst(self):
        pm = self._pm()
        if not pm:
            return None
        return pm.get_plugin_inst("agent")

    def _policy(self):
        pm = self._pm()
        if not pm:
            return None
        try:
            cfg = pm.get_plugin_config("agent") or {}
        except Exception:
            cfg = {}
        return cfg

    @staticmethod
    def _state(pm) -> tuple:
        """(installed, enabled) - is_plugin_enabled() defaults to True for
        unknown ids, so an uninstalled plugin would otherwise look enabled."""
        installed = bool(pm.has_plugin("agent"))
        return installed, bool(installed and pm.is_plugin_enabled("agent"))

    # ------------------------------------------------------------------

    def handle_read(self, action, params):
        pm = self._pm()
        if not pm:
            return fail("plugin manager is unavailable")
        if action == "get":
            inst = self._inst()
            cfg = self._policy()
            if cfg is None:
                return fail("plugin manager is unavailable")
            installed, enabled = self._state(pm)
            return ok(
                installed=installed,
                plugin_enabled=enabled,
                running=inst is not None,
                file_access=cfg.get("file_access") or {},
                exec_access=cfg.get("exec_access") or {},
            )
        if action == "info":
            cfg = self._policy() or {}
            tools = cfg.get("tools") or {}
            installed, enabled = self._state(pm)
            return ok(
                installed=installed,
                plugin_enabled=enabled,
                enabled_tools=list(tools.get("enabled_tools") or []),
                hint="file/command execution is delegated to the builtin agent plugin",
            )
        return fail(f"unknown read action '{action}'")

    # ------------------------------------------------------------------

    def backup_files(self, action, params):
        from core.utils.path_utils import get_config_path
        return [get_config_path() / "plugins" / "agent.json"]

    def backup_label(self, action, params):
        return "agent_policy"

    # ------------------------------------------------------------------

    async def handle_write(self, action, params):
        if action != "set":
            return fail(f"unknown write action '{action}'")
        pm = self._pm()
        if not pm:
            return fail("plugin manager is unavailable")
        if not pm.has_plugin("agent"):
            return fail("the builtin 'agent' plugin is not installed")

        patch = params.get("patch") or {}
        if not isinstance(patch, dict) or not patch:
            return fail("patch must be a non-empty JSON object")

        violations = self.plugin.validate_agent_policy_patch(patch)
        if violations:
            return fail("agent policy blocked: " + "; ".join(violations))

        current = pm.get_plugin_config("agent") or {}
        merged = self.plugin.deep_merge(current, patch)
        await pm.update_plugin_config("agent", merged)
        return ok(applied=sorted(str(k) for k in patch.keys()),
                  hint="agent policy updated and hot-reloaded")
