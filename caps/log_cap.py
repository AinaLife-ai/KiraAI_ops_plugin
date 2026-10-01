"""Log domain: recent logger output and framework log files.

``read_file`` is deliberately restricted to *log* files. It used to accept any
path under ``data/``, which turned it into an unmasked arbitrary-read: a reader
could pull ``config/system_config.json`` (plaintext API keys) or
``memory/chat_memory.json`` (every conversation) straight into the model
context, bypassing the read mask that ``config.get`` applies.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.utils.path_utils import get_data_path, get_root_path

from . import Capability, fail, ok, register, to_int

LOG_FILE_RE = re.compile(r"(^log\.log|.*\.log)(\.\d+)?$", re.IGNORECASE)

# A single log record can be a multi-kilobyte JSON blob (tool results, raw
# model payloads), so every message is clipped before it reaches the model -
# an unrestricted log.tail used to be able to return ~120k characters.
MAX_MESSAGE_CHARS = 400
DEFAULT_TAIL = 25
MAX_TAIL = 100
MIN_FILE_READ = 500
MAX_FILE_READ = 20000


def _clip(text, limit: int = MAX_MESSAGE_CHARS) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"…(+{len(text) - limit} chars)"


def _row(record: dict) -> dict:
    return {
        "time": record.get("time", ""),
        "level": record.get("level", "INFO"),
        "name": record.get("name", ""),
        "message": _clip(record.get("message", "")),
    }


@register
class LogCap(Capability):
    name = "log"
    ACTIONS = {
        "tail":      ("read", False, "读取最近的日志缓存（内存环形缓存，单条超长会被截断）"),
        "search":    ("read", False, "在日志缓存中按关键词搜索"),
        "history":   ("read", False, "列出日志文件与审计文件概况"),
        "read_file": ("read", False, "读取指定日志文件的尾部"),
    }

    @staticmethod
    def _cache():
        from core.logging_manager import log_cache_manager
        return log_cache_manager.get_cache() or []

    def handle_read(self, action, params):
        params = params or {}
        if action == "tail":
            try:
                cached = self._cache()
            except Exception as exc:
                return fail(f"failed to read log cache: {exc}")
            limit = to_int(params.get("limit"), DEFAULT_TAIL, 1, MAX_TAIL)
            rows = cached[-limit:]
            return ok(count=len(rows), items=[_row(r) for r in rows])
        if action == "search":
            keyword = str(params.get("keyword") or "").strip().lower()
            if not keyword:
                return fail("keyword is required")
            try:
                cached = self._cache()
            except Exception as exc:
                return fail(f"failed to read log cache: {exc}")
            limit = to_int(params.get("limit"), DEFAULT_TAIL, 1, MAX_TAIL)
            hits = [
                r for r in cached
                if keyword in str(r.get("message", "")).lower()
                or keyword in str(r.get("name", "")).lower()
            ]
            return ok(count=len(hits[-limit:]), items=[_row(r) for r in hits[-limit:]])
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
            root = data_dir.resolve()
            raw = str(params.get("path") or "").strip()
            if not raw:
                return fail("path is required (relative to data/, e.g. logs/xxx.log)")
            candidate = Path(raw)
            if candidate.is_absolute():
                return fail("only paths under data/ are allowed")
            target = (data_dir / candidate).resolve()
            if not str(target).startswith(str(root)):
                return fail("path escapes data/")
            if not target.is_file():
                return fail(f"file not found: {raw}")
            # log files only - see the module docstring.
            if not LOG_FILE_RE.match(target.name):
                return fail(
                    "only log files can be read here (name must match *.log or log.log*); "
                    "use config.get / plugin.info / skill.content for other content")
            allowed, why = self.plugin.engine.check_path("read", str(target))
            if not allowed:
                return fail(why)
            text = target.read_text(encoding="utf-8", errors="replace")
            limit = to_int(params.get("limit"), 8000, MIN_FILE_READ, MAX_FILE_READ)
            return ok(file=raw, tail=text[-limit:], truncated=len(text) > limit,
                      size=len(text), limit=limit)
        return fail(f"unknown read action '{action}'")
