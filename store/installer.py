"""Installer helpers: move staged trees into data/plugins or data/skills."""

from __future__ import annotations

import shutil
from pathlib import Path

from core.plugin import logger


async def install_plugin_dir(plugin_mgr, src_root: Path, plugin_id: str) -> Path:
    """Move a staged plugin tree into data/plugins/<id>/ and hot-load it."""
    plugins_dir = Path(plugin_mgr.plugin_dir).resolve()
    plugins_dir.mkdir(parents=True, exist_ok=True)
    target = (plugins_dir / plugin_id).resolve()
    if not target.is_relative_to(plugins_dir) or target.parent != plugins_dir:
        raise ValueError(f"illegal plugin id: {plugin_id!r}")
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(src_root), str(target))
    loaded = await plugin_mgr.load_plugin_from_dir(target, auto_install=True)
    if loaded is None:
        logger.warning(f"[kira_ops] staged install of {plugin_id} finished but loading failed")
    return target


def install_skill_dir(skills_root: Path, src_root: Path, skill_name: str, overwrite: bool = False) -> Path:
    """Move a staged skill tree into data/skills/<name>/."""
    skills_root = Path(skills_root).resolve()
    skills_root.mkdir(parents=True, exist_ok=True)
    target = (skills_root / skill_name).resolve()
    if not target.is_relative_to(skills_root) or target.parent != skills_root:
        raise ValueError(f"illegal skill name: {skill_name!r}")
    if target.exists():
        if not overwrite:
            raise FileExistsError(f"skill '{skill_name}' already exists (pass overwrite=true to replace)")
        shutil.rmtree(target)
    shutil.move(str(src_root), str(target))
    return target
