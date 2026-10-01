"""Capability registry: one class per domain, hot-extensible.

Adding a new domain only requires a new module in this package plus a
``register`` call; the tool surface (ops_status / ops_read / ops_config /
ops_action / ops_store) never changes.

Third-party plugins may contribute a domain at runtime without touching this
package (handled by ``main.KiraOpsPlugin.register_capability``):

    from core.chat.message_elements import ...            # the plugin's own code
    await ctx.emit_custom_event(
        "kira_ops.register_capability", {"class": MyCapability})

The payload class must subclass ``Capability`` and declare ``name`` plus a
non-empty ``ACTIONS`` mapping of ``{action: (kind, dangerous, description)}``
with ``kind`` in ``{"read", "write"}``.
"""

from __future__ import annotations

import re

REGISTRY: dict = {}

CAP_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


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


def register_capability(cls) -> tuple:
    """Validate and register a capability class contributed by another plugin.

    Returns ``(ok, name_or_reason)``. Everything is checked before the class
    enters the registry, so a malformed payload cannot break the tool surface.
    """
    if not isinstance(cls, type) or not issubclass(cls, Capability):
        return False, "payload must carry a Capability subclass under 'class'"
    name = str(getattr(cls, "name", "") or "").strip()
    if not name or not CAP_NAME_RE.match(name):
        return False, f"illegal capability name {name!r}"
    if name in REGISTRY:
        return False, f"capability '{name}' is already registered"
    actions = getattr(cls, "ACTIONS", None)
    if not isinstance(actions, dict) or not actions:
        return False, "ACTIONS must be a non-empty mapping"
    for action, spec in actions.items():
        if not isinstance(action, str) or not action:
            return False, "ACTIONS keys must be non-empty strings"
        if not isinstance(spec, (tuple, list)) or len(spec) < 3:
            return False, f"ACTIONS['{action}'] must be (kind, dangerous, description)"
        if str(spec[0]) not in ("read", "write"):
            return False, f"ACTIONS['{action}'] kind must be 'read' or 'write'"
    REGISTRY[name] = cls
    return True, name


def to_int(value, default: int, lo: int = None, hi: int = None) -> int:
    """Best-effort int conversion - tool arguments come straight from the model."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = int(default)
    if lo is not None:
        number = max(lo, number)
    if hi is not None:
        number = min(hi, number)
    return number


def apply_limit(items: list, params: dict, default: int = 0, cap: int = 200) -> tuple:
    """Apply the optional ``limit`` parameter: returns ``(items, truncated)``."""
    try:
        limit = int((params or {}).get("limit") or default or 0)
    except (TypeError, ValueError):
        limit = default or 0
    if limit <= 0:
        return items, False
    limit = min(limit, cap)
    return items[:limit], len(items) > limit


def paged(items: list, params: dict, default: int = 50, cap: int = 200) -> dict:
    """Build a list payload: count/total/items (+ truncated only when it is true)."""
    page, truncated = apply_limit(items, params, default=default, cap=cap)
    data = {"count": len(page), "total": len(items), "items": page}
    if truncated:
        data["truncated"] = True
    return data


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
