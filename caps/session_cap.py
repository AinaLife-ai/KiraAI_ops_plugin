"""Session domain: titles, capabilities, memory and deletion."""

from __future__ import annotations

import re

from . import Capability, fail, ok, paged, register

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
        "memory_clear": ("write", True, "清空会话记忆（不可逆）"),
        "delete":       ("write", True, "删除会话记录"),
    }

    def _sm(self):
        return getattr(self.ctx, "session_mgr", None)

    def _sid(self, params):
        return str(params.get("session_id") or params.get("sid") or params.get("target") or "").strip()

    def _known(self, sm, sid: str):
        """Existing session ids - get_session_info(sid) would otherwise create one.

        ``None`` means "could not enumerate" (never "unknown"), so the caller
        does not turn a framework hiccup into a hard rejection.
        """
        sessions, skipped = self.plugin.session_inventory()
        if not sessions and skipped:
            return None
        return {str(getattr(s, "sid", "") or s) for s in sessions}

    def _exists(self, sm, sid: str):
        """True/False/None - the raw store is checked first so that a malformed
        key (the thing delete is meant to repair) is still addressable."""
        raw = getattr(sm, "chat_memory", None)
        if isinstance(raw, dict):
            return sid in raw
        known = self._known(sm, sid)
        return None if known is None else sid in known

    @staticmethod
    def _bad_sid(sid: str) -> str:
        if not SID_RE.match(sid):
            return ("session_id must look like adapter:type:id "
                    "(e.g. qq:gm:123456) - refusing to write a malformed key")
        return ""

    # ------------------------------------------------------------------

    def handle_read(self, action, params):
        sm = self._sm()
        if not sm:
            return fail("session manager is unavailable")
        if action == "list":
            sessions, skipped = self.plugin.session_inventory()
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
            data = ok(**paged(items, params, default=50))
            if skipped:
                data["malformed_keys"] = skipped[:10]
                data["hint"] = ("these chat_memory keys are malformed and break the "
                                "framework session list; delete them with "
                                "ops_action(domain=session, action=delete, target=<key>)")
            return data
        if action == "info":
            sid = self._sid(params)
            if not sid:
                return fail("session_id is required")
            bad = self._bad_sid(sid)
            if bad:
                return fail(bad)
            known = self._known(sm, sid)
            if known is not None and sid not in known:
                return fail(f"session '{sid}' not found")
            try:
                s = sm.get_session_info(sid)
            except Exception as exc:
                return fail(f"session not found: {exc}")
            merged = self.ctx.get_session_capabilities(sid) or {}
            if params.get("full"):
                caps = merged
            else:
                caps = {str(k): bool((v or {}).get("enabled", True))
                        for k, v in merged.items() if isinstance(v, dict)}
            overrides = sm.get_effective_capabilities(sid, {}) or {}
            data = ok(session_id=sid,
                      title=getattr(s, "session_title", "") or "",
                      description=getattr(s, "session_description", "") or "",
                      capabilities=caps)
            if overrides:
                data["overrides"] = overrides
            return data
        if action == "memory_count":
            sid = self._sid(params)
            if not sid:
                return fail("session_id is required")
            bad = self._bad_sid(sid)
            if bad:
                return fail(bad)
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
        if action == "delete":
            # deleting is also the repair path for an already-broken key, so it
            # accepts any existing key rather than requiring a well-formed id.
            if not sid.strip():
                return fail("session_id is required")
        else:
            bad = self._bad_sid(sid)
            if bad:
                return fail(bad)
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
            exists = self._exists(sm, sid)
            if exists is False:
                return fail(f"session '{sid}' not found")
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
