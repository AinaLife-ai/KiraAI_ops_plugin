"""Capability registry: one class per domain, hot-extensible.

Adding a new domain only requires a new module in this package plus a
``register`` call; the tool surface (ops_status / ops_read / ops_config /
ops_action / ops_store) never changes. Third-party plugins may also register
capabilities at runtime:

    await ctx.emit_custom_event("kira_ops.register_capability", payload={...})
"""

from __future__ import annotations

REGISTRY: dict = {}


class Capability:
    """Base class for kira_ops domains.

    Subclasses declare ``name`` and ``ACTIONS`` = {action: (kind, dangerous,
    desc)} with kind in {"read", "write"}.
    """

    name = ""
    ACTIONS: dict = {}

    def __init__(self, plugin):
        self.plugin = plugin

    @property
    def ctx(self):
        return self.plugin.ctx

    # ------------------------------------------------------------------
    # hooks
    # ------------------------------------------------------------------

    def handle_read(self, action, params):
        raise NotImplementedError(f"{self.name}.{action} read not implemented")

    async def handle_write(self, action, params):
        raise NotImplementedError(f"{self.name}.{action} write not implemented")

    def backup_files(self, action, params) -> list:
        """Files to snapshot before this write action (default: none)."""
        return []

    def backup_label(self, action, params) -> str:
        return f"{self.name}_{action}"

    def classify(self, action, params):
        """Optional override of the action key used for the risk gate."""
        return None

    def preflight(self, action, params):
        """Optional gate evaluated *before* a confirm token is issued.

        Return an error string to reject the request outright, or None/"" to
        let it continue to the normal (possibly token-gated) pipeline.
        """
        return None

    def describe(self) -> dict:
        return {
            "domain": self.name,
            "actions": [
                {"action": a, "kind": v[0], "dangerous": bool(v[1]), "desc": v[2]}
                for a, v in self.ACTIONS.items()
            ],
        }


def register(cls):
    REGISTRY[cls.name] = cls
    return cls


def get_cap(name):
    return REGISTRY.get(str(name or ""))


def load_all() -> dict:
    from . import (  # noqa: F401  (import registers classes)
        plugin_cap, skill_cap, provider_cap, mcp_cap, session_cap,
        persona_cap, config_cap, log_cap, store_cap, agent_cap,
        control_cap, backup_cap,
    )
    return REGISTRY


def ok(**kw) -> dict:
    data = {"ok": True}
    data.update(kw)
    return data


def fail(message, **kw) -> dict:
    data = {"ok": False, "error": str(message)}
    data.update(kw)
    return data
