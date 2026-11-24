"""bot.py — entry point. wires up the cogs and runs the client."""

import asyncio
import logging
import time
from traceback import format_exc

from discord import Activity, ActivityType, Intents, Interaction, app_commands
from discord.ext import commands

from discord_bot import runtime
from discord_bot.cogs.account import AccountCommands
from discord_bot.cogs.config import ConfigCommands
from discord_bot.cogs.general import GeneralCommands
from discord_bot.cogs.watcher import TeamInvitationWatcher
from discord_bot.cogs.scheduler import MaintenanceScheduler, RoundScheduler
from discord_bot.cogs.tournament import TournamentCommands
from discord_bot.events import EventReceiver, reconcile_match_threads
from discord_bot.held_posts import HeldPosts
from discord_bot.ui import (
    BenchPanelView,
    ReadyPanelView,
    RegisterView,
    SwapVoteView,
    TeamInvitationView,
)
from server import config
from server.enums import TeamStatus
from tournament import seasons
from tournament.panels import bench_panels, ready_panels
from tournament.registration import Registration
from tournament.votes import Votes

RETRY_DELAY = 10


class DiscordBot(commands.Bot):
    def __init__(self) -> None:
        intents = Intents.default()
        intents.members = True
        super().__init__(
            command_prefix=[],
            intents=intents,
            owner_id=config.discord.owner_id,
        )
        self._events: EventReceiver | None = None
        self._reconciled = False

    async def setup_hook(self) -> None:
        self._restore_invitations()
        self._restore_ready_panels()
        self._restore_bench_panels()
        self._restore_swap_votes()
        self.add_view(RegisterView())
        self._events = EventReceiver(self, asyncio.get_running_loop())
        self._events.start()
        self.tree.on_error = self.on_app_cmd_error
        await self.add_cog(GeneralCommands(self))
        await self.add_cog(AccountCommands(self))
        await self.add_cog(TournamentCommands(self))
        await self.add_cog(ConfigCommands(self))
        await self.add_cog(HeldPosts(self))
        await self.add_cog(TeamInvitationWatcher(self))
        await self.add_cog(RoundScheduler(self))
        await self.add_cog(MaintenanceScheduler(self))

    def _restore_invitations(self) -> None:
        season_id = seasons.active_season
        if not int(season_id):
            return

        for team_id, team in Registration(season_id=season_id).read().teams.items():
            if team.status == TeamStatus.IN_INVITATION:
                self.add_view(TeamInvitationView(team_id=team_id, season_id=season_id))

    def _restore_ready_panels(self) -> None:
        season_id = str(seasons.active_season)
        if not int(season_id):
            return
        for match_key, entry in ready_panels.items(season_id):
            self.add_view(
                ReadyPanelView(
                    season_id,
                    match_key,
                    can_force=bool(entry.get("can_force", False)),
                )
            )

    def _restore_bench_panels(self) -> None:
        season_id = str(seasons.active_season)
        if not int(season_id):
            return
        for match_key, team, entry in bench_panels.items(season_id):
            self.add_view(
                BenchPanelView(
                    season_id,
                    match_key,
                    team,
                    out_discord_id=str(entry.get("out_discord_id") or ""),
                )
            )

    def _restore_swap_votes(self) -> None:
        season_id = str(seasons.active_season)
        if not int(season_id):
            return
        for vote in Votes(season_id).open_votes():
            if vote.message_id:
                self.add_view(SwapVoteView(season_id, vote.id, vote.team_id))

    async def on_app_cmd_error(
        self, interaction: Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.CheckFailure):
            return
        print(f"Discord: An error occurred while executing the command: {format_exc()}")

    async def on_ready(self):
        print(f"Discord: logged in as {self.user}")
        await self.change_presence(
            activity=Activity(type=ActivityType.listening, name="Zzzzzz....")
        )
        for guild in self.guilds:
            self.tree.clear_commands(guild=guild)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)

        # the game announces a round once; recover threads it sent before we were up.
        if not self._reconciled:
            self._reconciled = True
            await reconcile_match_threads(self)

    async def close(self) -> None:
        if self._events:
            self._events.shutdown()
            self._events = None
        await super().close()


def main() -> None:
    """runs the bot, restarting in-process on crashes and reloading on request."""
    while True:
        bot = DiscordBot()
        try:
            bot.run(token=config.discord.token, log_level=logging.WARNING)
        except (KeyboardInterrupt, SystemExit):
            # someone signalled the process: treat it as a stop, not a reload.
            runtime.stop()
        except Exception:
            # keep running the code that is already loaded; only /bot-restart
            # is allowed to pull in new code.
            print(f"[bot] crashed, restarting in {RETRY_DELAY}s:\n{format_exc()}")
            time.sleep(RETRY_DELAY)
            continue

        runtime.replace()


if __name__ == "__main__":
    main()
