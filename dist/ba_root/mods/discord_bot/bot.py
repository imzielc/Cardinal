"""bot.py — entry point. wires up the cogs and runs the client."""

import asyncio
import logging
from traceback import format_exc

from discord import Activity, ActivityType, Intents, Interaction, app_commands
from discord.ext import commands

from discord_bot.cogs.account import AccountCommands
from discord_bot.cogs.general import GeneralCommands
from discord_bot.cogs.invitation_watcher import TeamInvitationWatcher
from discord_bot.cogs.tournament import TournamentCommands
from discord_bot.events import EventReceiver
from discord_bot.held_posts import HeldPosts
from discord_bot.ui import RegisterView, TeamInvitationView
from server import config
from server.enums import TeamStatus
from tournament import tournament
from tournament.registration import Registration


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

    async def setup_hook(self) -> None:
        self._restore_invitations()
        # the registration panel's button must keep working after a restart.
        self.add_view(RegisterView())
        # start listening for game-side events.
        self._events = EventReceiver(self, asyncio.get_running_loop())
        self._events.start()
        self.tree.on_error = self.on_app_cmd_error
        await self.add_cog(GeneralCommands(self))
        await self.add_cog(AccountCommands(self))
        await self.add_cog(TournamentCommands(self))
        await self.add_cog(HeldPosts(self))
        await self.add_cog(TeamInvitationWatcher(self))
        # rename pre-existing team roles to the "[Team] <name>" format.
        asyncio.create_task(self._run_startup_migrations())

    def _restore_invitations(self) -> None:
        """re-registers the invitation buttons of the pending teams, so they work after a restart."""
        season_id = tournament.active_season
        if not int(season_id):
            return

        for team_id, team in Registration(season_id=season_id).read().teams.items():
            if team.status == TeamStatus.IN_INVITATION:
                self.add_view(TeamInvitationView(team_id=team_id, season_id=season_id))

    async def _run_startup_migrations(self) -> None:
        """startup work that needs a fully-ready bot. idempotent, safe to run every start."""
        try:
            await self.wait_until_ready()
        except Exception:
            return
        try:
            from discord_bot.events import migrate_team_roles
            await migrate_team_roles(self)
        except Exception as e:
            print(f"Discord: role migration failed: {e}")

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

    async def close(self) -> None:
        """stop the event listener before the bot shuts down."""
        if self._events:
            self._events.shutdown()
            self._events = None
        await super().close()


if __name__ == "__main__":
    bot = DiscordBot()
    bot.run(token=config.discord.token, log_level=logging.WARNING)