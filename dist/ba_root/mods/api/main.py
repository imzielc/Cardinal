"""entry point: wraps the aiohttp app in a self-healing loop."""

import sys
from pathlib import Path

_MODS = Path(__file__).resolve().parent.parent
if str(_MODS) not in sys.path:
    sys.path.insert(0, str(_MODS))

import os
import signal
import threading
import time
from traceback import format_exc

from api.app import run_app

RETRY_DELAY = 10
WATCH_INTERVAL = 10.0
LOG_CHECK_INTERVAL = 60
MAX_LOG_BYTES = 2 * 1024 * 1024

_RELOAD = False
_RUNNING = False


def _snapshot(api_dir: Path) -> dict[str, float]:
    try:
        return {str(p): p.stat().st_mtime for p in api_dir.rglob("*.py")}
    except Exception:
        return {}


def _watch_changes(api_dir: Path) -> None:
    """watches api/ for .py changes. reloads only while dev_reload is on."""
    global _RELOAD
    prev = _snapshot(api_dir)
    was_enabled = None
    while True:
        time.sleep(WATCH_INTERVAL)

        from server import config
        enabled = bool(config.api.get("dev_reload", False))

        if enabled != was_enabled:
            print(f"[api] dev reload {'on' if enabled else 'off'}")
            was_enabled = enabled
            prev = _snapshot(api_dir)
            continue

        if not enabled:
            continue

        cur = _snapshot(api_dir)
        if cur == prev:
            continue

        changed = [
            Path(p).name for p in cur if prev.get(p) != cur[p]
        ] + [f"{Path(p).name} (gone)" for p in prev if p not in cur]
        print(f"[api] change detected ({', '.join(changed)}), reloading...")
        prev = cur
        _RELOAD = True
        if _RUNNING:
            os.kill(os.getpid(), signal.SIGINT)


def _should_rotate(api_dir: Path) -> bool:
    """rotates only when stdout is api.log (i.e. started by the game loader)."""
    if sys.stdout.isatty():
        return False
    try:
        target = Path(os.readlink("/proc/self/fd/1")).resolve()
        return target == (api_dir / "api.log").resolve()
    except (OSError, ValueError):
        return False


def _rotate_if_needed(log_path: Path) -> None:
    if not log_path.exists() or log_path.stat().st_size < MAX_LOG_BYTES:
        return
    old = log_path.with_suffix(".log.old")
    try:
        log_path.replace(old)
    except OSError:
        return
    new_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    os.dup2(new_fd, 1)
    os.dup2(new_fd, 2)
    os.close(new_fd)
    print(f"[api] log rotated, previous kept at {old.name}")


def _log_rotator(log_path: Path) -> None:
    while True:
        time.sleep(LOG_CHECK_INTERVAL)
        try:
            _rotate_if_needed(log_path)
        except Exception:
            pass


def _wait_for_reload(timeout: float) -> None:
    """sleeps up to timeout, returns early if _RELOAD is set."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not _RELOAD:
        time.sleep(0.1)


def _run() -> None:
    global _RUNNING
    _RUNNING = True
    try:
        run_app()
    finally:
        _RUNNING = False


def main() -> None:
    """runs the app until it crashes, gets told to exit, or reloads."""
    api_dir = Path(__file__).resolve().parent

    threading.Thread(
        target=_watch_changes, args=(api_dir,), daemon=True, name="api-watcher"
    ).start()

    if _should_rotate(api_dir):
        threading.Thread(
            target=_log_rotator,
            args=(api_dir / "api.log",),
            daemon=True,
            name="api-log-rotator",
        ).start()

    while True:
        try:
            _run()
        except (KeyboardInterrupt, SystemExit):
            pass
        except Exception:
            print(f"[api] crashed, restarting in {RETRY_DELAY}s:\n{format_exc()}")
            _wait_for_reload(RETRY_DELAY)
            if not _RELOAD:
                continue

        if _RELOAD:
            print("[api] reloading with fresh imports...")
            os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve())])

        return


if __name__ == "__main__":
    main()