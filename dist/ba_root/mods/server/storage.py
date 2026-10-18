"""defines base storage class."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

# mods/ folder — where the code lives. storage.py -> server/ -> mods/.
MODS_DIR: Path = Path(__file__).resolve().parent.parent

# mods/database/ — where runtime state lives. kept inside the mods tree,
# as a clearly-named sibling of the code packages.
DATA_DIR: Path = MODS_DIR / "database"

BACKUP_COUNT = 3

# files to migrate into DATA_DIR on first boot, preserving .N.bak siblings.
# directory moves (SEASONS_DIR) are handled separately in migrate_to_data_dir.
_LEGACY_FILE_MOVES = [
    ("roles", "roles.json"),
    ("roles", "auth.json"),
    ("stats", "stats.json"),
    ("tournament", "linked.json"),
    ("tournament", "match_threads.json"),
]


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

        # rotate: old .2 -> .3, old .1 -> .2, current file -> .1.
        # all renames are metadata-only, cheap even on slow disks.
        if target_path.exists():
            base = str(target_path)
            oldest = Path(f"{base}.{BACKUP_COUNT}.bak")
            if oldest.exists():
                oldest.unlink()
            for n in range(BACKUP_COUNT - 1, 0, -1):
                src = Path(f"{base}.{n}.bak")
                dst = Path(f"{base}.{n + 1}.bak")
                if src.exists():
                    os.replace(src, dst)
            os.replace(target_path, Path(f"{base}.1.bak"))

        with target_path.open("w") as f:
            json.dump(data, f, indent=4)

        # update
        self._cache[target_path] = data
        self._mtime[target_path] = target_path.stat().st_mtime


# ---- one-time migration ----


def _move_file_and_backups(src: Path, dst: Path) -> bool:
    """moves a file and its .bak siblings from src to dst.

    each move is guarded: only when the source exists and the destination
    does not. a mid-run crash leaves the remaining files for the next run
    to pick up. returns True if anything was moved.
    """
    moved = False
    dst.parent.mkdir(parents=True, exist_ok=True)

    if src.exists() and not dst.exists():
        try:
            shutil.move(str(src), str(dst))
            moved = True
        except OSError:
            pass

    for n in range(1, BACKUP_COUNT + 1):
        s = Path(f"{src}.{n}.bak")
        d = Path(f"{dst}.{n}.bak")
        if s.exists() and not d.exists():
            try:
                shutil.move(str(s), str(d))
                moved = True
            except OSError:
                pass

    return moved


def _move_dir(src: Path, dst: Path) -> bool:
    """moves a directory from src to dst, only when source exists and destination does not."""
    if src.exists() and not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(src), str(dst))
            return True
        except OSError:
            pass
    return False


def migrate_to_data_dir() -> None:
    """one-time move of existing json data into DATA_DIR.

    idempotent — safe to run on every startup. each file and its .bak
    siblings are moved only when the source exists and the destination does
    not; the seasons directory moves only when the old location exists and
    the new one does not. a mid-run crash leaves the remaining files for
    the next run to pick up.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    for sub, filename in _LEGACY_FILE_MOVES:
        src = MODS_DIR / sub / filename
        dst = DATA_DIR / sub / filename
        if _move_file_and_backups(src, dst):
            print(f"[migration] moved {src.name} -> {dst}")

    seasons_src = MODS_DIR / "tournament" / "seasons"
    seasons_dst = DATA_DIR / "tournament" / "seasons"
    if _move_dir(seasons_src, seasons_dst):
        print(f"[migration] moved seasons/ -> {seasons_dst}")