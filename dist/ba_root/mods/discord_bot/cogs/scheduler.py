"""releases queued round starts during the nightly IST window."""

from __future__ import annotations

import datetime
from traceback import format_exc

import discord
from discord.ext import commands, tasks

from discord_bot.client import GameClient
from tournament import tournament
from tournament.brackets import Brackets

# rounds only release when the current IST hour is in [0, 8).
RELEASE_START_HOUR = 0
RELEASE_END_HOUR = 8
IST_OFFSET = datetime.timedelta(hours=5, minutes=30)
CHECK_INTERVAL_MINUTES = 5


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
        season_id = tournament.active_season
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