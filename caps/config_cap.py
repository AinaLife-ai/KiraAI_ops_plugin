"""Config domain: read/write KiraAI system config with masking + protection."""

from __future__ import annotations

from . import Capability, fail, ok, register


@register
class ConfigCap(Capability):
    name = "config"
    ACTIONS = {
        "get": ("read", False, "读取系统配置（敏感字段打码；可指定子树）"),
        "set": ("write", False, "写入系统配置（黑名单拦截敏感字段；写前自动备份）"),
    }

    def _cfg(self):
        return self.plugin.kira_config

    # ------------------------------------------------------------------

    def handle_read(self, action, params):
        cfg = self._cfg()
        if not cfg:
            return fail("kira config is unavailable")
        if action == "get":
            path = str(params.get("path") or "").strip()
            if path:
                value = cfg.get_config(path)
                if value is None:
                    return fail(f"config path '{path}' not found")
                return ok(path=path, value=self.plugin.mask(value))
            return ok(config=self.plugin.mask(dict(cfg)))
        return fail(f"unknown read action '{action}'")

    # ------------------------------------------------------------------

    def backup_files(self, action, params):
        from core.utils.path_utils import get_config_path
        return [get_config_path() / "system_config.json"]

    def backup_label(self, action, params):
        return "config_set"

    # ------------------------------------------------------------------

    async def handle_write(self, action, params):
        cfg = self._cfg()
        if not cfg:
            return fail("kira config is unavailable")
        if action != "set":
            return fail(f"unknown write action '{action}'")

        path = str(params.get("path") or "").strip()
        patch = params.get("patch")
        if not path:
            return fail("path is required (e.g. bot_config.bot)")
        if not isinstance(patch, dict) or not patch:
            return fail("patch must be a non-empty JSON object")

        # resolve target subtree
        target = cfg
        for part in path.split("."):
            if isinstance(target, dict) and part in target:
                target = target[part]
            else:
                return fail(f"config path '{path}' not found")
        if not isinstance(target, dict):
            return fail(f"config path '{path}' is not an object and cannot be patched")

        # field protection: deny list + secret guard. The allow list only
        # kicks in for restricted domains (provider/model configs), not for
        # general system config edits.
        from ..core.redact import check_patch
        check = check_patch(patch, self.plugin.engine.protected, restrict=False)
        if not check["ok"]:
            return fail("; ".join(check["violations"]))
        if check["secrets"]:
            return fail("secret fields are write-protected: "
                        + ", ".join(f"{p}" for p, _k in check["secrets"]))

        merged = self.plugin.deep_merge(target, patch)
        # write back in place, then persist
        cursor = cfg
        parts = path.split(".")
        for part in parts[:-1]:
            cursor = cursor[part]
        cursor[parts[-1]] = merged
        cfg.save_config()
        return ok(path=path, applied=sorted(str(k) for k in patch.keys()))
