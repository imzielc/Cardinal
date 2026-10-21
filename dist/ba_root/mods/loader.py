"""the mods loader."""

# ba_meta require api 9
import shutil
import os, sys
import subprocess
import threading
import time
from pathlib import Path
from traceback import format_exc

import babase
import bascenev1

RED = "\033[31m"
GREEN = "\033[32m"
RESET = "\033[0m"

AUTO_PULL_DELAY_SECONDS = 30
AUTO_PULL_TIMEOUT_SECONDS = 30

def _ensure_api() -> None:
    """spawns the api with `uv run` if it's not already up.

    the api restarts itself on crash; this only handles "never started".
    """
    import urllib.request
    from server import config
    from server.storage import MODS_DIR

    host = str(config.api.host)
    port = int(config.api.port)
    try:
        urllib.request.urlopen(
            f"http://{host}:{port}/v1/health", timeout=2
        ).close()
        return
    except Exception:
        pass

    uv = shutil.which("uv")
    if not uv:
        print("[api] uv not found, cannot spawn the api")
        return

    env = os.environ.copy()
    env["PYTHONPATH"] = str(MODS_DIR)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    log_path = MODS_DIR / "api" / "api.log"
    with open(log_path, "a") as log:
        subprocess.Popen(
            [uv, "run", str(MODS_DIR / "api" / "main.py")],
            cwd=str(MODS_DIR),
            env=env,
            start_new_session=True,
            stdout=log,
            stderr=log,
        )
    print(f"[api] spawned the api in the background (logs: {log_path})")

def _pull_updates() -> None:
    """pulls the latest commits in the background. new code applies next restart.

    runs in a detached, low-priority child process so it can't interfere with
    gameplay. quiet unless something new was actually pulled or git failed.
    """
    time.sleep(AUTO_PULL_DELAY_SECONDS)

    try:
        from server import config
        if not config.read().get("auto_pull", True):
            return
    except Exception:
        return

    git = shutil.which("git")
    if not git:
        return

    repo_root = Path(__file__).resolve().parents[3]
    cmd = [git, "pull", "--ff-only", "origin", "main"]
    nice = shutil.which("nice")
    if nice:
        cmd = [nice, "-n", "19"] + cmd

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            out, err = proc.communicate(timeout=AUTO_PULL_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            print("[auto-pull] timed out")
            return
    except Exception as e:
        print(f"[auto-pull] {e}")
        return

    if proc.returncode != 0:
        msg = (err or out or "").strip()
        if msg:
            print(f"[auto-pull] failed: {msg}")
        return

    out = (out or "").strip()
    if out and "Already up" not in out:
        print(f"[auto-pull] pulled:\n{out}")

def _startup_tasks() -> None:
    """runs the api check and the auto-pull in order, off the game thread."""
    from server import config

    if config.api.enable:
        _ensure_api()
    _pull_updates()

# ba_meta export babase.Plugin
class Load(babase.Plugin):
    def __init__(self) -> None:
        try:
            self._load()
            print(f"{GREEN}Success: All the mods have been loaded.{RESET}")
        except:
            print(
                f"{RED}Error: The mods could not be loaded, contact the mod author.{RESET}"
            )
            print(f"The error is as following:\n {format_exc()}")

    def _load(self) -> None:
        # necessary imports
        import commands
        import patches
        import roles
        import server
        import tournament
        import stats
        import utilities

        # boot storages.
        server.config.bootstrap()
        roles.roles.bootstrap()
        roles.auth.bootstrap()
        tournament.tournament.bootstrap()
        stats.stats.bootstrap()

        # the tournament manager.
        if int(tournament.tournament.active_season) > 0:
            from tournament import manager
            manager.manager.initialize(season_id=tournament.tournament.active_season)

        # load commands.
        commands.load()

        # load utilities.
        utilities.load()

        # load patches.
        patches.load()

        # discord bot
        if server.config.discord.enable:
            from discord_bot import launcher

            launcher.setup(self=self)

        # reload hooks.
        bascenev1.reload_hooks()

        # spawn api and auto-pull in the background.
        threading.Thread(target=_startup_tasks, daemon=True).start()