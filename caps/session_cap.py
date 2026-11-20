"""Session domain: titles, capabilities, memory and deletion."""

from __future__ import annotations

import re

from . import Capability, fail, ok, paged, register

# ── 双世代会话助手（特征识别，不绑版本号）─────────────────────────────────
# 2.x：同步 API（get_session_info/write_memory/get_memory_count/delete_session）；
# 3.0 rc.1+：全异步（get_session/update_session/delete_session），SQLite 持久化，
# update_session(content=[]) 只清对话、保留标题/能力设置/data。

def _type_of(sid: str) -> str:
    parts = str(sid or "").split(":")
    return parts[1] if len(parts) >= 2 else ""


async def _sm_get(sm, sid: str):
    """取单个会话（不创建），两代统一返回 Session 或 None。"""
    if hasattr(sm, "get_session_info"):
        try:
            return sm.get_session_info(sid)
        except Exception:
            return None
    res = sm.get_session(sid, ensure_session=False)
    if hasattr(res, "__await__"):
        res = await res
    return res


async def _sm_count(sm, sid: str) -> int:
    if hasattr(sm, "get_memory_count"):
        return sm.get_memory_count(sid)
    s = await _sm_get(sm, sid)
    return len(getattr(s, "content", None) or []) if s else 0


async def _sm_counts(sm, sid: str = None):
    """Use 3.x's content-free batch counter when available; None = fallback."""
    if hasattr(sm, "get_memory_count"):
        return None
    getter = getattr(sm, "get_session_message_counts", None)
    if not callable(getter):
        return None
    try:
        res = getter(sid) if sid is not None else getter()
        if hasattr(res, "__await__"):
            res = await res
        return res if isinstance(res, dict) else None
    except Exception:
        return None


async def _resolve_sid(sm, target: str) -> str:
    """Accept a 3.x internal session ID as well as a full routing SID."""
    target = str(target or "").strip()
    if ":" in target or not hasattr(sm, "resolve_session"):
        return target
    try:
        res = sm.resolve_session(target)
        if hasattr(res, "__await__"):
            res = await res
        if res is not None and getattr(res, "internal_id", None) == target:
            return str(getattr(res, "sid", "") or target)
    except Exception:
        pass
    return target


async def _sm_update_info(sm, sid: str, title=None, description=None):
    if hasattr(sm, "update_session_info"):
        sm.update_session_info(sid, title=title, description=description)
        return
    res = sm.update_session(sid, title=title, description=description)
    if hasattr(res, "__await__"):
        await res


async def _sm_clear(sm, sid: str):
    """清空对话内容（保留标题/能力设置等元数据）。"""
    if hasattr(sm, "write_memory"):
        sm.write_memory(sid, [])
        return
    res = sm.update_session(sid, content=[])
    if hasattr(res, "__await__"):
        await res


async def _sm_caps(ctx, sid: str) -> dict:
    res = ctx.get_session_capabilities(sid)
    if hasattr(res, "__await__"):
        res = await res
    return res or {}


async def _sm_caps_update(sm, sid: str, caps):
    res = sm.update_session_capabilities(sid, caps)
    if hasattr(res, "__await__"):
        await res


async def _sm_effective(sm, sid: str) -> dict:
    res = sm.get_effective_capabilities(sid, {})
    if hasattr(res, "__await__"):
        res = await res
    return res or {}


# adapter:type:id - the framework itself splits on ':' and indexes part 2, so a
# key that does not match this shape breaks SessionManager.get_session_info()
# for the whole process (and therefore the builtin session tools too).
SID_RE = re.compile(r"^[^:\s]+:[^:\s]+:[^\s]+$")


