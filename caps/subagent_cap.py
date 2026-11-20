"""Subagent inventory and preset administration through existing OPS tools.

Execution remains owned by KiraAI's built-in subagent tools.  This domain adds
only global inspection and preset lifecycle/scope management, using the public
SubAgentManager/SubAgentPresetStore contracts.  All ordinary writes intentionally
stay at the default standard level; no extra confirmation gate is introduced.
"""

from __future__ import annotations

from . import Capability, fail, ok, paged, register, to_int


def _clip(value, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + f"…(+{len(text) - limit} chars)"


@register
class SubagentCap(Capability):
    name = "subagent"
    ACTIONS = {
        "list":             ("read", False, "列出全部 subagent 会话（摘要，不含大段结果）"),
        "info":             ("read", False, "查看 subagent 状态与结果（结果有长度上限）"),
        "preset_list":      ("read", False, "列出 subagent 预设摘要"),
        "preset_info":      ("read", False, "读取单个 subagent 预设完整配置"),
        "preset_save":      ("write", False, "新建或更新 subagent 预设（standard，无需确认）"),
        "preset_set_scope": ("write", False, "设置/清除预设的会话范围（standard，无需确认）"),
        "preset_delete":    ("write", False, "删除 subagent 预设（standard，无需确认）"),
    }

    def _mgr(self):
        return getattr(self.ctx, "subagent_mgr", None)

    def _store(self):
        return getattr(self._mgr(), "presets", None)

    @staticmethod
    def _identity(params) -> str:
        return str(params.get("preset_id") or params.get("agent_id")
                   or params.get("target") or "").strip()

    def _preset_summary(self, item: dict, scope=None) -> dict:
        data = {
            "id": item.get("id"),
            "name": item.get("name") or "",
            "description": _clip(item.get("description"), 240),
            "enabled": bool(item.get("enabled", True)),
            "model": item.get("model"),
            "instruction_source": item.get("instruction_source") or "persona",
            "tool_mode": item.get("tool_mode") or "inherit",
            "max_steps": item.get("max_steps", -1),
            "tools": len(item.get("tools") or []),
            "skills": len(item.get("skills") or []),
        }
        if scope:
            data["scope"] = scope
        return data

    async def handle_read(self, action, params):
        params = params or {}
        mgr = self._mgr()
        if mgr is None:
            return fail("subagent manager is unavailable on this KiraAI version")
        if action == "list":
            try:
                agents = await mgr.list_agents(caller_id=None)
            except Exception as exc:
                return fail(f"failed to list subagents: {exc}")
            items = []
            for agent in agents or []:
                items.append({
                    "id": agent.get("id"), "sid": agent.get("sid"),
                    "title": agent.get("title") or "", "kind": agent.get("kind") or "subagent",
                    "parent_id": agent.get("parent_id"), "status": agent.get("status") or "done",
                    "step_index": agent.get("step_index") or 0, "turn": agent.get("turn") or 0,
                    "error": _clip(agent.get("error"), 300),
                })
            items.sort(key=lambda x: (str(x.get("status") or ""), str(x.get("title") or "")))
            return ok(**paged(items, params, default=50))
        if action == "info":
            identity = self._identity(params)
            if not identity:
                return fail("agent_id or preset_id target is required")
            try:
                data = await mgr.get(identity, caller_id=None)
            except Exception as exc:
                return fail(f"subagent '{identity}' not found or unavailable: {exc}")
            limit = to_int(params.get("limit"), 2000, 100, 12000)
            result = str(data.get("result") or "")
            data["result"] = result[:limit]
            if len(result) > limit:
                data["result_truncated"] = True
                data["result_size"] = len(result)
            data["error"] = _clip(data.get("error"), 500)
            return ok(**data)
        if action in ("preset_list", "preset_info"):
            store = self._store()
            if store is None:
                return fail("subagent preset store is unavailable")
            scopes = store.get_scopes()
            if action == "preset_list":
                items = [self._preset_summary(item, scopes.get(item.get("id")))
                         for item in store.list()]
                items.sort(key=lambda x: str(x.get("name") or "").lower())
                return ok(**paged(items, params, default=50))
            identity = self._identity(params)
            if not identity:
                return fail("preset_id is required")
            try:
                preset = store.get(identity).model_dump()
            except (KeyError, AttributeError) as exc:
                return fail(f"subagent preset '{identity}' not found: {exc}")
            limit = to_int(params.get("limit"), 4000, 100, 20000)
            instruction = str(preset.get("instruction") or "")
            preset["instruction"] = instruction[:limit]
            data = ok(id=identity, **self.plugin.mask(preset), scope=scopes.get(identity) or {})
            if len(instruction) > limit:
                data["instruction_truncated"] = True
                data["instruction_size"] = len(instruction)
            return data
        return fail(f"unknown read action '{action}'")

    def backup_files(self, action, params):
        from core.utils.path_utils import get_config_path
        return [get_config_path() / "subagent_presets.json"]

    def backup_label(self, action, params):
        return f"subagent_{action}_{self._identity(params) or 'new'}"

    async def handle_write(self, action, params):
        params = params or {}
        store = self._store()
        if store is None:
            return fail("subagent preset store is unavailable on this KiraAI version")
        identity = self._identity(params) or None
        try:
            if action == "preset_save":
                payload = (params.get("preset") or params.get("config")
                           or params.get("patch"))
                if not isinstance(payload, dict) or not payload:
                    return fail("preset/config/patch must be a non-empty JSON object")
                payload = dict(payload)
                # Responses include these management fields; they are not preset schema fields.
                for key in ("id", "preset_id", "scope"):
                    payload.pop(key, None)
                if identity:
                    current = store.get(identity).model_dump()
                    payload = self.plugin.deep_merge(current, payload)
                from core.agent.subagent_presets import SubAgentPreset
                preset = SubAgentPreset.model_validate(payload)
                saved = await store.save(preset, identity)
                return ok(preset_id=saved["id"], created=identity is None,
                          preset=self._preset_summary(saved))
            if action == "preset_set_scope":
                if not identity:
                    return fail("preset_id is required")
                store.get(identity)  # existence check
                mode = params.get("mode")
                sessions = params.get("sessions") or []
                if mode not in (None, "", "allow", "deny"):
                    return fail("mode must be 'allow', 'deny' or empty (clear)")
                if mode and not isinstance(sessions, list):
                    return fail("sessions must be a list")
                if any(not isinstance(sid, str) or not sid.strip() for sid in sessions):
                    return fail("scope session IDs must be non-empty strings")
                scopes = store.get_scopes()
                if mode and sessions:
                    scopes[identity] = {mode: list(dict.fromkeys(sid.strip() for sid in sessions))}
                else:
                    scopes.pop(identity, None)
                await store.replace_scopes(scopes)
                return ok(preset_id=identity, scope=store.get_scopes().get(identity) or {})
            if action == "preset_delete":
                if not identity:
                    return fail("preset_id is required")
                await store.delete(identity)
                return ok(preset_id=identity, deleted=True)
        except KeyError:
            return fail(f"subagent preset '{identity}' not found")
        except Exception as exc:
            return fail(f"subagent preset operation failed: {exc}")
        return fail(f"unknown write action '{action}'")
