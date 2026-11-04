"""releases queued round starts during the nightly IST window, and runs
periodic housekeeping (bench activation expiry, swap vote closing)."""

from __future__ import annotations

import datetime
import time
from traceback import format_exc

import discord
from discord.ext import commands, tasks

from discord_bot.client import GameClient
from tournament import seasons
from tournament.brackets import Brackets
from tournament.bench_panels import bench_panels
from tournament.votes import Votes

RELEASE_START_HOUR = 0
RELEASE_END_HOUR = 8
IST_OFFSET = datetime.timedelta(hours=5, minutes=30)
CHECK_INTERVAL_MINUTES = 5
MAINTENANCE_INTERVAL_MINUTES = 30


def _now_ist() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc) + IST_OFFSET


def _in_window() -> bool:
    return RELEASE_START_HOUR <= _now_ist().hour < RELEASE_END_HOUR


class RoundScheduler(commands.Cog):
    """releases pending round starts during the nightly window."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.watch.start()

    async def cog_unload(self) -> None:
        self.watch.cancel()

    @tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
    async def watch(self) -> None:
        season_id = seasons.active_season
        if not int(season_id):
            return
        if not _in_window():
            return
        try:
            brackets = Brackets(season_id=season_id)
            if not brackets.read_meta().pending_round_start:
                return
        except Exception:
            return
        try:
            await GameClient().send_action("release_pending_rounds", season_id=season_id)
        except Exception:
            print(f"Discord: round release failed:\n{format_exc()}")

    @watch.before_loop
    async def before_watch(self) -> None:
        await self.bot.wait_until_ready()


class MaintenanceScheduler(commands.Cog):
    """expires bench activations and closes swap votes every 30 minutes."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.tick.start()

    async def cog_unload(self) -> None:
        self.tick.cancel()

    @tasks.loop(minutes=MAINTENANCE_INTERVAL_MINUTES)
    async def tick(self) -> None:
        season_id = seasons.active_season
        if not int(season_id):
            return

        try:
            await self._expire_bench(season_id)
        except Exception:
            print(f"Discord: bench expiry failed:\n{format_exc()}")

        try:
            await self._close_votes(season_id)
        except Exception:
            print(f"Discord: vote closing failed:\n{format_exc()}")

    async def _expire_bench(self, season_id: str) -> None:
        try:
            result = await GameClient().send_action("expire_bench", response=True)
        except Exception:
            return
        if not result:
            return

        for item in result.get("expired") or []:
            match_key = item.get("match_key")
            team = item.get("team")
            out_discord = item.get("out_discord_id")
            in_discord = item.get("in_discord_id")
            if not (match_key and team and out_discord):
                continue

            entry = bench_panels.get(season_id, match_key, team)
            if not entry:
                continue

            try:
                channel = self.bot.get_channel(entry["channel_id"]) or \
                    await self.bot.fetch_channel(entry["channel_id"])
                message = await channel.fetch_message(entry["message_id"])
                await message.edit(
                    content=(
                        f"**Substitution request**\n\n"
                        f"<@{out_discord}> didn't respond within 6 hours. "
                        f"<@{in_discord}> plays this match."
                    ),
                    view=None,
                )
            except Exception:
                pass
            bench_panels.remove(season_id, match_key, team)

    async def _close_votes(self, season_id: str) -> None:
        from discord_bot.events import on_swap_vote_closed

        store = Votes(season_id)
        now = time.time()
        for vote in store.open_votes():
            if now < vote.closes_at:
                continue
            try:
                closed = store.close(vote.id)
            except Exception:
                continue
            if not closed:
                continue
            await on_swap_vote_closed(
                self.bot,
                {"season_id": season_id, "vote_id": closed.id, "outcome": closed.state},
            )

    @tick.before_loop
    async def before_tick(self) -> None:
        await self.bot.wait_until_ready()