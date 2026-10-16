"""the mods loader."""

# ba_meta require api 9
from traceback import format_exc

import babase
import bascenev1

RED = "\033[31m"
GREEN = "\033[32m"
RESET = "\033[0m"


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

        # load commands.
        commands.load()

        # load utilities.
        utilities.load()

        # load patches.
        patches.load()

        # the tournament manager is initialized a tick after boot:
        # its init reads the brackets (which pulls pydantic in through
        # schema.from_dict), and the cold pydantic import is heavy enough
        # to trip the boot GC pass and the HostSession::Update() check.
        if int(tournament.tournament.active_season) > 0:
            bascenev1.AppTimer(1.5, self._init_manager)

        # discord bot
        if server.config.discord.enable:
            from discord_bot import launcher

            launcher.setup(self=self)

        # reload hooks.
        bascenev1.reload_hooks()

    def _init_manager(self) -> None:
        """loads the tournament manager a tick after boot."""
        import tournament
        from tournament import manager

        manager.manager.initialize(season_id=tournament.tournament.active_season)