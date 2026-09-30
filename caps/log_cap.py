"""Log domain: recent logger output and framework log files."""

from __future__ import annotations

from pathlib import Path

from core.utils.path_utils import get_data_path, get_root_path

from . import Capability, fail, ok, register


@register
class LogCap(Capability):
    name = "log"
    ACTIONS = {
        "tail":    ("read", False, "读取最近的日志缓存（内存环形缓存）"),
        "search":  ("read", False, "在日志缓存中按关键词搜索"),
        "history": ("read", False, "列出日志文件与审计文件概况"),
        "read_file": ("read", False, "读取指定日志文件的尾部"),
    }

    def handle_read(self, action, params):
        if action == "tail":
            from core.logging_manager import log_cache_manager
            try:
                cached = log_cache_manager.get_cache() or []
            except Exception as exc:
                return fail(f"failed to read log cache: {exc}")
            limit = max(1, int(params.get("limit") or 30))
            rows = cached[-limit:]
            return ok(count=len(rows),
                      items=[{
                          "time": r.get("time", ""),
                          "level": r.get("level", "INFO"),
                          "name": r.get("name", ""),
                          "message": r.get("message", ""),
                      } for r in rows])
        if action == "search":
            from core.logging_manager import log_cache_manager
            keyword = str(params.get("keyword") or "").strip().lower()
            if not keyword:
                return fail("keyword is required")
            try:
                cached = log_cache_manager.get_cache() or []
            except Exception as exc:
                return fail(f"failed to read log cache: {exc}")
            limit = max(1, int(params.get("limit") or 30))
            hits = [
                {"time": r.get("time", ""), "level": r.get("level", "INFO"),
                 "name": r.get("name", ""), "message": r.get("message", "")}
                for r in cached
                if keyword in str(r.get("message", "")).lower()
                or keyword in str(r.get("name", "")).lower()
            ]
            return ok(count=len(hits[-limit:]), items=hits[-limit:])
        if action == "history":
            data_dir = get_data_path()
            files = []
            candidates = []
            for folder in ("logs", "log"):
                base = data_dir / folder
                if base.is_dir():
                    candidates.extend(sorted(base.glob("*.log*")))
            candidates.extend(sorted(data_dir.glob("log.log*")))
            for f in candidates:
                try:
                    stat = f.stat()
                except Exception:
                    continue
                files.append({"file": str(f.relative_to(data_dir)), "size": stat.st_size})
                if len(files) >= 10:
                    break
            audit = []
            try:
                audit_log = self.plugin.audit
                audit = audit_log.history(7) if audit_log else []
            except Exception:
                audit = []
            return ok(log_files=files, audit_files=audit,
                      root=str(get_root_path()))
        if action == "read_file":
            data_dir = get_data_path()
            raw = str(params.get("path") or "").strip()
            if not raw:
                return fail("path is required (relative to data/, e.g. logs/xxx.log)")
            candidate = Path(raw)
            if candidate.is_absolute():
                return fail("only paths under data/ are allowed")
            target = (data_dir / candidate).resolve()
            if not str(target).startswith(str(data_dir.resolve())):
                return fail("path escapes data/")
            if not target.is_file():
                return fail(f"file not found: {raw}")
            text = target.read_text(encoding="utf-8", errors="replace")
            limit = max(500, int(params.get("limit") or 8000))
            return ok(file=raw, tail=text[-limit:], truncated=len(text) > limit)
        return fail(f"unknown read action '{action}'")
