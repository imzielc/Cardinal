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
from server.utils import send


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


async def shutdown(self) -> None:
    print("shutting down discord bot")
    if hasattr(self, "process") and self.process and self.process.poll() is None:
        try:
            self.process.terminate()
            self.process.wait(timeout=2.0)
        except:
            self.process.kill()
        self.process = None

    if self.socket:
        self.socket.shutdown()
        self.socket = None


def launch(self) -> None:
    uv_path = shutil.which("uv")

    if importlib.util.find_spec("discord") is None:
        subprocess.run([uv_path, "add", "discord.py"], check=True)

    script_path = str(Path(__file__).resolve().parent / "bot.py")
    cmd = [uv_path, "run", script_path]

    MODS_DIR = str(Path(__file__).resolve().parent.parent)
    env = os.environ.copy()
    env["PYTHONPATH"] = MODS_DIR
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    print("Starting discord bot.")
    self.process = subprocess.Popen(cmd, env=env)


def setup(self) -> None:
    if config.discord.token == "ENTER-YOUR-BOT-TOKEN-HERE":
        print("Error: Put a valid bot token.")
        return

    threading.Thread(target=launch, args=(self,), daemon=True).start()

    port = config.discord.port
    self.socket = SocketTunnel(port)
    self.socket.start()

    babase.app.add_shutdown_task(shutdown(self))