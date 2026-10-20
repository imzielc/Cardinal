"""tournament slash commands."""

from __future__ import annotations

import asyncio

import discord
from discord import Interaction, app_commands
from discord.ext import commands

from discord_bot.client import GameClient
from discord_bot.events import cleanup_season, cleanup_expired_teams, promote_waitlist_one
from discord_bot.helpers import require
from discord_bot.ui import (
    CaptainRegistrationModal,
    ConfirmView,
    RegisterView,
    SoloRegistrationModal,
    _expiry_label,
    allowed_mentions_for,
    invitation_view,
    participant_role,
    refresh_register_panel,
)
from server import config
from server.enums import (
    Authority,
    SeriesType,
    TeamStatus,
    TournamentMode,
    TournamentStage,
    TournamentType,
)
from tournament import posts, tournament
from tournament.brackets import round_label
from tournament.link import link
from tournament.registration import Registration
from tournament.schema import SeasonSchema


class TournamentCommands(
    commands.GroupCog,
    group_name="tournament",
    group_description="Commands to manage tournaments.",
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()

    async def _get_thread(self, thread_id: int):
        """returns the thread if reachable, otherwise None."""
        if not thread_id:
            return None
        try:
            return self.bot.get_channel(thread_id) or await self.bot.fetch_channel(thread_id)
        except discord.HTTPException:
            return None

    @app_commands.command(name="create")
    @app_commands.describe(
        type="Tournament Type",
        series="Series Type",
        mode="Single or double elimination (default: single)",
    )
    @require(authority=Authority.LEADER)
    async def create_season(
        self,
        interaction: Interaction,
        type: TournamentType,
        series: SeriesType,
        mode: TournamentMode = TournamentMode.SINGLE,
    ) -> None:
        """creates a tournament season"""
        if int(tournament.active_season):
            await interaction.response.send_message(
                "Cannot create! a season is going on."
            )
            return

        from datetime import datetime, timedelta, timezone

        ist = timezone(timedelta(hours=5, minutes=30))

        schema = SeasonSchema(
            series=series,
            type=type,
            mode=mode,
            created_at=datetime.now(ist).isoformat(),
        )
        tournament.create_season(schema=schema)
        await interaction.response.send_message(
            f"The {type.lower()} season has been created with series: {series.lower()}, mode: {mode.lower().replace('_', ' ')}"
        )
        role = discord.utils.get(interaction.guild.roles, name="Participant")
        if role:
            await role.delete()
        role = await interaction.guild.create_role(
            name="Participant", mentionable=True,
        )
        season = tournament.get_season(season_id=tournament.active_season)
        season.participant_role_id = role.id
        tournament.update_season(season_id=tournament.active_season, season=season)

        await refresh_register_panel(self.bot)

    @app_commands.command(name="register-panel")
    @require(Authority.LEADER)
    async def register_panel(self, interaction: Interaction) -> None:
        """posts the registration panel here (cleans up the previous one)."""
        await interaction.response.defer(ephemeral=True)

        old_channel_id, old_message_id = tournament.get_panel()
        if old_channel_id and old_message_id:
            try:
                old_channel = self.bot.get_channel(old_channel_id) or await self.bot.fetch_channel(old_channel_id)
                old_message = await old_channel.fetch_message(old_message_id)
                await old_message.delete()
            except discord.HTTPException:
                pass

        message = await interaction.channel.send(view=RegisterView())
        tournament.set_panel(interaction.channel.id, message.id)
        await interaction.followup.send("Registration panel posted.", ephemeral=True)

    @app_commands.command(name="cancel")
    @require(Authority.LEADER)
    async def cancel_tournament(self, interaction: Interaction) -> None:
        """cancels the active tournament"""
        season_id = tournament.active_season
        if not int(season_id):
            await interaction.response.send_message(
                "There is no tournament season opened currently.", ephemeral=True
            )
            return

        season = tournament.get_season(season_id)

        async def confirmed(confirm_interaction: Interaction) -> None:
            if tournament.active_season != season_id:
                await confirm_interaction.response.edit_message(
                    content="That tournament was already cancelled.", view=None
                )
                return
            await confirm_interaction.response.edit_message(
                content="Cancelling the tournament...", view=None
            )

            await cleanup_season(self.bot, season_id)

            tournament.cancel_season(season_id)
            cancelled = await asyncio.to_thread(posts.cancel_season, season_id)
            staff = self.bot.get_cog("HeldPosts")
            if staff:
                for record in cancelled:
                    await staff.update_staff_message(
                        record, "cancelled (the tournament was cancelled)"
                    )
            await GameClient().send_action("tournament_cancel")

            await confirm_interaction.followup.send(
                f"Tournament season {season_id} has been cancelled by {confirm_interaction.user.mention}."
            )
            await refresh_register_panel(self.bot)

        view = ConfirmView(
            interaction.user.id,
            confirmed,
            confirm_label="Yes, cancel it",
            abort_text="The tournament goes on.",
        )
        await interaction.response.send_message(
            f"Cancel the active tournament (season {season_id}: {season.type.lower()}, "
            f"{season.mode.lower().replace('_', ' ')}, stage {season.stage.lower().replace('_', ' ')})?\n"
            "Registrations close, the held posts are thrown away and a match that is being played is stopped. "
            "This cannot be undone.",
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name="invite")
    @app_commands.describe(user="The user to invite to your team.")
    async def invite_member(self, interaction: Interaction, user: discord.Member) -> None:
        """invites a new member to your team (captain only)."""
        season_id = tournament.active_season
        if not int(season_id):
            await interaction.response.send_message(
                "There is no tournament ongoing.", ephemeral=True
            )
            return
        if user.bot:
            await interaction.response.send_message(
                "You cannot invite bots.", ephemeral=True
            )
            return
        if user.id == interaction.user.id:
            await interaction.response.send_message(
                "You cannot invite yourself.", ephemeral=True
            )
            return

        registration = Registration(season_id=season_id)
        team = registration.team_of(str(interaction.user.id))
        if not team:
            await interaction.response.send_message(
                "You are not in a team.", ephemeral=True
            )
            return
        if team.captain != str(interaction.user.id):
            await interaction.response.send_message(
                "Only the captain can invite members.", ephemeral=True
            )
            return
        if team.status != TeamStatus.IN_INVITATION:
            await interaction.response.send_message(
                "Your team is not accepting invitations anymore.", ephemeral=True
            )
            return
        if registration.is_expired(team):
            await interaction.response.send_message(
                "The invitation has expired.", ephemeral=True
            )
            return
        if any(str(user.id) == member.discord_id for member in team.members):
            await interaction.response.send_message(
                "This user is already on your team.", ephemeral=True
            )
            return

        remaining = registration.cooldown_remaining(team.id, str(user.id))
        if remaining > 0:
            minutes = int(remaining // 60) + 1
            await interaction.response.send_message(
                f"This user declined a recent invite. You can try again in about {minutes} minute(s).",
                ephemeral=True,
            )
            return

        if not link.is_linked(str(user.id)):
            await interaction.response.send_message(
                "This user hasn't linked their game account yet.", ephemeral=True
            )
            return
        if registration.is_registered(str(user.id)):
            await interaction.response.send_message(
                "This user is already registered.", ephemeral=True
            )
            return

        season = tournament.get_season(season_id)
        if season and len(team.members) >= season.type.count:
            await interaction.response.send_message(
                "Your team is already full.", ephemeral=True
            )
            return

        if not registration.add_member(team.id, str(user.id)):
            await interaction.response.send_message(
                "Could not invite this user.", ephemeral=True
            )
            return

        if team.role_id:
            try:
                team_role = interaction.guild.get_role(team.role_id)
                if team_role:
                    await user.add_roles(team_role)
            except discord.HTTPException:
                pass

        await interaction.response.send_message(
            f"Invited {user.mention} to team **{team.id}**.", ephemeral=True
        )

        updated = registration.get_team(team.id)
        if not updated:
            return

        invite_text = (
            f"{user.mention}, you have been invited to join **{updated.id}** "
            f"by <@{updated.captain}>."
        )
        view = invitation_view(updated, season_id, invite_text=invite_text)

        thread = await self._get_thread(updated.thread_id)
        if thread is not None:
            try:
                await thread.add_user(user)
            except discord.HTTPException:
                pass
            try:
                message = await thread.send(
                    view=view,
                    allowed_mentions=allowed_mentions_for(user),
                )
                registration.set_invitation_message(team.id, message.id)
            except discord.HTTPException:
                pass

    @app_commands.command(name="leave")
    async def leave_team(self, interaction: Interaction) -> None:
        """leaves your team. if you're the captain, the whole team is dissolved."""
        season_id = tournament.active_season
        if not int(season_id):
            await interaction.response.send_message(
                "There is no tournament ongoing.", ephemeral=True
            )
            return

        registration = Registration(season_id=season_id)
        team = registration.team_of(str(interaction.user.id))
        if not team:
            await interaction.response.send_message(
                "You're not on a team.", ephemeral=True
            )
            return

        season = tournament.get_season(season_id)
        if season and season.stage != TournamentStage.REGISTRATION:
            await interaction.response.send_message(
                "The tournament has already started, you can't leave now.",
                ephemeral=True,
            )
            return

        is_captain = team.captain == str(interaction.user.id)
        if is_captain:
            prompt = (
                f"You're the captain of **{team.id}**. Leaving will dissolve the whole "
                f"team and remove every member. Are you sure?"
            )
        else:
            prompt = f"Leave **{team.id}**?"

        async def confirmed(confirm_interaction: Interaction) -> None:
            current = Registration(season_id=season_id).get_team(team.id)
            if not current:
                await confirm_interaction.response.edit_message(
                    content="That team is no longer there.", view=None
                )
                return

            await confirm_interaction.response.edit_message(
                content="Working...", view=None
            )

            role = participant_role(interaction.guild, season_id)
            thread = await self._get_thread(current.thread_id)

            if is_captain:
                members = list(current.members)
                Registration(season_id=season_id).delete(team.id)

                for member in members:
                    try:
                        user = interaction.guild.get_member(int(member.discord_id))
                    except (TypeError, ValueError):
                        continue
                    if user is None:
                        continue
                    if role and role in user.roles:
                        try:
                            await user.remove_roles(role)
                        except discord.HTTPException:
                            pass

                if current.role_id:
                    try:
                        team_role = interaction.guild.get_role(current.role_id)
                        if team_role:
                            await team_role.delete()
                    except discord.HTTPException:
                        pass

                if thread is not None:
                    try:
                        await thread.send(
                            f"**{team.id}** was dissolved by the captain."
                        )
                        await thread.edit(archived=True, locked=True)
                    except discord.HTTPException:
                        pass

                await confirm_interaction.followup.send(
                    f"Team **{team.id}** dissolved.", ephemeral=True
                )

                await promote_waitlist_one(self.bot, season_id)
            else:
                updated = Registration(season_id=season_id).leave(
                    str(interaction.user.id)
                )

                if role and role in interaction.user.roles:
                    try:
                        await interaction.user.remove_roles(role)
                    except discord.HTTPException:
                        pass

                if current.role_id:
                    try:
                        team_role = interaction.guild.get_role(current.role_id)
                        if team_role and team_role in interaction.user.roles:
                            await interaction.user.remove_roles(team_role)
                    except discord.HTTPException:
                        pass

                if thread is not None and updated is not None:
                    try:
                        await thread.send(
                            f"<@{updated.captain}> {interaction.user.mention} left the team."
                        )
                    except discord.HTTPException:
                        pass

                await confirm_interaction.followup.send(
                    f"You left **{team.id}**.", ephemeral=True
                )

            await refresh_register_panel(self.bot)

        view = ConfirmView(
            interaction.user.id,
            confirmed,
            confirm_label="Yes, leave",
            abort_text="You're still on the team.",
        )
        await interaction.response.send_message(prompt, view=view, ephemeral=True)

    @app_commands.command(name="extend")
    @app_commands.describe(hours="How many hours to add to the invitation expiry (1-168)")
    @require(Authority.LEADER)
    async def extend_expiry(self, interaction: Interaction, hours: int) -> None:
        """extends the invitation expiry for the season and every team. leader only."""
        season_id = tournament.active_season
        if not int(season_id):
            await interaction.response.send_message(
                "There is no tournament season opened currently.", ephemeral=True
            )
            return

        if hours <= 0 or hours > 168:
            await interaction.response.send_message(
                "Hours must be between 1 and 168.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        current = config.discord.get("invite_expire_hours", 24)
        try:
            current = float(current)
        except (TypeError, ValueError):
            current = 24.0
        new_hours = current + hours
        config.set_nested(["discord", "invite_expire_hours"], new_hours)

        registration = Registration(season_id=season_id)
        extended = registration.extend_all_expiries(hours)

        expired = await cleanup_expired_teams(self.bot, season_id)
        for team in expired:
            print(
                f"Discord: expired team {team.id} after extension "
                f"(status={team.status.value}, expires_at={int(team.expires_at)})"
            )

        print(
            f"[leader] extend: +{hours}h by {interaction.user.name} "
            f"(config now {new_hours}h, extended {extended} team(s), "
            f"expired {len(expired)} team(s))"
        )

        msg = (
            f"Extended invitation expiry by **{hours}h**. "
            f"Config is now **{int(new_hours) if new_hours.is_integer() else new_hours}h**. "
            f"Updated **{extended}** team(s)."
        )
        if expired:
            msg += f" Expired **{len(expired)}** team(s) that were still past deadline."
        await interaction.followup.send(msg, ephemeral=True)

    @app_commands.command(name="release")
    @require(Authority.LEADER)
    async def release_round(self, interaction: Interaction) -> None:
        """releases a pending round start immediately, bypassing the night window."""
        season_id = tournament.active_season
        if not int(season_id):
            await interaction.response.send_message(
                "There is no tournament season opened currently.", ephemeral=True
            )
            return

        from tournament.brackets import Brackets
        brackets = Brackets(season_id=season_id)
        if not brackets.read_meta().pending_round_start:
            await interaction.response.send_message(
                "No pending round to release.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            result = await GameClient().send_action(
                "release_pending_rounds", response=True
            )
        except Exception as e:
            await interaction.followup.send(f"Failed to send: {e}", ephemeral=True)
            return

        if isinstance(result, dict) and result.get("released"):
            await interaction.followup.send("Round released.", ephemeral=True)
        else:
            await interaction.followup.send(
                "Could not release (a match may be running).", ephemeral=True
            )

    @app_commands.command(name="registrations")
    @app_commands.describe(option="Open or close the registrations")
    @require(Authority.LEADER)
    async def registrations(self, interaction: Interaction, option: bool) -> None:
        """open/close the registrations"""
        if not int(tournament.active_season):
            await interaction.response.send_message(
                "There is no tournament season opened currently.", ephemeral=True
            )
            return

        if option:
            if tournament.are_registrations_open:
                await interaction.response.send_message(
                    "Cannot open the registrations! they are already open.",
                    ephemeral=True,
                )
                return
            tournament.open_registrations()
        else:
            if not tournament.are_registrations_open:
                await interaction.response.send_message(
                    "Cannot close the registrations! they are already closed.",
                    ephemeral=True,
                )
                return
            tournament.close_registrations()

        await interaction.response.send_message(
            f"{'Opened' if option else 'closed'} the registrations."
        )
        await refresh_register_panel(self.bot)

    @app_commands.command(name="uuid")
    @require(Authority.LEADER)
    async def change_uuid(
        self, interaction: Interaction, user: discord.Member, uuid: str
    ) -> None:
        """changes the uuid of the registered player"""
        if not int(tournament.active_season):
            await interaction.response.send_message(
                "There is no tournament season opened currently.", ephemeral=True
            )
            return

        registration = Registration(season_id=tournament.active_season)
        if not registration.is_registered(str(user.id)):
            await interaction.response.send_message(
                "He is not registered.", ephemeral=True
            )
            return

        registration.change_uuid(discord_id=str(user.id), new_uuid=uuid)
        await interaction.response.send_message(
            f"Changed the uuid of {user.mention}'s account."
        )

    @app_commands.command(name="win")
    @require(Authority.LEADER)
    async def give_win(self, interaction: Interaction, match_index: int, team_index: int) -> None:
        """ gives win to the team"""
        if not int(tournament.active_season):
            await interaction.response.send_message(
                "There is no tournament season opened currently.", ephemeral=True
            )
            return

        from tournament.brackets import Brackets
        brackets = Brackets(season_id=tournament.active_season)
        response = brackets.give_win_to_team(match_index=match_index, team_index=team_index)
        await interaction.response.send_message(response, ephemeral=True)

    @app_commands.command(name="list")
    @require(Authority.LEADER)
    async def list_matches(self, interaction: Interaction) -> None:
        """lists all the matches in active round."""
        from tournament.brackets import Brackets
        brackets = Brackets(season_id=tournament.active_season)
        matches = brackets.list_matches()
        if not matches:
            await interaction.response.send_message("There are no matches in the round.")
            return

        lines = ["**Round matches:**\n"]
        for match_index, (key, match) in enumerate(matches.items(), start=1):
            team1 = match.teams[0].name or "BYE"
            team2 = match.teams[1].name or "BYE"
            label = round_label(match)
            status = match.status
            line = (
                f"`Match #{match_index}` | "
                f"**{label}** | "
                f"**(1)** `{team1}` vs **(2)** `{team2}` | "
                f"Status: `{status}`"
            )
            lines.append(line)
        message_text = "\n".join(lines)
        await interaction.response.send_message(message_text, ephemeral=True)

    @app_commands.command(name="teams")
    async def list_teams(self, interaction: Interaction) -> None:
        """lists the registered teams of the active season."""
        season_id = tournament.active_season
        if not int(season_id):
            await interaction.response.send_message(
                "There is no tournament season opened currently.", ephemeral=True
            )
            return

        registration = Registration(season_id=season_id).read()
        if not registration.teams:
            await interaction.response.send_message(
                "No teams registered yet.", ephemeral=True
            )
            return

        total = len(registration.teams)
        verified = sum(
            1 for t in registration.teams.values() if t.status == TeamStatus.VERIFIED
        )

        lines = [f"**Registered teams** — {total} total ({verified} verified)\n"]

        for team in registration.teams.values():
            joined = sum(1 for m in team.members if m.accepted)
            total_members = len(team.members)

            if team.status == TeamStatus.VERIFIED:
                status = "verified"
            elif team.status == TeamStatus.UNVERIFIED:
                status = f"unverified · {joined}/{total_members} joined"
            else:
                status = f"invitation · {joined}/{total_members} joined"

            parts = [f"**{team.id}**", f"`{status}`"]

            if team.expires_at > 0 and team.status != TeamStatus.VERIFIED:
                parts.append(_expiry_label(team.expires_at))

            if team.role_id:
                parts.append(f"<@&{team.role_id}>")

            lines.append(" — ".join(parts))

        chunks = []
        current = ""
        for line in lines:
            if len(current) + len(line) + 1 > 1900:
                chunks.append(current)
                current = line
            else:
                current += ("\n" if current else "") + line
        if current:
            chunks.append(current)

        allowed = discord.AllowedMentions.none()
        if len(chunks) == 1:
            await interaction.response.send_message(chunks[0], allowed_mentions=allowed)
            return

        await interaction.response.send_message(chunks[0], allowed_mentions=allowed)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, allowed_mentions=allowed)

    @app_commands.command(name="waitlist")
    @require(Authority.LEADER)
    async def show_waitlist(self, interaction: Interaction) -> None:
        """lists the teams queued for the active season."""
        season_id = tournament.active_season
        if not int(season_id):
            await interaction.response.send_message(
                "There is no tournament season opened currently.", ephemeral=True
            )
            return

        from tournament.waitlist import Waitlist
        wl = Waitlist(season_id)
        entries = wl.read().entries
        if not entries:
            await interaction.response.send_message(
                "The waitlist is empty.", ephemeral=True
            )
            return

        lines = [f"**Waitlist for season {season_id}:**\n"]
        for idx, entry in enumerate(entries, start=1):
            lines.append(
                f"`{idx}.` **{entry.team_name}** — <@{entry.captain_discord_id}>"
            )
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="start")
    @require(Authority.LEADER)
    async def start_tournament(self, interaction: Interaction) -> None:
        """starts the tournament"""
        if not int(tournament.active_season):
            await interaction.response.send_message(
                "There is no tournament season opened currently.", ephemeral=True
            )
            return

        if tournament.are_registrations_open:
            await interaction.response.send_message(
                "Cannot start the tournament! registrations are still open.",
                ephemeral=True,
            )
            return

        from tournament.brackets import Brackets

        brackets = Brackets(season_id=tournament.active_season)
        if brackets.read_meta().started:
            await interaction.response.send_message(
                "The tournament is already started.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)

        registration = Registration(season_id=brackets.season_id).read()
        teams = [
            team_id
            for team_id, team in registration.teams.items()
            if team.status == TeamStatus.VERIFIED
        ]
        skipped = [team_id for team_id in registration.teams if team_id not in teams]
        if skipped:
            await interaction.followup.send(
                f"The following teams have not been verified yet: {', '.join(skipped)}",
                ephemeral=True,
            )
            return
        mode = tournament.get_season(tournament.active_season).mode
        is_groupstage = False
        try:
            if mode == TournamentMode.DOUBLE:
                brackets.generate_double_elimination(teams=teams)
            else:
                is_groupstage = brackets.generate_group_stage(teams=teams)
        except (AssertionError, ValueError):
            message = (
                "Double elimination needs at least 4 teams."
                if mode == TournamentMode.DOUBLE
                else "The number of teams are either less than 4 or not divisible by 4."
            )
            await interaction.followup.send(
                f"{message} The tournament cannot be started.", ephemeral=True
            )
            return

        if is_groupstage:
            brackets.send_groupstage_brackets()
            guild = interaction.guild
            gs = brackets.read_gs()
            for group_name in gs.groups:
                role_name = f"Group {group_name}"
                role = discord.utils.get(guild.roles, name=role_name)
                if role:
                    await role.delete()
                role = await guild.create_role(
                    name=role_name, mentionable=True,
                )
                gs.groups[group_name].role_id = role.id
            brackets.commit_gs(gs)
        await interaction.followup.send(
            "The tournament has been started!", ephemeral=True
        )
        await asyncio.sleep(30)
        pings = "### These are the registered players/team captains:\n"
        for index, team in enumerate(teams, start=1):
            pings += f"{index}. `{team}`: <@{registration.teams[team].captain}>\n"

        if posts.hold_enabled():
            await asyncio.to_thread(
                posts.announce_lines,
                tournament.active_season,
                pings.rstrip("\n").split("\n"),
                "Team captains list",
                "captains",
            )
        else:
            await interaction.followup.send(pings)

        season = tournament.get_season(tournament.active_season)
        season.stage = TournamentStage.IN_PROGRESS
        tournament.update_season(season_id=tournament.active_season, season=season)

        await refresh_register_panel(self.bot)