"""Quick validation: schema.json builds into config fields, manifest loads."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from core.config.config_field import build_fields  # noqa: E402

PLUGIN = Path(__file__).resolve().parents[1]

raw = json.loads((PLUGIN / "schema.json").read_text(encoding="utf-8"))
fields = build_fields(raw)
print(f"schema fields: {len(fields)}")
for f in fields:
    print(f"  - {f.key} ({type(f).__name__})")

manifest = json.loads((PLUGIN / "manifest.json").read_text(encoding="utf-8"))
print("manifest plugin_id:", manifest.get("plugin_id"))
print("core_version:", manifest.get("core_version"))

from packaging.specifiers import SpecifierSet
from packaging.version import Version
from core.config.default import VERSION

spec = SpecifierSet(manifest["core_version"], prereleases=True)
ver = Version(VERSION.lstrip("v"))
print("compatible with current VERSION:", ver in spec)
