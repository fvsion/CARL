"""CARL's settings under its old name, ~/.config/llm-deploy (CARL was called LLM-Deploy before 1.2.0), moved to
~/.config/carl once (implements ConfDirMigration, contract C-027; decision 13: wired only in the macos profile).

FolderMigration is host/common.sh migrate_conf_dir: the folder moves, and a link is left at the old path, because
client configs written before the rename read the key there. Nothing moves when CARL_CONF_DIR picks another
folder, or when ~/.config/carl exists already (the old folder is then left alone, with a note at each start).
Files end up 0600, the folder 0700.

KeyMigration is the first of the key's earlier places in host/common.sh ensure_api_key: the old folder's api-key,
copied (not moved) to CARL's key file when that has no key, so configured clients keep working.
"""
from __future__ import annotations

import os
import shutil
import stat
from typing import List

from ...domain.config.key_paths import LEGACY_KEY_FILES
from ...domain.config.paths import CONF_DIR, LEGACY_CONF_DIR, home_path
from ..common.key_store import KeyMigration, tilde


class FolderMigration:
    """home: the user's home folder; conf_dir_set: CARL_CONF_DIR names a folder (then nothing moves)."""

    def __init__(self, home: str, conf_dir_set: bool) -> None:
        self.home, self.conf_dir_set = home, conf_dir_set
        self.new, self.old = home_path(home, CONF_DIR), home_path(home, LEGACY_CONF_DIR)

    def pending(self) -> bool:
        return not self.conf_dir_set and os.path.isdir(self.old) and not os.path.islink(self.old)

    def migrate(self) -> List[str]:
        if not self.pending():
            return []
        new, old = tilde(self.new, self.home), tilde(self.old, self.home)
        if os.path.lexists(self.new):
            return [f"note: both {new} and {old} exist: using {new}; the old folder is not used"]
        try:
            os.rename(self.old, self.new)
        except OSError:
            try:
                shutil.move(self.old, self.new)
            except (OSError, shutil.Error):
                return [f"warning: could not move {old} to {new}; still using the old folder"]
        os.chmod(self.new, 0o700)
        for name in os.listdir(self.new):
            p = os.path.join(self.new, name)
            if os.path.isfile(p) and not os.path.islink(p):
                os.chmod(p, stat.S_IMODE(os.stat(p).st_mode) & ~0o077)
        try:
            os.symlink(os.path.basename(self.new), self.old)
        except OSError:
            pass
        return [f"moved {old} to {new} (CARL's new name; the old path links to it)"]


def key_migration(home: str, target: str) -> KeyMigration:
    """The old folder's key (~/.config/llm-deploy/api-key) to CARL's key file (target)."""
    return KeyMigration(home, home_path(home, LEGACY_KEY_FILES[0]), target)
