"""The API key MTPLX kept (~/.mtplx/api-key, before CARL 1.2.0 ran llama.cpp only), copied once to CARL's key file
when that has none (implements ConfDirMigration, contract C-027; decision 13: its own adapter, wired only in the
macos profile, because MTPLX was used on nothing else). The second of the key's earlier places in host/common.sh
ensure_api_key, after the old settings folder's (llm_deploy_migration.py).
"""
from __future__ import annotations

from ...domain.config.key_paths import LEGACY_KEY_FILES
from ...domain.config.paths import home_path
from ..common.key_store import KeyMigration


def key_migration(home: str, target: str) -> KeyMigration:
    """MTPLX's key to CARL's key file (target)."""
    return KeyMigration(home, home_path(home, LEGACY_KEY_FILES[1]), target)
