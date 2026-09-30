"""Path guards: deny-lists for reads / writes / deletes (fail-closed).

Paths in the deny lists are interpreted relative to the KiraAI root when not
absolute. A candidate is blocked when it equals a denied path or lives inside
it. Used for validating user-supplied paths (e.g. agent policy edits) and any
future file-touching action.
"""

from __future__ import annotations

from pathlib import Path

from core.utils.path_utils import get_root_path


def _resolve(raw) -> Path:
    root = get_root_path()
    p = Path(str(raw or ""))
    if not p.is_absolute():
        p = root / p
    try:
        return p.resolve()
    except Exception:
        return p


def _deny_list(kind: str, protected: dict) -> list:
    protected = protected or {}
    mapping = {
        "read": protected.get("path_deny_read"),
        "write": protected.get("path_deny_write"),
        "delete": protected.get("path_deny_delete"),
    }
    return [x for x in (mapping.get(kind) or []) if str(x or "").strip()]


def check_path(kind: str, path, protected: dict):
    """Return (ok, reason). kind: 'read' | 'write' | 'delete'."""
    target = _resolve(path)
    target_str = str(target)
    for entry in _deny_list(kind, protected):
        base = _resolve(entry)
        try:
            if target == base or base in target.parents:
                return False, f"path is protected ({kind} deny: {entry})"
        except Exception:
            # If comparison fails for any reason, be conservative.
            if target_str.lower().startswith(str(base).lower()):
                return False, f"path is protected ({kind} deny: {entry})"
    return True, ""


def collect_path_violations(kind: str, paths, protected: dict) -> list:
    """Return reasons for every path that is blocked (empty = all fine)."""
    reasons = []
    for p in paths or []:
        if not str(p or "").strip():
            continue
        ok, why = check_path(kind, p, protected)
        if not ok:
            reasons.append(f"{p}: {why}")
    return reasons


def is_within(base, target) -> bool:
    """True when *target* resolves inside *base* (or equals it)."""
    try:
        base_r = Path(str(base)).resolve()
        target_r = Path(str(target)).resolve()
        return target_r == base_r or base_r in target_r.parents
    except Exception:
        return False


def is_direct_child(base, target) -> bool:
    """True when *target* is (or would be) a direct child of *base*."""
    try:
        base_r = Path(str(base)).resolve()
        target_r = Path(str(target)).resolve()
        return target_r.parent == base_r
    except Exception:
        return False
