"""watches team invitations, sends reminders, and cleans up the ones that expired."""

from traceback import format_exc

import discord
from discord.ext import commands, tasks

from discord_bot.events import promote_waitlist_one
from discord_bot.ui import _expiry_label
from tournament import tournament
from tournament.registration import Registration


class TeamInvitationWatcher(commands.Cog):
    """watches active invitations, reminds pending teams, and cleans up expired ones."""

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

            # 1. expire anything past its deadline.
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
                # a slot just opened: promote the top of the waitlist if any.
                await promote_waitlist_one(self.bot, season_id)

            # 2. remind any team that is still pending verification.
            #    (registration.read() re-reads from disk after expire_team above.)
            for team in registration.read().teams.values():
                if not registration.reminder_due(team):
                    continue
                if await self._send_reminder(team, season):
                    registration.mark_reminded(team.id)
        except Exception:
            print(f"Discord: invitation watcher failed:\n{format_exc()}")

    async def _send_reminder(self, team, season) -> bool:
        """sends a reminder into the team's thread.

        returns True if the reminder was sent OR could not be sent for a
        permanent reason (thread gone, nobody left to remind) — in both cases
        the caller marks it as handled. returns False only on a transient
        error, so the next cycle retries.
        """
        if not team.thread_id:
            # no thread to send into; nothing to do.
            return True

        try:
            thread = self.bot.get_channel(team.thread_id) or await self.bot.fetch_channel(team.thread_id)
        except discord.NotFound:
            return True
        except discord.HTTPException:
            return False
        if thread is None:
            return True

        content, targets = self._reminder_message(team)
        if content is None:
            return True

        mentions = discord.AllowedMentions(
            users=[discord.Object(id=int(did)) for did in targets],
            roles=False,
            everyone=False,
        )
        try:
            await thread.send(content, allowed_mentions=mentions)
        except discord.NotFound:
            return True
        except discord.HTTPException:
            return False
        return True

    def _reminder_message(self, team) -> tuple[str | None, list[str]]:
        """builds the reminder text and the list of discord ids to ping.

        returns (None, []) when there is nobody to remind (team is done).
        """
        unaccepted = [m for m in team.members if not m.accepted]
        unverified = [m for m in team.members if not m.device_uuid]

        if not unaccepted and not unverified:
            return None, []

        # ping anyone who still needs to act, plus the captain, so the
        # person running the team always knows it is at risk.
        targets: list[str] = []
        seen: set[str] = set()
        for member in list(unaccepted) + list(unverified):
            if member.discord_id in seen:
                continue
            seen.add(member.discord_id)
            targets.append(member.discord_id)
        if team.captain not in seen:
            targets.append(team.captain)

        mention_line = " ".join(f"<@{did}>" for did in targets)
        expiry = f"\n{_expiry_label(team.expires_at)}" if team.expires_at > 0 else ""

        if unaccepted:
            body = (
                f"{mention_line} — invitation for team **{team.id}** is still pending.\n"
                f"Use the message above to accept or decline.{expiry}"
            )
        else:
            body = (
                f"{mention_line} — everyone joined team **{team.id}**, "
                f"but verification isn't complete yet.\n"
                f"Join the game server and run `/verify` in-game.{expiry}"
            )
        return body, targets

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