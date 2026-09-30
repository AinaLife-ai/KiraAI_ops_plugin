"""Persona domain: list / read / switch / create / update / delete."""

from __future__ import annotations

from . import Capability, apply_limit, fail, ok, register


@register
class PersonaCap(Capability):
    name = "persona"
    ACTIONS = {
        "list":       ("read", False, "列出全部人设与当前激活项"),
        "info":       ("read", False, "读取人设详情（含正文）"),
        "get_active": ("read", False, "读取当前激活人设"),
        "set_active": ("write", True, "切换激活人设"),
        "create":     ("write", True, "新建人设"),
        "update":     ("write", True, "修改人设（受 protected.persona_write 约束）"),
        "delete":     ("write", True, "删除人设"),
    }

    def _pm(self):
        return getattr(self.ctx, "persona_mgr", None)

    # ------------------------------------------------------------------

    async def handle_read(self, action, params):
        pm = self._pm()
        if not pm:
            return fail("persona manager is unavailable")
        if action == "list":
            try:
                personas = await pm.list_personas()
            except Exception as exc:
                return fail(f"failed to list personas: {exc}")
            items = [{
                "id": p.id,
                "name": p.name or "",
                "format": p.format or "",
                "active": bool(p.is_active),
                "length": len(p.content or ""),
            } for p in personas]
            total = len(items)
            items, truncated = apply_limit(items, params, default=50)
            return ok(count=len(items), total=total, truncated=truncated, items=items)
        if action == "get_active":
            try:
                p = await pm.get_active_persona()
            except Exception as exc:
                return fail(f"failed to read active persona: {exc}")
            if not p:
                return fail("no active persona")
            return ok(id=p.id, name=p.name or "", format=p.format or "",
                      content=(p.content or "")[:int(params.get("limit") or 4000)])
        if action == "info":
            pid = str(params.get("persona_id") or "").strip() or None
            try:
                p = await pm.get_persona(pid)
            except Exception as exc:
                return fail(f"failed to read persona: {exc}")
            if not p:
                return fail(f"persona '{pid or '(active)'}' not found")
            return ok(id=p.id, name=p.name or "", format=p.format or "",
                      active=bool(p.is_active),
                      content=(p.content or "")[:int(params.get("limit") or 4000)])
        return fail(f"unknown read action '{action}'")

    # ------------------------------------------------------------------

    def backup_files(self, action, params):
        return []

    def backup_label(self, action, params):
        return f"persona_{action}"

    # ------------------------------------------------------------------

    def preflight(self, action, params):
        if action in ("create", "update", "delete") and not self.plugin.engine.persona_write_allowed:
            return "persona writes are disabled (protected.persona_write=false)"
        return None

    async def handle_write(self, action, params):
        pm = self._pm()
        if not pm:
            return fail("persona manager is unavailable")
        if action in ("create", "update", "delete") and not self.plugin.engine.persona_write_allowed:
            return fail("persona writes are disabled (protected.persona_write=false)")

        from core.persona.model import PersonaInfo
        if action == "set_active":
            pid = str(params.get("persona_id") or "").strip()
            if not pid:
                return fail("persona_id is required")
            changed = await pm.set_active_persona(pid)
            return ok(persona_id=pid, active=bool(changed))
        if action == "create":
            pid = str(params.get("persona_id") or "").strip()
            if not pid:
                return fail("persona_id is required")
            content = str(params.get("content") or "")
            if not content:
                return fail("content is required")
            info = PersonaInfo(id=pid, name=str(params.get("name") or pid),
                               format=str(params.get("format") or "text"), content=content)
            created = await pm.create_persona(info)
            return ok(persona_id=pid, created=bool(created))
        if action == "update":
            pid = str(params.get("persona_id") or "").strip()
            if not pid:
                return fail("persona_id is required")
            info = PersonaInfo(
                id=pid,
                name=params.get("name"),
                format=params.get("format"),
                content=params.get("content"),
                is_active=params.get("is_active"),
            )
            updated = await pm.update_persona(info)
            return ok(persona_id=pid, updated=bool(updated))
        if action == "delete":
            pid = str(params.get("persona_id") or "").strip()
            if not pid:
                return fail("persona_id is required")
            deleted = await pm.delete_persona(pid)
            return ok(persona_id=pid, deleted=bool(deleted))
        return fail(f"unknown write action '{action}'")
