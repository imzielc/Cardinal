"""notify.py — fires state-change events to the discord bot.

game side only. fire-and-forget UDP, no acks, no retries, no game imports.
the game and bot live and die together, so a lost event during startup is
not worth chasing.
"""

from __future__ import annotations

import json
import socket

from server import config


def _port() -> int:
    try:
        return int(config.discord.get("bot_port", 9002))
    except (TypeError, ValueError):
        return 0


def send_event(event: str, **kwargs) -> bool:
    """sends an event to the bot. returns whether the datagram was sent."""
    port = _port()
    if not port:
        return False
    payload = json.dumps({"event": event, **kwargs}).encode("utf-8")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(payload, ("127.0.0.1", port))
        return True
    except OSError:
        return False