@register
class SessionCap(Capability):
    name = "session"
    ACTIONS = {
        "list":         ("read", False, "列出全部会话（标题/类型/记忆条数）"),
        "info":         ("read", False, "会话详情（含能力覆盖）"),
        "memory_count": ("read", False, "会话记忆条数"),
        "title":        ("write", False, "修改会话标题/描述"),
        "caps":         ("write", False, "设置会话级能力覆盖"),
        "llm_model":    ("write", False, "设置/清除 IM 会话级 LLM 模型（3.2+；null 清除）"),
        "memory_clear": ("write", True, "清空会话记忆（不可逆）"),
        "delete":       ("write", True, "删除会话记录"),
    }

    def _sm(self):
        return getattr(self.ctx, "session_mgr", None)

    def _sid(self, params):
        return str(params.get("session_id") or params.get("sid") or params.get("target") or "").strip()

    async def _known(self, sm, sid: str):
        """Existing session ids（双世代枚举）。

        ``None`` means "could not enumerate" (never "unknown"), so the caller
        does not turn a framework hiccup into a hard rejection.
        """
        sessions, skipped = await self.plugin.session_inventory()
        if not sessions and skipped:
            return None
        return {str(getattr(s, "sid", "") or s) for s in sessions}

    async def _exists(self, sm, sid: str):
        """True/False/None。2.x 先查 raw chat_memory（畸形键也可寻址）；
        3.0 起 DB 约束已挡畸形键，直接 get_session(ensure_session=False) 探测。"""
        raw = getattr(sm, "chat_memory", None)
        if isinstance(raw, dict):
            return sid in raw
        if hasattr(sm, "get_session"):
            try:
                res = sm.get_session(sid, ensure_session=False)
                if hasattr(res, "__await__"):
                    res = await res
                return res is not None
            except Exception:
                return None
        known = await self._known(sm, sid)
        return None if known is None else sid in known

    @staticmethod
    def _bad_sid(sid: str) -> str:
        if not SID_RE.match(sid):
            return ("session_id must look like adapter:type:id "
                    "(e.g. qq:gm:123456) - refusing to write a malformed key")
        return ""

    # ------------------------------------------------------------------

    async def handle_read(self, action, params):
        sm = self._sm()
        if not sm:
            return fail("session manager is unavailable")
        if action == "list":
            requested_kind = str(params.get("kind") or "").strip().lower()
            sessions, skipped = await self.plugin.session_inventory(kind=requested_kind or None)
            counts = await _sm_counts(sm)
            items = []
            keyword = str(params.get("keyword") or "").lower()
            for s in sessions:
                sid = getattr(s, "sid", None) or str(s)
                title = getattr(s, "session_title", "") or ""
                if keyword and keyword not in f"{sid} {title}".lower():
                    continue
                try:
                    count = (counts.get(sid, 0) if isinstance(counts, dict)
                             else await _sm_count(sm, sid))
                except Exception:
                    count = -1
                session_data = getattr(s, "data", None)
                kind = getattr(s, "kind", "im") or "im"
                entry = {
                    "session_id": sid,
                    "title": title,
                    "type": getattr(s, "session_type", "") or _type_of(sid),
                    "kind": kind,
                    "memory_count": count,
                }
                for source_key, output_key in (("internal_id", "internal_id"),
                                               ("parent_id", "parent_id")):
                    value = getattr(s, source_key, None)
                    if value:
                        entry[output_key] = value
                if isinstance(session_data, dict) and session_data.get("llm_model"):
                    entry["llm_model"] = session_data["llm_model"]
                if kind == "subagent" and isinstance(session_data, dict):
                    state = session_data.get("subagent") or {}
                    if isinstance(state, dict) and state.get("status"):
                        entry["subagent_status"] = state["status"]
                items.append(entry)
            items.sort(key=lambda x: (x["kind"], x["session_id"]))
            data = ok(**paged(items, params, default=50))
            if skipped:
                data["malformed_keys"] = skipped[:10]
                data["hint"] = ("these chat_memory keys are malformed and break the "
                                "framework session list; delete them with "
                                "ops_action(domain=session, action=delete, target=<key>)")
            return data
        if action == "info":
            sid = await _resolve_sid(sm, self._sid(params))
            if not sid:
                return fail("session_id is required")
            bad = self._bad_sid(sid)
            if bad:
                return fail(bad)
            known = await self._known(sm, sid)
            if known is not None and sid not in known:
                return fail(f"session '{sid}' not found")
            s = await _sm_get(sm, sid)
            if s is None:
                return fail(f"session '{sid}' not found")
            merged = await _sm_caps(self.ctx, sid)
            if params.get("full"):
                caps = merged
            else:
                caps = {str(k): bool((v or {}).get("enabled", True))
                        for k, v in merged.items() if isinstance(v, dict)}
            overrides = await _sm_effective(sm, sid)
            session_data = getattr(s, "data", None)
            kind = getattr(s, "kind", "im") or "im"
            data = ok(session_id=sid,
                      title=getattr(s, "session_title", "") or "",
                      description=getattr(s, "session_description", "") or "",
                      kind=kind,
                      capabilities=caps)
            for key in ("internal_id", "parent_id", "created_at", "updated_at"):
                value = getattr(s, key, None)
                if value is not None:
                    data[key] = value
            if isinstance(session_data, dict):
                data["llm_model"] = session_data.get("llm_model")
                if kind == "subagent":
                    state = session_data.get("subagent") or {}
                    if isinstance(state, dict):
                        data["subagent"] = {
                            k: state.get(k) for k in
                            ("status", "preset_id", "scope_sid", "turn", "step_index")
                            if state.get(k) is not None
                        }
            counts = await _sm_counts(sm, sid)
            data["memory_count"] = (counts.get(sid, 0) if isinstance(counts, dict)
                                      else await _sm_count(sm, sid))
            if overrides:
                data["overrides"] = overrides
            return data
        if action == "memory_count":
            sid = await _resolve_sid(sm, self._sid(params))
            if not sid:
                return fail("session_id is required")
            bad = self._bad_sid(sid)
            if bad:
                return fail(bad)
            known = await self._known(sm, sid)
            if known is not None and sid not in known:
                return fail(f"session '{sid}' not found")
            counts = await _sm_counts(sm, sid)
            count = (counts.get(sid, 0) if isinstance(counts, dict)
                     else await _sm_count(sm, sid))
            return ok(session_id=sid, memory_count=count)
        return fail(f"unknown read action '{action}'")


    async def handle_write(self, action, params):
        sm = self._sm()
        if not sm:
            return fail("session manager is unavailable")
        sid = await _resolve_sid(sm, self._sid(params))
        if not sid:
            return fail("session_id is required")
        if action == "delete":
            # deleting is also the repair path for an already-broken key, so it
            # accepts any existing key rather than requiring a well-formed id.
            if not sid.strip():
                return fail("session_id is required")
        else:
            bad = self._bad_sid(sid)
            if bad:
                return fail(bad)
        if action in ("title", "caps", "llm_model", "memory_clear"):
            exists = await self._exists(sm, sid)
            if exists is False:
                return fail(f"session '{sid}' not found")
            if exists is None:
                return fail(f"cannot verify that session '{sid}' exists; write refused")
        if action == "title":
            await _sm_update_info(sm, sid, title=params.get("title"),
                                  description=params.get("description"))
            return ok(session_id=sid)
        if action == "caps":
            caps = params.get("capabilities")
            if caps is not None and not isinstance(caps, dict):
                return fail("capabilities must be a JSON object or null (null clears overrides)")
            await _sm_caps_update(sm, sid, caps)
            return ok(session_id=sid,
                      capabilities=(await _sm_effective(sm, sid)) if caps else {})
        if action == "llm_model":
            import inspect
            update = getattr(sm, "update_session", None)
            if not callable(update) or "llm_model" not in inspect.signature(update).parameters:
                return fail("per-session LLM selection requires a KiraAI version with llm_model support")
            current = await _sm_get(sm, sid)
            if current is not None and (getattr(current, "kind", "im") or "im") == "subagent":
                return fail("subagent models are managed by their preset; direct session overrides are not supported")
            if "llm_model" in params:
                reference = params.get("llm_model")
            elif "model" in params:
                reference = params.get("model")
            else:
                return fail("llm_model is required (provider_id:model_id, or null to clear)")
            if reference is not None:
                if not isinstance(reference, str):
                    return fail("llm_model must be a string or null")
                provider_id, separator, model_id = reference.partition(":")
                if not separator or not provider_id or not model_id:
                    return fail("invalid LLM model reference; expected provider_id:model_id")
                provider_mgr = getattr(self.ctx, "provider_mgr", None)
                if provider_mgr is None:
                    return fail("provider manager is unavailable")
                try:
                    from core.provider import ModelType
                    model = provider_mgr.get_model_info(provider_id, model_id, ModelType.LLM)
                except Exception as exc:
                    return fail(f"cannot validate LLM model: {exc}")
                if model is None:
                    return fail(f"LLM model not found: {reference}")
                reference = f"{model.provider_id}:{model.model_id}"
            try:
                res = update(sid, llm_model=reference)
                if hasattr(res, "__await__"):
                    await res
            except Exception as exc:
                return fail(f"failed to set session LLM model: {exc}")
            return ok(session_id=sid, llm_model=reference)
        if action == "memory_clear":
            # 清空只清对话内容，保留会话标题/能力设置（两代同语义）
            before = await _sm_count(sm, sid)
            await _sm_clear(sm, sid)
            return ok(session_id=sid, cleared_count=before)
        if action == "delete":
            exists = await self._exists(sm, sid)
            if exists is False:
                return fail(f"session '{sid}' not found")
            res = sm.delete_session(sid)
            if hasattr(res, "__await__"):
                await res
            # 与官方删除路径一致，清理技能/MCP/subagent 预设范围。
            cleanup_errors = []
            for modern_attr, legacy_attr in (("skills_mgr", "skills_manager"),
                                             ("mcp_mgr", "mcp_manager")):
                try:
                    mgr = self.plugin._manager(modern_attr, legacy_attr)
                    if mgr is not None and hasattr(mgr, "remove_session_from_scopes"):
                        res2 = mgr.remove_session_from_scopes(sid)
                        if hasattr(res2, "__await__"):
                            await res2
                except Exception as exc:
                    cleanup_errors.append(f"{modern_attr}: {exc}")
            try:
                subagent_mgr = getattr(self.ctx, "subagent_mgr", None)
                presets = getattr(subagent_mgr, "presets", None)
                if presets is not None and hasattr(presets, "remove_session_from_scopes"):
                    res2 = presets.remove_session_from_scopes(sid)
                    if hasattr(res2, "__await__"):
                        await res2
            except Exception as exc:
                cleanup_errors.append(f"subagent_presets: {exc}")
            data = ok(session_id=sid, deleted=True)
            if cleanup_errors:
                data["scope_cleanup_errors"] = cleanup_errors
            return data
        return fail(f"unknown write action '{action}'")

