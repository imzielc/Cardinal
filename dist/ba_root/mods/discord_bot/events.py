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

from discord_bot.ui import (
    TeamInvitationView,
    allowed_mentions_for,
    create_team_role,
    create_team_thread,
    refresh_register_panel,
)
from server import config
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


async def cleanup_expired_team(bot: commands.Bot, team, season) -> None:
    """removes a team's discord traces: participant role, team role, thread."""
    channel = None
    if team.thread_id:
        try:
            channel = bot.get_channel(team.thread_id) or await bot.fetch_channel(team.thread_id)
        except discord.HTTPException:
            channel = None

    guild = getattr(channel, "guild", None) if channel else None
    if guild is None and bot.guilds:
        guild = bot.guilds[0]

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

    if team.role_id and guild is not None:
        team_role = guild.get_role(team.role_id)
        if team_role:
            try:
                await team_role.delete()
            except discord.HTTPException:
                pass

    if channel is not None:
        try:
            await channel.delete()
        except discord.HTTPException:
            pass


async def cleanup_expired_teams(bot: commands.Bot, season_id: str) -> list:
    """expires every team past its deadline, cleans up, promotes waitlist."""
    registration = Registration(season_id=season_id)
    season = tournament.get_season(season_id)
    removed = []
    for candidate in registration.expired_teams():
        team = registration.expire_team(candidate.id)
        if team is None:
            continue
        try:
            await cleanup_expired_team(bot, team, season)
        except Exception:
            pass
        removed.append(team)
        try:
            await promote_waitlist_one(bot, season_id)
        except Exception:
            pass
    return removed


async def cleanup_season(bot: commands.Bot, season_id: str) -> None:
    """deletes every discord trace of a season: roles, threads, match threads."""
    try:
        season = tournament.get_season(season_id)
    except Exception:
        season = None
    if season:
        await _delete_role(bot, season.participant_role_id)

    try:
        from tournament.brackets import Brackets
        gs = Brackets(season_id=season_id).read_gs()
        for group in gs.groups.values():
            await _delete_role(bot, group.role_id)
    except Exception:
        pass

    try:
        registration = Registration(season_id=season_id).read()
    except Exception:
        registration = None
    if registration:
        for team in registration.teams.values():
            await _delete_thread(bot, team.thread_id)
            await _delete_role(bot, team.role_id)

    for tid in match_threads.get(season_id).values():
        await _delete_thread(bot, tid)
    match_threads.clear()


# ---- waitlist ----


async def announce_waitlist_promotion(bot: commands.Bot, season_id: str, info: dict) -> None:
    """sets up discord presence for a team that just left the waitlist."""
    team_id = info.get("team_id")
    captain_id = info.get("captain_discord_id")
    if not team_id or not captain_id:
        return

    registration = Registration(season_id=season_id)
    team = registration.get_team(team_id)
    if not team:
        return

    season = tournament.get_season(season_id)
    if not season:
        return

    guild = None
    captain = None
    for g in bot.guilds:
        member = g.get_member(int(captain_id))
        if member:
            guild = g
            captain = member
            break
    if guild is None or captain is None:
        return

    if season.participant_role_id:
        participant = guild.get_role(season.participant_role_id)
        if participant:
            try:
                await captain.add_roles(participant)
            except discord.HTTPException:
                pass

    member_ids = []
    for m in team.members:
        try:
            member_ids.append(int(m.discord_id))
        except (TypeError, ValueError):
            pass
    team_role_id = await create_team_role(guild, team_id, member_ids)
    if team_role_id:
        registration.set_role(team_id, team_role_id)

    channel_id, _ = tournament.get_panel()
    if not channel_id:
        return
    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    except Exception:
        return

    invited_users = []
    for m in team.members:
        if m.discord_id == str(captain_id):
            continue
        member = guild.get_member(int(m.discord_id))
        if member:
            invited_users.append(member)

    thread = await create_team_thread(
        channel, team_id, season_id, captain, invited_users
    )
    if thread is None:
        return
    registration.set_thread(team_id, thread.id)

    invite_text = (
        f"{' '.join(u.mention for u in invited_users) or 'Welcome'}, "
        f"your team **{team_id}** was moved from the waitlist into the tournament. "
        f"Please accept the invitation."
    )
    view = TeamInvitationView(team_id=team_id, invite_text=invite_text, season_id=season_id)
    try:
        message = await thread.send(
            view=view,
            allowed_mentions=allowed_mentions_for(captain, *invited_users),
        )
        registration.set_invitation_message(team_id, message.id)
    except Exception:
        pass

    await refresh_register_panel(bot)


async def promote_waitlist_one(bot: commands.Bot, season_id: str) -> None:
    """promotes one waitlisted team and announces it."""
    try:
        promoted = Registration(season_id=season_id).promote_from_waitlist(slots=1)
    except Exception as e:
        print(f"Discord: waitlist promotion failed: {e}")
        return
    for info in promoted:
        try:
            await announce_waitlist_promotion(bot, season_id, info)
        except Exception as e:
            print(f"Discord: could not announce waitlist promotion: {e}")


# ---- thread content ----


