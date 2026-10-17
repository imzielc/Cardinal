"""events.py — receives state-change events from the game over UDP and dispatches them.

the game fires `send_event(name, **kwargs)` from `server.notify`. we bind a port,
listen, and shove every payload into the asyncio loop as a task. handlers live here.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading

import discord
from discord.ext import commands

from discord_bot.ui import TeamInvitationView, refresh_register_panel
from server import config
from server.enums import TeamStatus
from tournament import tournament
from tournament.match_threads import match_threads
from tournament.registration import Registration


class EventReceiver(threading.Thread):
    """listens on the bot port, dispatches incoming events as asyncio tasks."""

    def __init__(self, bot: commands.Bot, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__(daemon=True)
        self.bot = bot
        self.loop = loop
        self.running = True
        self.enabled = False
        self._tasks: set = set()
        try:
            self.port = int(config.discord.get("bot_port", 9002))
        except (TypeError, ValueError):
            self.port = 9002
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind(("127.0.0.1", self.port))
            self.enabled = True
        except OSError as e:
            print(f"Discord: could not bind event port {self.port}: {e}")
            try:
                self.sock.close()
            except OSError:
                pass

    def run(self) -> None:
        if not self.enabled:
            return
        while self.running:
            try:
                data, _ = self.sock.recvfrom(8192)
            except OSError:
                break
            try:
                payload = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            name = payload.get("event")
            handler = HANDLERS.get(name)
            if handler is None:
                continue
            try:
                self.loop.call_soon_threadsafe(self._dispatch, handler, payload)
            except RuntimeError:
                break

    def _dispatch(self, handler, payload: dict) -> None:
        try:
            task = self.loop.create_task(handler(self.bot, payload))
        except RuntimeError:
            return
        self._tasks.add(task)
        task.add_done_callback(self._on_done)

    def _on_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            print(f"Discord: event handler failed: {exc}")

    def shutdown(self) -> None:
        if not self.running:
            return
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass


# ---- shared cleanups ----


async def _delete_thread(bot: commands.Bot, thread_id: int) -> None:
    """deletes one thread by id, silent on any failure."""
    if not thread_id:
        return
    try:
        thread = bot.get_channel(thread_id) or await bot.fetch_channel(thread_id)
        await thread.delete()
    except Exception:
        pass


def _find_role(bot: commands.Bot, role_id: int):
    """finds a role by id across the bot's guilds, or returns None."""
    if not role_id:
        return None
    for guild in bot.guilds:
        role = guild.get_role(role_id)
        if role is not None:
            return role
    return None


async def _delete_role(bot: commands.Bot, role_id: int) -> None:
    """deletes a role by id, silent on any failure."""
    role = _find_role(bot, role_id)
    if role is None:
        return
    try:
        await role.delete()
    except discord.HTTPException:
        pass


async def cleanup_season(bot: commands.Bot, season_id: str) -> None:
    """deletes every discord trace of a season: participant role, group roles, team roles,
    invitation threads, and match threads."""
    # 1. the participant role (created at /tournament create).
    try:
        season = tournament.get_season(season_id)
    except Exception:
        season = None
    if season:
        await _delete_role(bot, season.participant_role_id)

    # 2. group roles (created at /tournament start when the group stage runs).
    try:
        from tournament.brackets import Brackets
        gs = Brackets(season_id=season_id).read_gs()
        for group in gs.groups.values():
            await _delete_role(bot, group.role_id)
    except Exception:
        pass

    # 3. team roles and invitation threads.
    try:
        registration = Registration(season_id=season_id).read()
    except Exception:
        registration = None
    if registration:
        for team in registration.teams.values():
            await _delete_thread(bot, team.thread_id)
            await _delete_role(bot, team.role_id)

    # 4. match threads.
    for tid in match_threads.get(season_id).values():
        await _delete_thread(bot, tid)
    match_threads.clear()


# ---- one-shot migration ----


def _message_has_invitation_buttons(message) -> bool:
    """checks whether a message carries a tiv; accept/decline button anywhere in its tree."""
    def walk(item) -> bool:
        cid = getattr(item, "custom_id", None)
        if isinstance(cid, str) and cid.startswith("tiv;"):
            return True
        children = getattr(item, "children", None) or []
        return any(walk(child) for child in children)

    try:
        for item in (message.components or []):
            if walk(item):
                return True
    except Exception:
        pass
    return False


