"""Field-level masking and write guards for configuration objects.

Keyword matching is component-aware so that e.g. "max_tokens" does NOT match
"token", while "access_token", "apiKey" and "client_secret" do.
"""

from __future__ import annotations

import re

MASK = "***"

# Applied on top of whatever the operator configured: these key names carry
# credentials in every ecosystem we touch (HTTP Authorization/Bearer headers,
# cookies, provider keys), so masking them must not be possible to switch off.
ALWAYS_MASK = (
    "api_key", "apikey", "authorization", "bearer", "cookie",
    "secret", "password", "credential", "token", "private_key",
)


def with_floor(keywords) -> list:
    """User keywords + the non-negotiable ones (deduplicated, order kept)."""
    out = []
    for key in list(keywords or []) + list(ALWAYS_MASK):
        if key not in out:
            out.append(key)
    return out

_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")


def _norm(text) -> str:
    return _SPLIT_RE.sub("", str(text or "").lower())


def _components(key) -> list:
    return [c for c in _SPLIT_RE.split(str(key or "")) if c]


def key_matches(key, keywords) -> bool:
    """True when *key* looks like it carries one of the sensitive *keywords*."""
    whole = _norm(key)
    if not whole:
        return False
    wanted = [_norm(k) for k in (keywords or []) if _norm(k)]
    if not wanted:
        return False
    for k in wanted:
        if whole == k or whole.endswith(k):
            return True
    for comp in _components(key):
        n = _norm(comp)
        for k in wanted:
            if n == k or n.endswith(k):
                return True
    return False


def _mask_all(data):
    if isinstance(data, dict):
        return {k: _mask_all(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_mask_all(v) for v in data]
    return MASK


def mask_data(data, keywords):
    """Deep-copy *data*, replacing values under sensitive keys with MASK."""
    if not keywords:
        return data
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if key_matches(k, keywords):
                out[k] = _mask_all(v) if isinstance(v, (dict, list)) else MASK
            else:
                out[k] = mask_data(v, keywords)
        return out
    if isinstance(data, list):
        return [mask_data(v, keywords) for v in data]
    return data


def flatten(data, prefix: str = "") -> list:
    """Flatten nested dicts to (dotted_path, leaf_key, value) tuples."""
    items = []
    if isinstance(data, dict):
        for k, v in data.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict) and v:
                items.extend(flatten(v, path))
            else:
                items.append((path, str(k), v))
    else:
        leaf = prefix.split(".")[-1] if prefix else ""
        items.append((prefix, leaf, data))
    return items


def check_field_write(key, protected: dict, restrict: bool = False):
    """Return (ok, reason) for writing a single field."""
    protected = protected or {}
    if key_matches(key, with_floor(protected.get("write_deny"))):
        return False, f"field '{key}' is on the write deny list"
    if restrict and not key_matches(key, protected.get("write_allow") or []):
        return False, f"field '{key}' is not on the write allow list"
    return True, ""


def check_patch(patch, protected: dict, restrict: bool = False) -> dict:
    """Inspect a (possibly nested) patch.

    Returns {"ok", "violations", "secrets", "leaves"} where:
      violations - list of reasons that must abort the write
      secrets    - [(path, key)] entries matching the read mask; the caller
                   should escalate them to the config.set_secret action
      leaves     - flattened (path, key, value) list for applying
    """
    protected = protected or {}
    result = {"ok": True, "violations": [], "secrets": [], "leaves": []}
    if not isinstance(patch, dict):
        result["ok"] = False
        result["violations"].append("patch must be a JSON object")
        return result
    for path, key, value in flatten(patch):
        result["leaves"].append((path, key, value))
        ok, why = check_field_write(key, protected, restrict=restrict)
        if not ok:
            result["violations"].append(f"{path}: {why}")
            continue
        if key_matches(key, with_floor(protected.get("read_mask"))):
            result["secrets"].append((path, key))
    result["ok"] = not result["violations"]
    return result
