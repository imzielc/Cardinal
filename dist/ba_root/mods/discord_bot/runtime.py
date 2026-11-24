"""process-level bits for the bot: where its script lives, and how it ends."""

import os
import shutil
import sys
from pathlib import Path

BOT_SCRIPT = Path(__file__).resolve().parent / "bot.py"

_STOP = False


def stop() -> None:
    """make the next replace() exit instead of reloading."""
    global _STOP
    _STOP = True


def replace() -> None:
    """end this process: reload the bot with fresh code, or exit if stopped.

    never returns.
    """
    if _STOP:
        print("[bot] stopped")
        os._exit(0)

    uv = shutil.which("uv")
    if not uv:
        print("[bot] uv not found, cannot reload")
        os._exit(1)

    # same command the game uses to start us, so the environment matches.
    print("[bot] reloading with fresh imports...")
    os.execv(uv, [uv, "run", str(BOT_SCRIPT)])