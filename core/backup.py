"""Automatic pre-write snapshots with retention cleanup.

Snapshots are taken by plain code right before a write is applied; nothing
here depends on the LLM. Backups live under the plugin data dir:

    <plugin_data>/backups/<YYYYmmdd-HHMMSS>_<label>/

Each folder carries a _meta.json with origin paths, md5 hashes and an
'applied' marker so restore() can detect edits made after our write.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path


def _data_base() -> Path:
    """Resolved data dir of the *current* instance."""
    try:
        from core.utils.path_utils import get_data_path
        return Path(get_data_path()).resolve()
    except Exception:
        return Path.cwd() / "data"


def _root_base() -> Path:
    try:
        from core.utils.path_utils import get_root_path
        return Path(get_root_path()).resolve()
    except Exception:
        return Path.cwd()


def record_origin(path) -> dict:
    """Where a snapshot came from, expressed so it survives a tree copy.

    Instances are usually full copies of each other (KiraAI9 -> KiraAI10), so an
    absolute path recorded in one tree points at the *other* instance once the
    folder is copied. ``origin_rel`` is relative to the data dir and is resolved
    against the current instance at restore time; ``origin`` is kept for humans
    and for files that live outside data/.
    """
    src = Path(str(path))
    entry = {"origin": str(src), "origin_rel": None}
    try:
        entry["origin_rel"] = str(src.resolve().relative_to(_data_base()))
    except Exception:
        entry["origin_rel"] = None
    return entry


# Path shapes that identify a KiraAI instance tree. A legacy rollback point
# (recorded before origin_rel existed) whose absolute path has this shape but
# lives outside the current root came from another copy of KiraAI.
_TREE_MARKERS = ("plugin_data", "plugins", "config", "memory", "skills")


def _looks_like_another_tree(path: Path) -> bool:
    parts = [p.lower() for p in path.resolve().parts]
    if "data" not in parts:
        return False
    tail = parts[parts.index("data") + 1:]
    return bool(tail) and any(marker in tail for marker in _TREE_MARKERS)


def resolve_origin(entry) -> tuple:
    """Return (path, error) for a snapshot entry in *this* instance's tree.

    * ``origin_rel`` (new snapshots): resolved against the current data dir, so
      a rollback point survives a folder copy and points at this instance's own
      files.
    * legacy absolute ``origin``: allowed when it is inside the current root.
      Paths that look like another KiraAI tree are refused instead of silently
      writing into the other instance; a plain file outside any tree (an OS
      config someone chose to snapshot) is still restorable.
    """
    rel = entry.get("origin_rel")
    if rel:
        return _data_base() / str(rel), ""
    origin = str(entry.get("origin") or "")
    if not origin:
        return None, "rollback entry has no recorded origin"
    candidate = Path(origin)
    try:
        candidate.resolve().relative_to(_root_base())
        return candidate, ""
    except Exception:
        pass
    if _looks_like_another_tree(candidate):
        return None, (
            f"this rollback point belongs to another KiraAI instance "
            f"({origin}); it travelled here with a copied folder and will not "
            f"be written outside the current tree")
    return candidate, ""


def file_md5(path) -> str:
    try:
        h = hashlib.md5()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def _safe_label(label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", str(label or "item")).strip("_")
    return (cleaned or "item")[:48]


def _folder_size(folder: Path) -> int:
    total = 0
    try:
        for f in folder.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except Exception:
        pass
    return total


class BackupManager:
    def __init__(self, base_dir, settings: dict = None):
        self.dir = Path(base_dir) / "backups"
        # The framework runs same-step tool calls in parallel and snapshots run
        # in a worker thread, so two snapshots can land in the same second.
        self._lock = threading.Lock()
        self.apply_settings(settings or {})

    def apply_settings(self, settings: dict) -> None:
        s = settings or {}
        self.enabled = bool(s.get("enabled", True))
        self.keep_last = max(1, int(s.get("keep_last", 20) or 20))
        self.max_age_days = max(1, int(s.get("max_age_days", 7) or 7))
        self.max_total_mb = max(1, int(s.get("max_total_mb", 200) or 200))
        self.cleanup_on_start = bool(s.get("cleanup_on_start", True))

    # ------------------------------------------------------------------
    # meta helpers
    # ------------------------------------------------------------------

    def _meta_path(self, bid: str) -> Path:
        return self.dir / str(bid or "") / "_meta.json"

    def _load_meta(self, bid: str):
        try:
            return json.loads(self._meta_path(bid).read_text(encoding="utf-8"))
        except Exception:
            return None

    def _save_meta(self, bid: str, meta: dict) -> None:
        try:
            self._meta_path(bid).write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # snapshot / restore
    # ------------------------------------------------------------------

    def snapshot(self, files, label: str, reason: str = "write", extra: dict = None) -> dict:
        """Copy *files* into a new backup folder and return its descriptor."""
        if not self.enabled:
            return {"id": "", "skipped": "backup disabled"}
        sources = [Path(str(f)) for f in files or []]
        sources = [p for p in sources if p.is_file()]
        if not sources:
            return {"id": "", "skipped": "no existing files to snapshot"}

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = f"{stamp}_{_safe_label(label)}"
        with self._lock:
            folder = self.dir / base
            counter = 1
            # exist_ok=False makes the claim atomic: a racing snapshot retries
            # with a new suffix instead of silently writing into our folder.
            while True:
                try:
                    folder.mkdir(parents=True, exist_ok=False)
                    break
                except FileExistsError:
                    counter += 1
                    folder = self.dir / f"{base}_{counter}"

        entries = []
        for src in sources:
            dest = folder / src.name
            n = 1
            while dest.exists():
                dest = folder / f"{src.stem}__{n}{src.suffix}"
                n += 1
            record = record_origin(src)
            try:
                shutil.copy2(src, dest)
                record.update({
                    "name": src.name,
                    "stored": dest.name,
                    "md5": file_md5(src),
                })
                entries.append(record)
            except Exception as exc:
                record["error"] = str(exc)
                entries.append(record)

        meta = {
            "id": folder.name,
            "label": _safe_label(label),
            "reason": str(reason or ""),
            "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "root": str(_root_base()),
            "data": str(_data_base()),
            "files": entries,
            "applied": False,
            "post_md5": {},
        }
        if extra:
            meta.update(extra)
        self._save_meta(folder.name, meta)
        self.cleanup()
        return {
            "id": folder.name,
            "path": str(folder),
            "files": [e.get("name") for e in entries],
        }

    def mark_applied(self, bid: str) -> None:
        """Record current (post-write) hashes so restore() can spot later edits."""
        meta = self._load_meta(bid)
        if not meta:
            return
        post = {}
        for entry in meta.get("files", []):
            target, why = resolve_origin(entry)
            if target is not None and target.is_file():
                post[entry.get("name") or target.name] = file_md5(target)
        meta["applied"] = True
        meta["post_md5"] = post
        self._save_meta(bid, meta)

    def list(self, limit: int = 50) -> list:
        out = []
        if not self.dir.exists():
            return out
        folders = sorted(
            (d for d in self.dir.iterdir() if d.is_dir()),
            key=lambda p: p.name,
            reverse=True,
        )
        for folder in folders:
            meta = self._load_meta(folder.name) or {}
            # the absolute path is deliberately not returned: the id is the
            # handle for restore() and this keeps the payload small.
            entry = {
                "id": folder.name,
                "created": meta.get("created", ""),
                "label": meta.get("label", ""),
                "reason": meta.get("reason", ""),
                "files": len(meta.get("files", []) or []),
                "applied": bool(meta.get("applied")),
            }
            # a rollback point copied over from another instance cannot be
            # restored here - say so instead of silently writing elsewhere
            for recorded in (meta.get("files") or []):
                target, _why = resolve_origin(recorded)
                if target is None:
                    entry["foreign"] = True
                    break
            out.append(entry)
            if len(out) >= int(limit or 50):
                break
        return out

    def count(self) -> int:
        """Number of snapshot folders - no _meta.json is read, unlike list()."""
        if not self.dir.exists():
            return 0
        try:
            return sum(1 for d in self.dir.iterdir() if d.is_dir())
        except Exception:
            return 0

    def restore(self, bid: str, force: bool = False) -> dict:
        folder = self.dir / str(bid or "")
        meta = self._load_meta(bid)
        if not folder.is_dir() or not meta:
            return {"ok": False, "error": f"backup '{bid}' not found"}
        restored, conflicts, errors, foreign = [], [], [], []
        for entry in meta.get("files", []):
            target, why = resolve_origin(entry)
            stored = folder / str(entry.get("stored") or entry.get("name") or "")
            if target is None:
                foreign.append(f"{entry.get('name') or '?'}: {why}")
                continue
            if not stored.is_file():
                continue
            current = file_md5(target) if target.is_file() else ""
            recorded = (meta.get("post_md5") or {}).get(entry.get("name") or "")
            if recorded and current and current != recorded and not force:
                conflicts.append(str(target))
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(stored, target)
                restored.append(str(target))
            except Exception as exc:
                errors.append(f"{target}: {exc}")
        need_force = bool(conflicts) and not force
        result = {
            "ok": not errors and not need_force and not foreign,
            "restored": restored,
            "conflicts": conflicts,
            "errors": errors,
            "need_force": need_force,
        }
        if foreign:
            result["foreign"] = foreign
            result["hint"] = ("some entries belong to another KiraAI instance and were "
                              "skipped - restore them from the instance that created them")
        return result

    # ------------------------------------------------------------------
    # retention
    # ------------------------------------------------------------------

    def cleanup(self) -> dict:
        if not self.dir.exists():
            return {"removed": 0, "kept": 0}
        removed = set()
        folders = [d for d in self.dir.iterdir() if d.is_dir()]

        # 1) keep at most keep_last per label
        groups: dict = {}
        for d in folders:
            raw = d.name.split("_", 1)[1] if "_" in d.name else d.name
            label = re.sub(r"_\d+$", "", raw)
            groups.setdefault(label, []).append(d)
        for _label, items in groups.items():
            items.sort(key=lambda p: p.name, reverse=True)
            for old in items[self.keep_last:]:
                shutil.rmtree(old, ignore_errors=True)
                removed.add(old.name)

        # 2) age-based pruning
        cutoff = datetime.now() - timedelta(days=self.max_age_days)
        for d in [x for x in self.dir.iterdir() if x.is_dir()]:
            stamp = d.name.split("_", 1)[0]
            try:
                made = datetime.strptime(stamp, "%Y%m%d-%H%M%S")
            except ValueError:
                continue
            if made < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                removed.add(d.name)

        # 3) total-size cap (delete oldest first)
        cap = self.max_total_mb * 1024 * 1024
        current = [x for x in self.dir.iterdir() if x.is_dir()]
        total = sum(_folder_size(x) for x in current)
        if total > cap:
            for d in sorted(current, key=lambda p: p.name):
                if total <= cap:
                    break
                size = _folder_size(d)
                shutil.rmtree(d, ignore_errors=True)
                removed.add(d.name)
                total -= size

        kept = len([x for x in self.dir.iterdir() if x.is_dir()])
        return {"removed": len(removed), "kept": kept}
