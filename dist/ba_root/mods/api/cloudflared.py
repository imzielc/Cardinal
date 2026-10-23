"""downloads and keep-alives a cloudflared tunnel subprocess."""

import os
import platform
import shutil
import subprocess
import threading
import time
from pathlib import Path

from server import config
from server.storage import MODS_DIR

BIN_DIR = MODS_DIR / "api" / "bin"
PID_FILE = BIN_DIR / "cloudflared.pid"
DOWNLOAD_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/"
    "cloudflared-linux-{arch}"
)
RESPAWN_DELAY = 5

_started = False


def _arch() -> str:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "amd64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    raise RuntimeError(f"unsupported arch for cloudflared: {machine}")


def _ensure_binary() -> Path | None:
    existing = shutil.which("cloudflared")
    if existing:
        return Path(existing)

    BIN_DIR.mkdir(parents=True, exist_ok=True)
    target = BIN_DIR / "cloudflared"
    if target.exists():
        return target

    url = DOWNLOAD_URL.format(arch=_arch())
    print(f"[api] downloading cloudflared from {url}")
    try:
        subprocess.run(["curl", "-fsSL", url, "-o", str(target)], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("[api] cloudflared download failed")
        return None
    target.chmod(0o755)
    return target


def _read_running_pid() -> int | None:
    """returns the pid of a still-running cloudflared, or None."""
    try:
        pid = int(PID_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return None
    return pid


def _keep_alive(binary: Path, token: str) -> None:
    while True:
        try:
            proc = subprocess.Popen(
                [str(binary), "tunnel", "--no-autoupdate", "run", "--token", token],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            PID_FILE.parent.mkdir(parents=True, exist_ok=True)
            PID_FILE.write_text(str(proc.pid))
            proc.wait()
        except Exception as e:
            print(f"[api] cloudflared spawn failed: {e}")
        time.sleep(RESPAWN_DELAY)


def start() -> None:
    """spawns cloudflared in a daemon thread, unless one is already running
    from an earlier process (dev_reload preserves it via the pid file)."""
    global _started
    if _started:
        return

    token = str(config.api.get("cf_tunnel_token") or "").strip()
    if not token:
        return

    existing = _read_running_pid()
    if existing is not None:
        _started = True
        print(f"[api] cloudflared already running (pid {existing}), reusing it")
        return

    binary = _ensure_binary()
    if binary is None:
        return

    _started = True
    threading.Thread(
        target=_keep_alive, args=(binary, token), daemon=True, name="cloudflared"
    ).start()