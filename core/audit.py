"""JSONL audit trail, one file per day, with retention cleanup."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path


class AuditLog:
    def __init__(self, base_dir, settings: dict = None):
        self.dir = Path(base_dir) / "audit"
        self.apply_settings(settings or {})

    def apply_settings(self, settings: dict) -> None:
        s = settings or {}
        self.enabled = bool(s.get("enabled", True))
        self.audit_reads = bool(s.get("audit_reads", False))
        self.max_age_days = max(1, int(s.get("max_age_days", 14) or 14))

    def write(self, *, kind: str = "write", tool: str = "", domain: str = "",
              action: str = "", target: str = "", sid: str = "", uid: str = "",
              args=None, ok: bool = True, error: str = "", ms: int = 0,
              backup: str = "", note: str = "") -> None:
        """Append one record; never raises (auditing must not break the flow)."""
        try:
            if not self.enabled:
                return
            if kind == "read" and not self.audit_reads:
                return
            record = {
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "kind": kind, "tool": tool, "domain": domain, "action": action,
                "target": str(target or ""), "sid": str(sid or ""), "uid": str(uid or ""),
                "ok": bool(ok), "err": str(error or ""), "ms": int(ms or 0),
                "backup": str(backup or ""), "note": str(note or ""),
            }
            if args is not None:
                try:
                    json.dumps(args, ensure_ascii=False)
                    record["args"] = args
                except TypeError:
                    record["args"] = str(args)
            self.dir.mkdir(parents=True, exist_ok=True)
            path = self.dir / f"audit-{datetime.now().strftime('%Y-%m-%d')}.jsonl"
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def tail(self, limit: int = 50, keyword: str = None, day: str = None) -> list:
        """Return the newest *limit* records (oldest first within the slice)."""
        result = []
        if not self.dir.exists():
            return result
        files = sorted(self.dir.glob("audit-*.jsonl"), key=lambda p: p.name, reverse=True)
        if day:
            files = [f for f in files if f.stem == f"audit-{day}"]
        needle = (keyword or "").lower()
        want = max(1, int(limit or 50))
        for path in files:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            for line in reversed(lines):
                if needle and needle not in line.lower():
                    continue
                try:
                    result.append(json.loads(line))
                except Exception:
                    continue
                if len(result) >= want:
                    return list(reversed(result))
        return list(reversed(result))

    def history(self, limit: int = 7) -> list:
        """Metadata for the most recent day files."""
        out = []
        if not self.dir.exists():
            return out
        for path in sorted(self.dir.glob("audit-*.jsonl"), key=lambda p: p.name, reverse=True):
            try:
                stat = path.stat()
            except Exception:
                continue
            out.append({
                "file": path.name,
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
            if len(out) >= max(1, int(limit or 7)):
                break
        return out

    def cleanup(self) -> int:
        if not self.dir.exists():
            return 0
        cutoff = (datetime.now() - timedelta(days=self.max_age_days)).strftime("%Y-%m-%d")
        removed = 0
        for path in self.dir.glob("audit-*.jsonl"):
            stem = path.stem.replace("audit-", "")
            if stem < cutoff:
                try:
                    path.unlink()
                    removed += 1
                except Exception:
                    pass
        return removed
