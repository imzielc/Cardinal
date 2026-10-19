"""defines base storage class."""

from __future__ import annotations

import json
import os
from pathlib import Path

# mods/ folder — where the code lives. storage.py -> server/ -> mods/.
MODS_DIR: Path = Path(__file__).resolve().parent.parent

# mods/database/ — where runtime state lives. kept inside the mods tree,
# as a clearly-named sibling of the code packages.
DATA_DIR: Path = MODS_DIR / "database"


class Storage:
    """storage class.

    by default, storage writes into DATA_DIR. callers that need a different
    root (Config, which lives at the project root next to mods_config.json)
    pass base_dir=MODS_DIR explicitly. callers that need an absolute target
    (SEASONS_DIR and its per-season subfolders) pass an absolute path as
    subfolder; it replaces base_dir instead of joining with it.
    """

    def __init__(
        self,
        filename: str,
        subfolder: str | Path | None = None,
        base_dir: Path | None = None,
    ) -> None:
        self._cache = {}
        self._mtime = {}
        if subfolder is not None:
            sub = Path(subfolder)
            if sub.is_absolute():
                # absolute path: use as-is, ignore base_dir.
                self.directory = sub
            else:
                self.directory = (base_dir or DATA_DIR) / sub
        else:
            self.directory = base_dir or DATA_DIR
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / filename

    def read(self, external_path: Path | None = None) -> dict:
        """reads the data from the file."""
        target_path = external_path or self.path
        try:
            current_mtime = target_path.stat().st_mtime
            if (
                target_path in self._cache
                and self._mtime.get(target_path) == current_mtime
            ):
                # return from cache
                return self._cache[target_path]
            with target_path.open("r") as f:
                data = json.load(f)
                self._cache[target_path] = data
                self._mtime[target_path] = current_mtime
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def commit(self, data: dict | list, external_path: Path | None = None) -> None:
        """commits the data to the file."""
        target_path = external_path or self.path

        # keep one backup: move the existing file to <name>.bak before writing.
        # os.replace is a metadata operation, cheap even on slow disks.
        if target_path.exists():
            backup_path = target_path.parent / (target_path.name + ".bak")
            os.replace(target_path, backup_path)

        with target_path.open("w") as f:
            json.dump(data, f, indent=4)

        # update
        self._cache[target_path] = data
        self._mtime[target_path] = target_path.stat().st_mtime