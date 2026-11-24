import importlib.util
import json
import functools
import os
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import babase

from commands import command_line
from server import config
from server.clients import Client, all_clients
from server.storage import MODS_DIR
from server.utils import send

_process: subprocess.Popen | None = None
_spawn_lock = threading.Lock()


class SocketTunnel(threading.Thread):
    """a socket tunnel to communicate and receive commands from discord bot without interrupting the main game thread and process."""

    def __init__(self, port: int):
        super().__init__(daemon=True)
        self.port = port
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", self.port))

    def run(self) -> None:
        while self.running:
            try:
                raw_bytes, addr = self.sock.recvfrom(4096)
                if not self.running:
                    break

                data = json.loads(raw_bytes.decode("utf-8"))
                babase.pushcall(
                    functools.partial(self._handle_data, data, self.sock, addr),
                    from_other_thread=True,
                )
            except OSError:
                break
            except (ValueError, UnicodeDecodeError):
                continue

    def _handle_data(self, data: dict, sock: socket.socket, addr: tuple) -> None:
        action = data["action"]
        if action == "message":
            send(data["text"], sender=data["sender"])

        elif action == "command":
            account_id = data["account_id"]
            command = data["command"]
            client = Client(account_id=account_id)
            command_line(msg=command, client=client)

        elif action == "tournament_cancel":
            from tournament.manager import manager
            manager.cancel()

        elif action == "list":
            response = {"players": []}
            for client in all_clients():
                response["players"].append(
                    {
                        "name": client.display_string,
                        "client_id": client.client_id,
                    }
                )
            sock.sendto(json.dumps(response).encode("utf-8"), addr)

        elif action == "release_pending_rounds":
            from tournament.manager import manager

            result = {"released": False}
            if manager.brackets and not manager.active_match:
                try:
                    result["released"] = manager.brackets.release_pending_rounds()
                except Exception:
                    pass
            sock.sendto(json.dumps(result).encode("utf-8"), addr)

        elif action == "force_start_match":
            from tournament.manager import manager

            result = manager.force_start_match(data["match_key"])
            sock.sendto(json.dumps(result).encode("utf-8"), addr)

        elif action == "activate_bench":
            from tournament.manager import manager

            result = manager.activate_bench(
                match_key=data["match_key"],
                out_discord=data["out_discord"],
                in_discord=data["in_discord"],
                requested_by=data.get("requested_by", ""),
                reason=data.get("reason", ""),
            )
            sock.sendto(json.dumps(result).encode("utf-8"), addr)

        elif action == "force_bench":
            from tournament.manager import manager

            result = manager.force_bench_activation(
                match_key=data["match_key"],
                out_discord=data["out_discord"],
                in_discord=data["in_discord"],
                requested_by=data.get("requested_by", ""),
                reason=data.get("reason", ""),
            )
            sock.sendto(json.dumps(result).encode("utf-8"), addr)

        elif action == "resolve_bench":
            from tournament.manager import manager

            result = manager.resolve_bench_activation(
                match_key=data["match_key"],
                team_name=data["team"],
                action=data["resolution"],
            )
            sock.sendto(json.dumps(result).encode("utf-8"), addr)

        elif action == "expire_bench":
            from tournament.manager import manager

            result = {"expired": manager.expire_bench_activations()}
            sock.sendto(json.dumps(result).encode("utf-8"), addr)

    def shutdown(self) -> None:
        if not self.running:
            return
        self.running = False
        wake_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        wake_sock.sendto(b"{}", ("127.0.0.1", self.port))
        wake_sock.close()
        self.sock.close()

        if threading.current_thread() != self:
            self.join(timeout=1.0)


BOT_SCRIPT = Path(__file__).resolve().parent / "bot.py"
BOT_LOG = Path(__file__).resolve().parent / "bot.log"


def _running_pids() -> list[int]:
    """pids of running bot processes. empty where /proc is unavailable."""
    pids = []
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return pids

    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\\", b"/")
        except OSError:
            continue
        if b"discord_bot/bot.py" in cmdline:
            pids.append(int(entry.name))
    return pids


def bot_running() -> bool:
    """whether a bot process is already up, ours or one left from before."""
    if _process and _process.poll() is None:
        return True
    return bool(_running_pids())


def _spawn() -> None:
    """start the bot detached, so it outlives the game process."""
    global _process

    uv_path = shutil.which("uv")
    if not uv_path:
        print("[bot] uv not found, cannot spawn the bot")
        return

    if importlib.util.find_spec("discord") is None:
        subprocess.run([uv_path, "add", "discord.py"], check=True)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(MODS_DIR)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    print(f"[bot] starting discord bot (logs: {BOT_LOG})")
    with open(BOT_LOG, "a") as log:
        _process = subprocess.Popen(
            [uv_path, "run", str(BOT_SCRIPT)],
            env=env,
            start_new_session=True,
            stdout=log,
            stderr=log,
        )


def ensure_bot() -> None:
    """starts the bot only if none is running.

    a running bot is left alone on purpose: it survives game restarts and keeps
    the code it booted with until /bot-restart replaces it.
    """
    if config.discord.token == "ENTER-YOUR-BOT-TOKEN-HERE":
        return

    with _spawn_lock:
        if bot_running():
            return
        _spawn()


async def shutdown(self) -> None:
    """shut down the game-side socket tunnel only. the bot stays up."""
    if self.socket:
        self.socket.shutdown()
        self.socket = None


def setup(self) -> None:
    if config.discord.token == "ENTER-YOUR-BOT-TOKEN-HERE":
        print("Error: Put a valid bot token.")
        return

    threading.Thread(target=ensure_bot, daemon=True).start()

    port = config.discord.port
    self.socket = SocketTunnel(port)
    self.socket.start()

    babase.app.add_shutdown_task(shutdown(self))