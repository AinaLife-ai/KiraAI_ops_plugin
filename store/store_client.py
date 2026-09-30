"""Plugin-store client: data source, GitHub proxy racing, zip installs.

Ported behaviour from the former standalone store plugin, trimmed to what
kira_ops needs: cached listing fetch, fastest-proxy selection with failover,
SSRF-guarded direct downloads, safe zip extraction and staging installs.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import shutil
import socket
import tempfile
import time
import urllib.parse
import zipfile
from pathlib import Path

import httpx

from core.plugin import logger

PROXY_CANDIDATES = [
    "https://ghproxy.com",
    "https://gh-proxy.com",
    "https://ghfast.top",
    "https://ghps.cc",
    "https://mirror.ghproxy.com",
    "https://gh.ddlc.top",
]
PROXY_TEST_URL = "https://raw.githubusercontent.com/octocat/Hello-World/master/README"
PROXY_CACHE_TTL = 600.0

PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class StoreClient:
    def __init__(self, settings: dict = None):
        self.apply_settings(settings or {})
        self._cache = None
        self._cache_ts = 0.0
        self._cache_lock = asyncio.Lock()
        self._proxy_result = None
        self._proxy_lock = asyncio.Lock()

    def apply_settings(self, settings: dict) -> None:
        s = settings or {}
        self.store_url = str(s.get("store_url") or "https://plugins.kira-ai.top/api/plugins/all")
        self.proxy = str(s.get("github_proxy") or "").strip().rstrip("/")
        self.timeout = float(s.get("request_timeout") or 15)
        self.cache_ttl = int(s.get("cache_ttl") or 300)
        self.max_results = int(s.get("max_results") or 10)

    # ------------------------------------------------------------------
    # store listing
    # ------------------------------------------------------------------

    async def fetch(self, force: bool = False) -> list:
        now = time.time()
        if not force and self.cache_ttl > 0 and self._cache is not None \
                and (now - self._cache_ts) < self.cache_ttl:
            return self._cache
        async with self._cache_lock:
            now = time.time()
            if not force and self.cache_ttl > 0 and self._cache is not None \
                    and (now - self._cache_ts) < self.cache_ttl:
                return self._cache
            data = await self._fetch_uncached()
            if self.cache_ttl > 0:
                self._cache = data
                self._cache_ts = now
            return data

    async def _fetch_uncached(self) -> list:
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            resp = await client.get(self.store_url)
            resp.raise_for_status()
            payload = resp.json()
        if isinstance(payload, dict):
            for key in ("plugins", "data", "items", "results", "list"):
                val = payload.get(key)
                if isinstance(val, list):
                    payload = val
                    break
                if isinstance(val, dict) and val and all(isinstance(x, dict) for x in val.values()):
                    payload = list(val.values())
                    break
            else:
                for val in payload.values():
                    if isinstance(val, list):
                        payload = val
                        break
                    if isinstance(val, dict) and val and all(isinstance(x, dict) for x in val.values()):
                        payload = list(val.values())
                        break
        return payload if isinstance(payload, list) else []

    def find_plugin(self, plugins: list, plugin_id: str):
        for p in plugins or []:
            if not isinstance(p, dict):
                continue
            pid = p.get("plugin_id") or p.get("id") or ""
            if str(pid) == str(plugin_id):
                return p
        return None

    # ------------------------------------------------------------------
    # proxy racing
    # ------------------------------------------------------------------

    async def pick_proxy(self) -> list:
        if self.proxy:
            return [self.proxy, None]
        now = time.time()
        if self._proxy_result is not None and (now - self._proxy_result[1]) < PROXY_CACHE_TTL:
            return list(self._proxy_result[0])
        async with self._proxy_lock:
            now = time.time()
            if self._proxy_result is not None and (now - self._proxy_result[1]) < PROXY_CACHE_TTL:
                return list(self._proxy_result[0])
            test_timeout = max(2.0, min(self.timeout / 3.0, 5.0))
            candidates = [("direct", None)] + [(p, p) for p in PROXY_CANDIDATES]

            async def probe(label, prefix):
                url = f"{prefix}/{PROXY_TEST_URL}" if prefix else PROXY_TEST_URL
                try:
                    async with httpx.AsyncClient(timeout=test_timeout, follow_redirects=True) as client:
                        t0 = time.time()
                        resp = await client.get(url)
                        cost = time.time() - t0
                        if resp.status_code == 200 and len(resp.content) > 0:
                            return (cost, label, prefix)
                except Exception:
                    pass
                return (float("inf"), label, prefix)

            results = await asyncio.gather(*(probe(l, p) for l, p in candidates))
            ok = [(c, l, p) for c, l, p in results if p is not None and c != float("inf")]
            ok.sort(key=lambda x: x[0])
            ordered = [p for _c, _l, p in ok] + [None]
            self._proxy_result = (ordered, time.time())
            return list(ordered)

    def invalidate_proxy(self, prefix) -> None:
        if not prefix or self._proxy_result is None:
            return
        ordered = [p for p in self._proxy_result[0] if p != prefix]
        if None not in ordered:
            ordered.append(None)
        self._proxy_result = (ordered, self._proxy_result[1])

    # ------------------------------------------------------------------
    # downloads
    # ------------------------------------------------------------------

    @staticmethod
    def parse_repo(repo):
        if not repo:
            return None
        repo = re.sub(r"\.git$", "", str(repo).strip())
        m = re.search(r"github\.com[/:]([^/]+)/([^/\s]+)", repo)
        if m:
            return m.group(1), m.group(2)
        parts = [p for p in repo.split("/") if p]
        if len(parts) >= 2:
            return parts[-2], parts[-1]
        return None

    @staticmethod
    def _is_private_ip(ip_str: str) -> bool:
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return True
        return bool(ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_multicast or ip.is_reserved or ip.is_unspecified)

    @classmethod
    def validate_direct_url(cls, url: str) -> str:
        try:
            parsed = urllib.parse.urlparse(str(url).strip())
        except Exception as exc:
            return f"cannot parse download url ({exc})"
        if parsed.scheme.lower() != "https":
            return f"only https downloads are allowed (scheme: {parsed.scheme or 'none'})"
        host = (parsed.hostname or "").lower()
        if not host:
            return "download url has no host"
        if host == "localhost" or host.endswith(".localhost"):
            return f"local addresses are not allowed: {host}"
        try:
            infos = socket.getaddrinfo(host, None)
        except Exception:
            return f"cannot resolve host: {host}"
        for info in infos:
            ip = info[4][0]
            if cls._is_private_ip(ip):
                return f"host resolves to a private/reserved address: {host} -> {ip}"
        return ""

    async def download(self, url: str, dest: Path) -> None:
        async with httpx.AsyncClient(timeout=max(60.0, self.timeout), follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as fh:
                    async for chunk in resp.aiter_bytes():
                        fh.write(chunk)

    async def download_github_archive(self, owner: str, repo: str, dest: Path) -> str:
        base = f"https://github.com/{owner}/{repo}/archive/HEAD.zip"
        attempts = []
        for prefix in await self.pick_proxy():
            url = f"{prefix}/{base}" if prefix else base
            label = prefix or "direct"
            try:
                await self.download(url, dest)
                logger.info(f"[kira_ops] downloaded {owner}/{repo} via {label}")
                return url
            except Exception as exc:
                logger.warning(f"[kira_ops] download attempt failed ({label}): {exc}")
                attempts.append(f"{label}({type(exc).__name__})")
                self.invalidate_proxy(prefix)
        self._proxy_result = None
        raise RuntimeError("all download sources failed: " + "、".join(attempts))

    # ------------------------------------------------------------------
    # zip handling / staging installs
    # ------------------------------------------------------------------

    @staticmethod
    def extract_zip(zip_path: Path, staging: Path):
        """Extract with zip-slip guard; returns (manifest or None, root Path)."""
        staging = Path(staging)
        staging.mkdir(parents=True, exist_ok=True)
        prefix = ""
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            tops = {n.split("/")[0] for n in names if n and not n.endswith("/")}
            if len(tops) == 1:
                td = next(iter(tops))
                if all(n == td or n.startswith(td + "/") for n in names if n):
                    prefix = td + "/"
            for member in names:
                if prefix and not member.startswith(prefix):
                    continue
                rel = member[len(prefix):] if prefix else member
                if not rel:
                    continue
                target = (staging / rel).resolve()
                if not str(target).startswith(str(staging.resolve()) + os.sep):
                    raise ValueError(f"illegal extraction path: {member}")
                if member.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        matches = sorted(staging.rglob("manifest.json"),
                         key=lambda p: len(p.relative_to(staging).parts))
        if matches:
            try:
                return json.loads(matches[0].read_text(encoding="utf-8")), matches[0].parent
            except Exception as exc:
                logger.warning(f"[kira_ops] failed to parse manifest: {exc}")
        return None, staging

    @staticmethod
    def temp_workspace(prefix: str = "kira_ops_") -> Path:
        return Path(tempfile.mkdtemp(prefix=prefix))