async def migrate_invitation_views(bot: commands.Bot) -> None:
    """one-shot: fixes teams whose invitation message id was never recorded, and
    re-renders any that are past the invitation stage so their message matches
    the current design.

    idempotent — running this on every startup is safe and cheap. teams with
    a recorded id are skipped.
    """
    season_id = tournament.active_season
    if not int(season_id or "0"):
        return

    try:
        registration = Registration(season_id=season_id)
        db = registration.read()
    except Exception:
        return

    for team in db.teams.values():
        if not team.thread_id:
            continue
        if team.invitation_message_id != 0:
            continue

        try:
            thread = bot.get_channel(team.thread_id) or await bot.fetch_channel(team.thread_id)
        except Exception:
            continue
        if thread is None:
            continue

        # find the most recent invitation message in the thread.
        found = None
        try:
            async for message in thread.history(limit=100):
                if message.author.id != bot.user.id:
                    continue
                if _message_has_invitation_buttons(message):
                    found = message
                    break
        except Exception:
            pass

        if found is None:
            continue

        # record the id so future events can edit this message.
        registration.set_invitation_message(team.id, found.id)

        # only re-render teams that are past the invitation stage.
        if team.status not in (TeamStatus.UNVERIFIED, TeamStatus.VERIFIED):
            continue

        was_archived = getattr(thread, "archived", False)
        if was_archived:
            try:
                await thread.edit(archived=False)
            except Exception:
                pass

        try:
            await found.edit(
                view=TeamInvitationView(team_id=team.id, season_id=season_id),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            pass


# ---- handlers ----


async def on_team_verified(bot: commands.Bot, payload: dict) -> None:
    """fired on every member verification (not just the last).

    refreshes the panel and the invitation view so the reader can see who
    is still left to verify. once the team is fully verified the view
    renders in its final, locked-down state.
    """
    season_id = str(payload.get("season_id") or "")
    team_id = str(payload.get("team_id") or "")
    if not int(season_id or "0") or not team_id:
        return

    # the panel's team counts change only on the final verify, but refreshing
    # on every verify is cheap and keeps everything in sync.
    await refresh_register_panel(bot)

    try:
        registration = Registration(season_id=season_id)
        team = registration.get_team(team_id)
    except Exception:
        return
    if not team or not team.thread_id:
        return

    try:
        thread = bot.get_channel(team.thread_id) or await bot.fetch_channel(team.thread_id)
    except Exception:
        thread = None
    if thread is None:
        return

    view = TeamInvitationView(team_id=team_id, season_id=season_id)

    if team.invitation_message_id:
        try:
            message = await thread.fetch_message(team.invitation_message_id)
            await message.edit(
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            pass
    else:
        # fallback for teams that predate the tracked message id and have not
        # been caught by the migration yet. records the id so this is one-shot.
        try:
            message = await thread.send(
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            registration.set_invitation_message(team_id, message.id)
        except Exception:
            pass


async def on_round_started(bot: commands.Bot, payload: dict) -> None:
    """a new round of matches is playable. refresh the private match threads."""
    season_id = str(payload.get("season_id") or "")
    if not int(season_id or "0"):
        return

    # tear down the old round's threads.
    for tid in match_threads.get(season_id).values():
        await _delete_thread(bot, tid)
    match_threads.clear()

    matches = payload.get("matches") or []
    if not matches:
        return

    try:
        channel_id = int(config.discord.get("brackets_channel_id", 0))
    except (TypeError, ValueError):
        channel_id = 0
    if not channel_id:
        return

    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    except Exception:
        return

    registration = Registration(season_id=season_id).read()
    new_threads: dict[str, int] = {}

    try:
        for match in matches:
            key = str(match.get("key") or "")
            team1_name = match.get("team1") or "TBD"
            team2_name = match.get("team2") or "TBD"
            if not key or team1_name == "TBD" or team2_name == "TBD":
                continue

            member_ids: list[int] = []
            role_ids: list[int] = []
            for tname in (team1_name, team2_name):
                team = registration.teams.get(tname)
                if not team:
                    continue
                if team.role_id:
                    role_ids.append(int(team.role_id))
                for m in team.members:
                    try:
                        member_ids.append(int(m.discord_id))
                    except (TypeError, ValueError):
                        pass

            try:
                thread = await channel.create_thread(
                    name=f"{team1_name} vs {team2_name}"[:95],
                    type=discord.ChannelType.private_thread,
                    invitable=False,
                    auto_archive_duration=1440,
                )
            except Exception as e:
                print(f"Discord: could not create match thread: {e}")
                continue

            for mid in member_ids:
                try:
                    await thread.add_user(discord.Object(id=mid))
                except Exception:
                    pass

            pings = " ".join(f"<@&{rid}>" for rid in role_ids)
            header = f"{pings}\n" if pings else ""
            try:
                await thread.send(
                    f"{header}**{team1_name} vs {team2_name}** — coordinate your match timings here.",
                    allowed_mentions=discord.AllowedMentions(
                        roles=[discord.Object(id=rid) for rid in role_ids],
                        users=False,
                        everyone=False,
                    ),
                )
            except Exception:
                pass

            new_threads[key] = thread.id
    finally:
        match_threads.set(season_id, new_threads)


async def on_match_ended(bot: commands.Bot, payload: dict) -> None:
    """a single match finished. close its thread."""
    season_id = str(payload.get("season_id") or "")
    key = str(payload.get("key") or "")
    if not season_id or not key:
        return

    tid = match_threads.remove(season_id, key)
    if tid is None:
        return

    await _delete_thread(bot, tid)


async def on_tournament_completed(bot: commands.Bot, payload: dict) -> None:
    """the bracket is over. clean up the season's roles and threads, refresh the panel."""
    season_id = str(payload.get("season_id") or "")
    if not season_id:
        return
    await cleanup_season(bot, season_id)
    await refresh_register_panel(bot)


async def on_match_announcement(bot: commands.Bot, payload: dict) -> None:
    """a match is going live. post the announcement directly (no subprocess)."""
    from tournament.webhook import Webhook

    season_id = str(payload.get("season_id") or "")
    role_id = int(payload.get("participant_role_id") or 0)
    team1 = str(payload.get("team1") or "")
    team2 = str(payload.get("team2") or "")
    if not season_id or not team1 or not team2:
        return

    message = f"<@&{role_id}> **{team1}** vs **{team2}** is live now in the server!"
    webhook = Webhook(season_id)
    try:
        await asyncio.to_thread(
            webhook.send, "announcements", "match-announcement", None, message
        )
    except Exception as e:
        print(f"Discord: match announcement failed: {e}")


HANDLERS = {
    "team_verified": on_team_verified,
    "round_started": on_round_started,
    "match_ended": on_match_ended,
    "tournament_completed": on_tournament_completed,
    "match_announcement": on_match_announcement,
}