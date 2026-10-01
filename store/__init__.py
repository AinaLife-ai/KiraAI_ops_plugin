"""Store package: listing client + framework-backed install helpers."""

from .installer import (
    extract_zip_safely,
    install_plugin_from_direct_url,
    install_plugin_from_repo,
    install_plugin_from_zip_bytes,
    install_skill_dir,
    install_skill_from_zip_bytes,
)
from .store_client import DEFAULT_STORE_URL, PLUGIN_ID_RE, StoreClient

__all__ = [
    "StoreClient",
    "PLUGIN_ID_RE",
    "DEFAULT_STORE_URL",
    "install_plugin_from_repo",
    "install_plugin_from_zip_bytes",
    "install_plugin_from_direct_url",
    "install_skill_dir",
    "install_skill_from_zip_bytes",
    "extract_zip_safely",
]
