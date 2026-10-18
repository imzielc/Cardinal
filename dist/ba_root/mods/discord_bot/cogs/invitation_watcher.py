"""watches team invitations and cleans up the ones that expired."""

from traceback import format_exc

import discord
from discord.ext import commands, tasks

from discord_bot.ui import allowed_mentions_for
from tournament import tournament
from tournament.registration import Registration


class TeamInvitationWatcher(commands.Cog):
    """cleans up team invitations that have expired."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.watch.start()

    async def cog_unload(self) -> None:
        self.watch.cancel()

    @tasks.loop(minutes=5)
    async def watch(self) -> None:
        season_id = tournament.active_season
        if not int(season_id):
            return
        try:
            registration = Registration(season_id=season_id)
            season = tournament.get_season(season_id)
            for candidate in registration.expired_teams():
                expired = registration.expire_team(candidate.id)
                if expired is None:
                    # verified teams are protected by expire_team; nothing to do.
                    continue
                print(
                    f"Discord: expired team {expired.id} "
                    f"(status={expired.status.value}, "
                    f"expires_at={int(expired.expires_at)})"
                )
                try:
                    await self._cleanup(expired, season)
                except Exception:
                    print(f"Discord: could not clean up expired invitation {expired.id}:\n{format_exc()}")
        except Exception:
            print(f"Discord: invitation watcher failed:\n{format_exc()}")

    async def _cleanup(self, team, season) -> None:
        channel = None
        if team.thread_id:
            try:
                channel = self.bot.get_channel(team.thread_id)
                if channel is None:
                    channel = await self.bot.fetch_channel(team.thread_id)
            except discord.HTTPException:
                channel = None

        # reach the guild through the thread, or fall back to the first guild we know.
        guild = getattr(channel, "guild", None) if channel else None
        if guild is None and self.bot.guilds:
            guild = self.bot.guilds[0]

        if season is not None and season.participant_role_id and guild is not None:
            role = guild.get_role(season.participant_role_id)
            if role is not None:
                for member in team.members:
                    try:
                        user = guild.get_member(int(member.discord_id))
                    except (TypeError, ValueError):
                        continue
                    if user and role in user.roles:
                        try:
                            await user.remove_roles(role)
                        except discord.HTTPException:
                            pass

        # delete the team role if it exists.
        if team.role_id and guild is not None:
            team_role = guild.get_role(team.role_id)
            if team_role:
                try:
                    await team_role.delete()
                except discord.HTTPException:
                    pass

        # the attempt is dead, throw its thread away.
        if channel is not None:
            try:
                await channel.delete()
            except discord.HTTPException:
                pass

    @watch.before_loop
    async def before_watch(self) -> None:
        await self.bot.wait_until_ready()