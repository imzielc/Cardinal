"""bot.py — entry point. wires up the cogs and runs the client."""

import asyncio
import logging
from traceback import format_exc

from discord import Activity, ActivityType, Intents, Interaction, NotFound, app_commands
from discord.ext import commands

from discord_bot.cogs.account import AccountCommands
from discord_bot.cogs.config import ConfigCommands
from discord_bot.cogs.general import GeneralCommands
from discord_bot.cogs.watcher import TeamInvitationWatcher
from discord_bot.cogs.scheduler import RoundScheduler
from discord_bot.cogs.tournament import TournamentCommands
from discord_bot.events import EventReceiver, _delete_thread, on_round_started
from discord_bot.held_posts import HeldPosts
from discord_bot.ui import RegisterView, TeamInvitationView, ReadyPanelView
from tournament.ready_panels import ready_panels
from server import config
from server.enums import TeamStatus
from tournament import seasons
from tournament.brackets import Brackets
from tournament.match_threads import match_threads
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
        self._reconciled: bool = False
    
    async def setup_hook(self) -> None:
        self._restore_invitations()
        self._restore_ready_panels()
        # the registration panel's button must keep working after a restart.
        self.add_view(RegisterView())
        # start listening for game-side events.
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

    async def _reconcile_threads(self) -> None:
        """brings match_threads back in sync with the season's live matches.

        deletes threads whose match is gone, creates threads for live matches
        that have none. best-effort: any failure leaves state untouched.
        """
        season_id = str(seasons.active_season)
        if not int(season_id):
            return
        try:
            brackets = Brackets(season_id=season_id)
            if not brackets.read_meta().started:
                return
            live = brackets.list_matches()
            payload = brackets._build_round_payload([])
        except Exception:
            return

        live_keys = set(live.keys())
        tracked = match_threads.get(season_id)

        kept: dict[str, int] = {}
        for key, tid in tracked.items():
            if key not in live_keys:
                await _delete_thread(self, tid)
                continue
            try:
                await self.fetch_channel(int(tid))
            except NotFound:
                continue
            except Exception:
                kept[key] = tid
                continue
            kept[key] = tid
        if kept != tracked:
            match_threads.set(season_id, kept)

        missing = [m for m in payload if m["key"] not in kept]
        if missing:
            await on_round_started(self, {"season_id": season_id, "matches": missing})

    def _restore_invitations(self) -> None:
        """re-registers the invitation buttons of the pending teams, so they work after a restart."""
        season_id = seasons.active_season
        if not int(season_id):
            return

        for team_id, team in Registration(season_id=season_id).read().teams.items():
            if team.status == TeamStatus.IN_INVITATION:
                self.add_view(TeamInvitationView(team_id=team_id, season_id=season_id))

    def _restore_ready_panels(self) -> None:
        """re-registers the ready panel buttons that survived a restart."""
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

        if not self._reconciled:
            try:
                await self._reconcile_threads()
                self._reconciled = True
            except Exception:
                print(f"Discord: thread reconciliation failed:\n{format_exc()}")

    async def close(self) -> None:
        """stop the event listener before the bot shuts down."""
        if self._events:
            self._events.shutdown()
            self._events = None
        await super().close()


if __name__ == "__main__":
    bot = DiscordBot()
    bot.run(token=config.discord.token, log_level=logging.WARNING)