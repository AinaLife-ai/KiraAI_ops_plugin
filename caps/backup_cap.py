"""Backup domain: list snapshots and restore them."""

from __future__ import annotations

from . import Capability, fail, ok, register


@register
class BackupCap(Capability):
    name = "backup"
    ACTIONS = {
        "list":    ("read", False, "列出自动快照（含回滚点 ID）"),
        "restore": ("write", True, "把快照恢复到原位置（被外部改过需要 force）"),
    }

    def _bm(self):
        return getattr(self.plugin, "backups", None)

    def handle_read(self, action, params):
        bm = self._bm()
        if not bm:
            return fail("backup manager is unavailable")
        if action == "list":
            return ok(count=len(bm.list(200)), items=bm.list(100))
        return fail(f"unknown read action '{action}'")

    def backup_label(self, action, params):
        return "backup_restore"

    async def handle_write(self, action, params):
        bm = self._bm()
        if not bm:
            return fail("backup manager is unavailable")
        if action == "restore":
            bid = str(params.get("backup_id") or params.get("id") or params.get("target") or "").strip()
            if not bid:
                return fail("backup id is required")
            result = bm.restore(bid, force=bool(params.get("force")))
            if result.get("need_force"):
                return fail("target files changed after the snapshot; pass force=true to overwrite",
                            conflicts=result.get("conflicts"))
            return ok(**result)
        return fail(f"unknown write action '{action}'")
