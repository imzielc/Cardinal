"""watches team invitations, sends reminders, and cleans up the ones that expired."""

from traceback import format_exc

import discord
from discord.ext import commands, tasks

from discord_bot.events import cleanup_expired_teams
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
            # 1. expire anything past its deadline and clean up Discord.
            expired = await cleanup_expired_teams(self.bot, season_id)
            for team in expired:
                print(
                    f"Discord: expired team {team.id} "
                    f"(status={team.status.value}, "
                    f"expires_at={int(team.expires_at)})"
                )

            # 2. remind any team that is still pending verification.
            #    we re-read each team by id inside the loop (via get_team),
            #    so a verify that happens while an earlier reminder is being
            #    awaited is picked up on the very next iteration. this closes
            #    the race where a snapshot taken at loop start would still
            #    show a just-verified team as pending.
            registration = Registration(season_id=season_id)
            team_ids = list(registration.read().teams.keys())
            for team_id in team_ids:
                team = registration.get_team(team_id)
                if team is None:
                    # deleted during the loop (expired, dissolved, etc.)
                    continue
                if not registration.reminder_due(team):
                    continue
                if await self._send_reminder(team):
                    registration.mark_reminded(team.id)
        except Exception:
            print(f"Discord: invitation watcher failed:\n{format_exc()}")

    async def _send_reminder(self, team) -> bool:
        """sends a reminder into the team's thread.

        returns True if the reminder was sent OR could not be sent for a
        permanent reason (thread gone, nobody left to remind) — in both cases
        the caller marks it as handled. returns False only on a transient
        error, so the next cycle retries.
        """
        if not team.thread_id:
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
        """builds the reminder text and the list of discord ids to ping."""
        unaccepted = [m for m in team.members if not m.accepted]
        unverified = [m for m in team.members if not m.device_uuid]

        if not unaccepted and not unverified:
            return None, []

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

    @watch.before_loop
    async def before_watch(self) -> None:
        await self.bot.wait_until_ready()