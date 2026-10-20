"""the mods loader."""

# ba_meta require api 9
import shutil
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

        # pull updates in the background after boot. this cycle keeps running
        # the code it already loaded; the pulled code applies next restart.
        threading.Thread(target=_pull_updates, daemon=True).start()