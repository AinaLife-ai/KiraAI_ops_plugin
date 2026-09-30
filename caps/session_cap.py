"""Session domain: titles, capabilities, memory and deletion."""

from __future__ import annotations

from . import Capability, apply_limit, fail, ok, register


@register
class SessionCap(Capability):
    name = "session"
    ACTIONS = {
        "list":         ("read", False, "列出全部会话（标题/类型/记忆条数）"),
        "info":         ("read", False, "会话详情（含能力覆盖）"),
        "memory_count": ("read", False, "会话记忆条数"),
        "title":        ("write", False, "修改会话标题/描述"),
        "caps":         ("write", False, "设置会话级能力覆盖"),
        "memory_clear": ("write", True, "清空会话记忆（不可逆）"),
        "delete":       ("write", True, "删除会话记录"),
    }

    def _sm(self):
        return getattr(self.ctx, "session_mgr", None)

    def _sid(self, params):
        return str(params.get("session_id") or params.get("sid") or params.get("target") or "").strip()

    def _known(self, sm, sid: str):
        """session ids that already exist - get_session_info(sid) would create one."""
        try:
            sessions = sm.get_session_info() or []
        except Exception:
            return None
        return {str(getattr(s, "sid", "") or s) for s in sessions}

    # ------------------------------------------------------------------

    def handle_read(self, action, params):
        sm = self._sm()
        if not sm:
            return fail("session manager is unavailable")
        if action == "list":
            try:
                sessions = sm.get_session_info() or []
            except Exception as exc:
                return fail(f"failed to list sessions: {exc}")
            items = []
            keyword = str(params.get("keyword") or "").lower()
            for s in sessions:
                sid = getattr(s, "sid", None) or str(s)
                title = getattr(s, "session_title", "") or ""
                if keyword and keyword not in f"{sid} {title}".lower():
                    continue
                try:
                    count = sm.get_memory_count(sid)
                except Exception:
                    count = -1
                items.append({
                    "session_id": sid,
                    "title": title,
                    "type": getattr(s, "session_type", ""),
                    "memory_count": count,
                })
            items.sort(key=lambda x: x["session_id"])
            total = len(items)
            items, truncated = apply_limit(items, params)
            return ok(count=len(items), total=total, truncated=truncated, items=items)
        if action == "info":
            sid = self._sid(params)
            if not sid:
                return fail("session_id is required")
            known = self._known(sm, sid)
            if known is not None and sid not in known:
                return fail(f"session '{sid}' not found")
            try:
                s = sm.get_session_info(sid)
            except Exception as exc:
                return fail(f"session not found: {exc}")
            return ok(session_id=sid,
                      title=getattr(s, "session_title", "") or "",
                      description=getattr(s, "session_description", "") or "",
                      capabilities=self.ctx.get_session_capabilities(sid) or {})
        if action == "memory_count":
            sid = self._sid(params)
            if not sid:
                return fail("session_id is required")
            known = self._known(sm, sid)
            if known is not None and sid not in known:
                return fail(f"session '{sid}' not found")
            return ok(session_id=sid, memory_count=sm.get_memory_count(sid))
        return fail(f"unknown read action '{action}'")

    # ------------------------------------------------------------------

    def handle_write(self, action, params):
        sm = self._sm()
        if not sm:
            return fail("session manager is unavailable")
        sid = self._sid(params)
        if not sid:
            return fail("session_id is required")
        if action == "title":
            sm.update_session_info(sid, title=params.get("title"), description=params.get("description"))
            return ok(session_id=sid)
        if action == "caps":
            caps = params.get("capabilities")
            if caps is not None and not isinstance(caps, dict):
                return fail("capabilities must be a JSON object or null (null clears overrides)")
            sm.update_session_capabilities(sid, caps)
            return ok(session_id=sid, capabilities=sm.get_effective_capabilities(sid, {}) if caps else {})
        if action == "memory_clear":
            before = sm.get_memory_count(sid)
            sm.write_memory(sid, [])
            return ok(session_id=sid, cleared_count=before)
        if action == "delete":
            sm.delete_session(sid)
            try:
                skills = getattr(getattr(self.ctx, "message_processor", None), "skills_manager", None)
                if skills:
                    skills.remove_session_from_scopes(sid)
            except Exception:
                pass
            try:
                mcp = getattr(getattr(self.ctx, "message_processor", None), "mcp_manager", None)
                if mcp:
                    mcp.remove_session_from_scopes(sid)
            except Exception:
                pass
            return ok(session_id=sid, deleted=True)
        return fail(f"unknown write action '{action}'")
