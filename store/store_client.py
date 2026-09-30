"""Store listing client.

Only the *catalogue* lives here: fetch + cache the plugin list from the
configured store URL and look entries up. Everything that touches archives is
delegated to ``core.plugin.plugin_installer`` (see ``store/installer.py``), so
this module no longer carries its own zip handling, proxy racing or staging.

What remains kira_ops' own responsibility is the SSRF guard for arbitrary
https download URLs, because the store feed is user-configurable.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import time
import urllib.parse
from typing import Optional

import httpx

PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

DEFAULT_STORE_URL = "https://plugins.kira-ai.top/api/plugins/all"


class StoreClient:
    def __init__(self, settings: dict = None):
        self.apply_settings(settings or {})
        self._cache = None
        self._cache_ts = 0.0
        self._cache_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # settings
    # ------------------------------------------------------------------

    def apply_settings(self, settings: dict) -> None:
        s = settings or {}
        self.store_url = str(s.get("store_url") or DEFAULT_STORE_URL)
        self.github_proxy = str(s.get("github_proxy") or "").strip().rstrip("/")
        self.timeout = float(s.get("request_timeout") or 15)
        self.cache_ttl = int(s.get("cache_ttl") or 300)
        self.max_results = int(s.get("max_results") or 10)

    def gh_proxy_argument(self) -> str:
        """Value handed to the framework installer ('auto' = ranked mirrors)."""
        return self.github_proxy or "auto"

    # ------------------------------------------------------------------
    # listing
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

    @staticmethod
    def find_plugin(plugins: list, plugin_id: str):
        for p in plugins or []:
            if not isinstance(p, dict):
                continue
            pid = p.get("plugin_id") or p.get("id") or ""
            if str(pid) == str(plugin_id):
                return p
        return None

    @staticmethod
    def entry_links(entry: dict) -> tuple:
        """(repo_url, direct_zip_url) advertised by a store entry."""
        if not isinstance(entry, dict):
            return "", ""
        repo = str(entry.get("repo") or entry.get("repository") or "").strip()
        direct = str(entry.get("download_url") or entry.get("zip_url")
                     or entry.get("archive_url") or "").strip()
        return repo, direct

    # ------------------------------------------------------------------
    # SSRF guard for store-provided direct links
    # ------------------------------------------------------------------

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
        """Return a reason string when the URL must be blocked, else ''."""
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

    # ------------------------------------------------------------------
    # status helper (panel / ops_store sources)
    # ------------------------------------------------------------------

    def status(self) -> dict:
        return {
            "store_url": self.store_url,
            "github_proxy": self.github_proxy or "(auto)",
            "cache_ttl": self.cache_ttl,
            "cached": self._cache is not None,
            "installer": "framework (core.plugin.plugin_installer)",
        }
