"""defines base storage class."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import threading
from pathlib import Path

try:
    import fcntl
except ImportError:
    # not on linux, no cross-process lock.
    fcntl = None

# mods/ folder — where the code lives. storage.py -> server/ -> mods/.
MODS_DIR: Path = Path(__file__).resolve().parent.parent

# mods/database/ — where runtime state lives. kept inside the mods tree,
# as a clearly-named sibling of the code packages.
DATA_DIR: Path = MODS_DIR / "database"

# per-thread set of files we already hold the lock on. flock is reentrant
# across the same thread only when it uses the same file description — we
# use a fresh fd per call, so track depth ourselves to avoid self-deadlock.
_lock_state = threading.local()


@contextlib.contextmanager
def _file_lock(path: Path):
    """cross-process exclusive lock on the file at path. re-entrant within a thread."""
    if fcntl is None:
        yield
        return
    key = str(path.resolve())
    held = getattr(_lock_state, "held", None)
    if held is None:
        held = {}
        _lock_state.held = held
    if held.get(key):
        # already held by this thread, no second flock.
        held[key] += 1
        try:
            yield
        finally:
            held[key] -= 1
        return
    # lock a sidecar file, so renames of the target never lose the lock.
    lock_path = path.parent / (path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        held[key] = 1
        try:
            yield
        finally:
            held[key] = 0
            fcntl.flock(lock, fcntl.LOCK_UN)


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

        with _file_lock(target_path):
            # keep one backup: copy the current file to <name>.bak. copy, not
            # move, so a concurrent reader never sees the file missing mid-swap.
            if target_path.exists():
                backup_path = target_path.parent / (target_path.name + ".bak")
                shutil.copy2(target_path, backup_path)

            # write to a temp file, then atomically replace. readers always see
            # the old or the new content, never a half-written file.
            temp_path = target_path.parent / (target_path.name + ".tmp")
            with temp_path.open("w") as f:
                json.dump(data, f, indent=4)
            os.replace(temp_path, target_path)

        # update
        self._cache[target_path] = data
        self._mtime[target_path] = target_path.stat().st_mtime