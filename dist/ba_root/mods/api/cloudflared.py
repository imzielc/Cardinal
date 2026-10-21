"""downloads and keep-alives a cloudflared tunnel subprocess."""

import platform
import shutil
import subprocess
import threading
import time
from pathlib import Path

from server import config
from server.storage import MODS_DIR

BIN_DIR = MODS_DIR / "api" / "bin"
DOWNLOAD_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/"
    "cloudflared-linux-{arch}"
)
RESPAWN_DELAY = 5


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


def _keep_alive(binary: Path, token: str) -> None:
    while True:
        try:
            proc = subprocess.Popen(
                [str(binary), "tunnel", "--no-autoupdate", "run", "--token", token],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            proc.wait()
        except Exception as e:
            print(f"[api] cloudflared spawn failed: {e}")
        time.sleep(RESPAWN_DELAY)


def start() -> None:
    """spawns cloudflared in a daemon thread. no-op when the token is empty."""
    token = str(config.api.get("cf_tunnel_token") or "").strip()
    if not token:
        return
    binary = _ensure_binary()
    if binary is None:
        return
    threading.Thread(
        target=_keep_alive, args=(binary, token), daemon=True, name="cloudflared"
    ).start()