def _match_header(payload_match: dict) -> str:
    """first message body of a match thread. group matches show standings, knockout show the round."""
    stage = payload_match.get("stage")
    team1 = payload_match.get("team1") or "TBD"
    team2 = payload_match.get("team2") or "TBD"

    if stage == "group":
        group = payload_match.get("group_id") or ""
        standings = payload_match.get("standings") or {}
        lines = []
        for name in (team1, team2):
            s = standings.get(name)
            if s:
                lines.append(
                    f"{name} · {s['wins']}W-{s['loses']}L · {s['points']} pts · "
                    f"{'+' if s['diff'] >= 0 else ''}{s['diff']} diff"
                )
            else:
                lines.append(f"{name} · no standing")
        header = f"**Group {group}**\n" if group else ""
        return header + "Standings:\n" + "\n".join(lines)

    label = payload_match.get("round_label")
    if label:
        return f"**{team1} vs {team2}** — {label} — coordinate your match timings here."
    return f"**{team1} vs {team2}** — coordinate your match timings here."


# ---- handlers ----


async def on_team_verified(bot: commands.Bot, payload: dict) -> None:
    """refreshes the panel and the invitation view after a member verifies."""
    season_id = str(payload.get("season_id") or "")
    team_id = str(payload.get("team_id") or "")
    if not int(season_id or "0") or not team_id:
        return

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
        try:
            message = await thread.send(
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            registration.set_invitation_message(team_id, message.id)
        except Exception:
            pass


async def on_round_started(bot: commands.Bot, payload: dict) -> None:
    """creates or refreshes match threads, and pings each team in their own thread."""
    season_id = str(payload.get("season_id") or "")
    if not int(season_id or "0"):
        return

    matches = payload.get("matches") or []
    existing = match_threads.get(season_id)
    new_keys = {str(m.get("key") or "") for m in matches if m.get("key")}

    # drop threads for matches that are no longer playable.
    for key in list(existing.keys()):
        if key in new_keys:
            continue
        await _delete_thread(bot, existing.pop(key))

    if not matches:
        match_threads.set(season_id, existing)
        return

    try:
        channel_id = int(config.discord.get("brackets_channel_id", 0))
    except (TypeError, ValueError):
        channel_id = 0
    if not channel_id:
        match_threads.set(season_id, existing)
        return

    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    except Exception:
        match_threads.set(season_id, existing)
        return

    registration = Registration(season_id=season_id).read()
    created_keys: set[str] = set()

    for match in matches:
        key = str(match.get("key") or "")
        team1_name = match.get("team1") or "TBD"
        team2_name = match.get("team2") or "TBD"
        if not key or team1_name == "TBD" or team2_name == "TBD":
            continue
        if key in existing:
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
                f"{header}{_match_header(match)}",
                allowed_mentions=discord.AllowedMentions(
                    roles=[discord.Object(id=rid) for rid in role_ids],
                    users=False, everyone=False,
                ),
            )
        except Exception:
            pass

        existing[key] = thread.id
        created_keys.add(key)

    match_threads.set(season_id, existing)

    # ping each team in their own thread about the matches that were just announced.
    if not created_keys:
        return

    team_matches: dict[str, list[tuple[dict, int]]] = {}
    for match in matches:
        key = str(match.get("key") or "")
        if key not in created_keys:
            continue
        tid = existing.get(key)
        if not tid:
            continue
        for tname in (match.get("team1"), match.get("team2")):
            if tname and tname != "TBD":
                team_matches.setdefault(tname, []).append((match, tid))

    for team_name, entries in team_matches.items():
        team = registration.teams.get(team_name)
        if not team or not team.thread_id:
            continue
        try:
            team_thread = (
                bot.get_channel(team.thread_id)
                or await bot.fetch_channel(team.thread_id)
            )
        except Exception:
            continue
        if team_thread is None:
            continue

        lines = []
        for match, match_thread_id in entries:
            opponent = (
                match.get("team2") if match.get("team1") == team_name
                else match.get("team1")
            )
            label = match.get("round_label") or ""
            if label:
                lines.append(f"**{label}** — vs **{opponent}** <#{match_thread_id}>")
            else:
                lines.append(f"vs **{opponent}** <#{match_thread_id}>")

        body = "Your team has a match:\n" + "\n".join(lines)
        content = f"<@&{team.role_id}> {body}" if team.role_id else body
        mentions = discord.AllowedMentions(
            roles=[discord.Object(id=team.role_id)] if team.role_id else [],
            users=False, everyone=False,
        )
        try:
            await team_thread.send(content, allowed_mentions=mentions)
        except Exception:
            pass


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
    """a match is going live. post the announcement directly."""
    from tournament.webhook import Webhook

    season_id = str(payload.get("season_id") or "")
    role_id = int(payload.get("participant_role_id") or 0)
    team1 = str(payload.get("team1") or "")
    team2 = str(payload.get("team2") or "")
    label = str(payload.get("round_label") or "")
    if not season_id or not team1 or not team2:
        return

    prefix = f"**{label}** — " if label else ""
    message = f"<@&{role_id}> {prefix}**{team1}** vs **{team2}** is live now in the server!"
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