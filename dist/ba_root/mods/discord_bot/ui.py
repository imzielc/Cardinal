"""ui components for discord bot."""

from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import AllowedMentions, ButtonStyle, Guild, Interaction, Role, ui
from discord.utils import get

from server.enums import TeamStatus
from tournament import tournament
from tournament.link import link
from tournament.registration import Registration

TEAM_NAME_MAX_LENGTH = 40
THREAD_ARCHIVE_MINUTES = 1440
THREAD_NAME_SUFFIX_MAX = 6

REGISTER_BUTTON_ID = "tournament;register"


def thread_name(team_name: str, season_id: str) -> str:
    """a stable, unique-ish name for the team's private thread."""
    base = team_name.strip()[: 100 - THREAD_NAME_SUFFIX_MAX - 4]
    return f"{base} · S{season_id}"


def parse_date(value: str) -> int | None:
    """turns an ISO date or datetime string into a unix timestamp, or None."""
    if not value:
        return None
    try:
        if "T" in value:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return None


async def refresh_register_panel(bot) -> None:
    """re-renders the tracked register panel to match the current state. silent on any failure."""
    channel_id, message_id = tournament.get_panel()
    if not channel_id or not message_id:
        return

    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    except discord.NotFound:
        tournament.clear_panel()
        return
    except discord.HTTPException:
        return

    try:
        message = await channel.fetch_message(message_id)
    except discord.NotFound:
        tournament.clear_panel()
        return
    except discord.HTTPException:
        return

    try:
        await message.edit(view=RegisterView())
    except discord.HTTPException:
        pass


class ConfirmView(ui.View):
    """asks the user to confirm something that cannot be undone, only that user can answer."""

    def __init__(self, user_id: int, on_confirm, confirm_label: str = "Yes", abort_text: str = "Okay, nothing was changed."):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.on_confirm = on_confirm
        self.abort_text = abort_text
        self.confirm.label = confirm_label

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your confirmation.", ephemeral=True
            )
            return False
        return True

    @ui.button(label="Yes", style=ButtonStyle.danger)
    async def confirm(self, interaction: Interaction, button: ui.Button):
        self.stop()
        await self.on_confirm(interaction)

    @ui.button(label="No", style=ButtonStyle.secondary)
    async def abort(self, interaction: Interaction, button: ui.Button):
        self.stop()
        await interaction.response.edit_message(content=self.abort_text, view=None)


def participant_role(guild: Guild, season_id: str) -> Role | None:
    """returns the participant role of the season."""
    season = tournament.get_season(season_id)
    if season and season.participant_role_id:
        role = guild.get_role(season.participant_role_id)
        if role:
            return role
    return get(guild.roles, name="Participant")


def valid_team_name(name: str) -> bool:
    """the team name is used inside a button's custom id, so it must stay simple."""
    return bool(name.strip()) and ";" not in name


def allowed_mentions_for(*members) -> AllowedMentions:
    """only the given users may be pinged, nobody else."""
    users = []
    for member in members:
        if member is None:
            continue
        uid = getattr(member, "id", None)
        if uid is None:
            continue
        users.append(discord.Object(id=int(uid)))
    return AllowedMentions(users=users, roles=False, everyone=False)


def invitation_view(team, season_id: str, invite_text: str = "") -> "TeamInvitationView":
    return TeamInvitationView(team_id=team.id, invite_text=invite_text, season_id=season_id)


async def create_team_role(guild: Guild, team_name: str, member_ids: list[int]) -> int:
    """creates the team's role, adds everyone to it, returns the id (0 on failure)."""
    if guild is None:
        return 0
    try:
        role = await guild.create_role(
            name=team_name[:100],
            mentionable=True,
            reason=f"Tournament team role for {team_name}",
        )
    except (discord.Forbidden, discord.HTTPException):
        return 0

    for mid in member_ids:
        try:
            member = guild.get_member(mid) or await guild.fetch_member(mid)
            if member:
                await member.add_roles(role)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            pass

    return role.id


# ---- register panel ----


class RegisterView(ui.LayoutView):
    """the persistent registration panel. rebuilt from state every time it is shown."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self._build()

    def _build(self) -> None:
        self.clear_items()

        season_id = tournament.active_season
        season = tournament.get_season(season_id) if int(season_id) else None
        open_ = season is not None and tournament.are_registrations_open

        container = ui.Container(accent_color=discord.Color.blurple())
        container.add_item(ui.TextDisplay("### 🏆  Tournament Registration"))

        if season is None:
            container.add_item(
                ui.TextDisplay("No tournament is running right now. Check back later.")
            )
            container.add_item(ui.Separator())
            button = ui.Button(
                label="Register",
                style=ButtonStyle.primary,
                emoji="<:register:1560371261459267654>",
                custom_id=REGISTER_BUTTON_ID,
                disabled=True,
            )
        else:
            type_str = season.type.value.replace("_", " ").title()
            series_str = season.series.value.replace("_", " ").title()
            mode_str = season.mode.value.replace("_", " ").title()

            total, verified = 0, 0
            try:
                reg = Registration(season_id=season_id).read()
                total = len(reg.teams)
                verified = sum(
                    1 for t in reg.teams.values() if t.status == TeamStatus.VERIFIED
                )
            except Exception:
                pass

            container.add_item(
                ui.TextDisplay(
                    f"**Season** `{season_id}`\n"
                    f"**Format** {type_str} • {series_str}\n"
                    f"**Mode** {mode_str}\n"
                    f"**Teams** {total} registered • {verified} verified"
                )
            )
            container.add_item(ui.Separator())

            if open_:
                container.add_item(
                    ui.TextDisplay(
                        "Click the button below to sign up. "
                        "You'll need your game account linked (`/account link`)."
                    )
                )
                button = ui.Button(
                    label="Register",
                    style=ButtonStyle.primary,
                    emoji="<:register:1560371261459267654>",
                    custom_id=REGISTER_BUTTON_ID,
                )
                button.callback = self.register_btn
            else:
                container.add_item(
                    ui.TextDisplay("🔒 Registrations are currently closed.")
                )
                button = ui.Button(
                    label="Register",
                    style=ButtonStyle.primary,
                    emoji="<:register:1560371261459267654>",
                    custom_id=REGISTER_BUTTON_ID,
                    disabled=True,
                )

        container.add_item(ui.ActionRow(button))
        self.add_item(container)

    async def register_btn(self, interaction: Interaction) -> None:
        season_id = tournament.active_season
        if not int(season_id):
            await interaction.response.send_message(
                "There is no tournament ongoing right now.", ephemeral=True
            )
            return

        if not tournament.are_registrations_open:
            await interaction.response.send_message(
                "Registrations are closed.", ephemeral=True
            )
            return

        if not link.is_linked(str(interaction.user.id)):
            await interaction.response.send_message(
                "Link your game account first with `/account link`.", ephemeral=True
            )
            return

        season_data = tournament.get_season(season_id=season_id)
        size = season_data.type.count
        if size > 1:
            await interaction.response.send_modal(
                CaptainRegistrationModal(season_id=season_id, size=size - 1)
            )
        else:
            await interaction.response.send_modal(
                SoloRegistrationModal(season_id=season_id)
            )


# ---- account status view ----


class LinkedAccountView(ui.LayoutView):
    """shows the game account bound to a discord id, using components v2."""

    def __init__(self, data: dict, discord_id: str):
        super().__init__(timeout=180)
        self.data = data
        self.discord_id = discord_id
        self._build()

    def _build(self) -> None:
        self.clear_items()

        tag = str(self.data.get("tag") or "Unknown")
        account_id = str(self.data.get("id") or self.discord_id)

        container = ui.Container()
        container.add_item(ui.TextDisplay("### Linked account"))
        container.add_item(
            ui.TextDisplay(
                f"**Tag**  `{tag}`\n"
                f"**Account ID**  `{account_id}`"
            )
        )
        container.add_item(ui.Separator())

        rows = []

        created = parse_date(self.data.get("create_time", ""))
        if created:
            rows.append(f"**Created**  <t:{created}:D>")

        last = parse_date(self.data.get("last_active_day", ""))
        if last:
            rows.append(f"**Last seen**  <t:{last}:D>")

        days = self.data.get("total_active_days")
        if days is not None:
            rows.append(f"**Active days**  `{days}`")

        if rows:
            container.add_item(ui.TextDisplay("\n".join(rows)))

        self.add_item(container)


# solo registration modal.


class SoloRegistrationModal(ui.Modal, title="Tournament Solo Registration."):
    """asks for team name."""

    team_name = ui.TextInput(
        label="Team Name",
        required=True,
        placeholder="Enter an unique team name.",
        max_length=TEAM_NAME_MAX_LENGTH,
    )

    def __init__(self, season_id: str):
        super().__init__()
        self.season_id = season_id

    async def on_submit(self, interaction: Interaction):
        registration = Registration(self.season_id)

        if not link.is_linked(str(interaction.user.id)):
            await interaction.response.send_message(
                "Link your game account first with `/account link`.", ephemeral=True
            )
            return

        if registration.is_registered(str(interaction.user.id)):
            await interaction.response.send_message(
                "You are already registered.", ephemeral=True
            )
            return

        if not valid_team_name(self.team_name.value):
            await interaction.response.send_message(
                "The team name cannot be empty or contain `;`.", ephemeral=True
            )
            return

        team_id = registration.register(
            team_name=self.team_name.value,
            captain_discord_id=str(interaction.user.id),
        )
        if team_id is None:
            await interaction.response.send_message(
                "The name you have chosen is already taken.", ephemeral=True
            )
            return
        if not team_id:
            await interaction.response.send_message(
                "Couldn't register you. Make sure your account isn't on another team.",
                ephemeral=True,
            )
            return

        role = participant_role(interaction.guild, self.season_id)
        if role:
            await interaction.user.add_roles(role)
        await interaction.response.send_message(
            f"You're registered as **{self.team_name.value}**! Join the game and run `/verify` to finish.",
            ephemeral=True,
        )
        await refresh_register_panel(interaction.client)


# registration modal for team-captain to input his  team-name and select team members.


class CaptainRegistrationModal(ui.Modal, title="Tournament Team Registration."):
    """asks for captain's account id and team's name"""

    team_name = ui.TextInput(
        label="Team Name",
        required=True,
        placeholder="Enter an unique team name.",
        max_length=TEAM_NAME_MAX_LENGTH,
    )

    def __init__(self, season_id: str, size: int):
        super().__init__()
        self.season_id = season_id
        self.select = ui.Label(
            text="Select your teammates",
            component=ui.UserSelect(
                placeholder=f"Select {size}", min_values=size, max_values=size
            ),
        )
        self.add_item(self.select)

    async def _find_archived_thread(self, channel, name: str):
        """looks for an archived private thread with this name in the channel."""
        try:
            async for thread in channel.archived_threads(limit=100, private=True):
                if thread.name == name and thread.archived:
                    return thread
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            pass
        return None

    async def _create_team_thread(self, interaction: Interaction, invited_members: list):
        """returns the private thread for the team, reusing an archived one if it exists."""
        channel = interaction.channel
        if not hasattr(channel, "create_thread"):
            return None

        name = thread_name(self.team_name.value, self.season_id)

        existing = await self._find_archived_thread(channel, name)
        if existing is not None:
            try:
                if existing.locked:
                    await existing.edit(archived=False, locked=False)
                else:
                    await existing.edit(archived=False)
                await existing.send("**Team re-created.** Fresh invitation below.")
                try:
                    await existing.add_user(interaction.user)
                except discord.HTTPException:
                    pass
                for member in invited_members:
                    try:
                        await existing.add_user(member)
                    except discord.HTTPException:
                        pass
                return existing
            except discord.HTTPException:
                pass

        try:
            thread = await channel.create_thread(
                name=name,
                type=discord.ChannelType.private_thread,
                invitable=False,
                auto_archive_duration=THREAD_ARCHIVE_MINUTES,
            )
        except (discord.Forbidden, discord.HTTPException, TypeError):
            return None

        try:
            await thread.add_user(interaction.user)
        except discord.HTTPException:
            try:
                await thread.delete()
            except discord.HTTPException:
                pass
            return None

        for member in invited_members:
            try:
                await thread.add_user(member)
            except discord.HTTPException:
                pass
        return thread

    async def on_submit(self, interaction: Interaction):
        registration = Registration(self.season_id)

        if not link.is_linked(str(interaction.user.id)):
            await interaction.response.send_message(
                "Link your game account first with `/account link`.", ephemeral=True
            )
            return

        if registration.is_registered(str(interaction.user.id)):
            await interaction.response.send_message(
                "You are already registered.", ephemeral=True
            )
            return

        if not valid_team_name(self.team_name.value):
            await interaction.response.send_message(
                "The team name cannot be empty or contain `;`.", ephemeral=True
            )
            return

        invited_members = self.select.component.values
        if any(member.bot or member.id == interaction.user.id for member in invited_members):
            await interaction.response.send_message(
                "You cannot invite bots or yourself.", ephemeral=True
            )
            return

        unlinked = [m for m in invited_members if not link.is_linked(str(m.id))]
        if unlinked:
            names = ", ".join(m.mention for m in unlinked)
            await interaction.response.send_message(
                f"These players haven't linked yet: {names}.\nAsk them to run `/account link` and `/link <code>` in-game first.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        team_id = registration.register(
            team_name=self.team_name.value,
            captain_discord_id=str(interaction.user.id),
            invited_members=[str(member.id) for member in invited_members],
        )

        if team_id is None:
            await interaction.followup.send(
                "The name you have chosen is already taken.", ephemeral=True
            )
            return

        if not team_id:
            await interaction.followup.send(
                "Failed to register. One of you might already be on another team.",
                ephemeral=True,
            )
            return

        role = participant_role(interaction.guild, self.season_id)
        if role:
            await interaction.user.add_roles(role)

        member_ids = [interaction.user.id] + [m.id for m in invited_members]
        team_role_id = await create_team_role(
            interaction.guild, self.team_name.value, member_ids
        )
        if team_role_id:
            registration.set_role(team_id, team_role_id)

        thread = await self._create_team_thread(interaction, invited_members)

        team = registration.get_team(team_id)
        invite_text = (
            f"{' '.join(member.mention for member in invited_members)}, "
            f"you have been invited to join **{self.team_name.value}** by {interaction.user.mention}."
        )
        view = invitation_view(team, self.season_id, invite_text=invite_text)

        target = thread if thread else interaction.channel
        try:
            await target.send(
                view=view,
                allowed_mentions=allowed_mentions_for(interaction.user, *invited_members),
            )
        except discord.HTTPException:
            await interaction.followup.send(
                "Team created, but the invitation message could not be sent.",
                ephemeral=True,
            )
            return

        if thread:
            registration.set_thread(team_id, thread.id)

        await interaction.followup.send(
            f"Team **{self.team_name.value}** created! "
            f"Teammates must accept the invite, then everyone runs `/verify` in-game.",
            ephemeral=True,
        )
        await refresh_register_panel(interaction.client)


# team invitation view for teammates to accept/reject (components v2).


class TeamInvitationView(ui.LayoutView):
    """accept or reject a team invitation, rendered with components v2."""

    def __init__(self, team_id: str, invite_text: str = "", season_id: str = ""):
        super().__init__(timeout=None)
        self.team_id = team_id
        self.invite_text = invite_text
        self.season_id = season_id
        self._build()

    def _build(self, team=None) -> None:
        self.clear_items()

        season_id = self.season_id or tournament.active_season
        if team is None and int(season_id or "0"):
            try:
                team = Registration(season_id=season_id).get_team(self.team_id)
            except Exception:
                team = None

        season = tournament.get_season(season_id) if int(season_id or "0") else None
        size = season.type.count if season else 0

        container = ui.Container()

        if self.invite_text:
            container.add_item(ui.TextDisplay(self.invite_text))
            container.add_item(ui.Separator())

        container.add_item(ui.TextDisplay(f"### 🎫  Team invitation — **{self.team_id}**"))

        if team is None:
            container.add_item(ui.TextDisplay("*This invitation is no longer active.*"))
            self.add_item(container)
            return

        member_lines = []
        for member in team.members:
            if member.discord_id == team.captain:
                member_lines.append(f"👑 <@{member.discord_id}>  —  *Captain*")
            elif member.accepted:
                member_lines.append(f"✅ <@{member.discord_id}>  —  *Joined*")
            else:
                member_lines.append(f"⏳ <@{member.discord_id}>  —  *Pending*")
        container.add_item(ui.TextDisplay("\n".join(member_lines) or "*No members.*"))
        container.add_item(ui.Separator())

        meta = f"**Roster** {len(team.members)}/{size}" if size else f"**Roster** {len(team.members)}"
        if team.expires_at > 0:
            meta += f"   •   **Expires** <t:{int(team.expires_at)}:R>"
        if team.status:
            meta += f"   •   **Status** `{team.status}`"
        container.add_item(ui.TextDisplay(meta))

        accept = ui.Button(
            label="Accept",
            style=ButtonStyle.success,
            custom_id=f"tiv;accept;{self.team_id}",
            emoji="✅",
        )
        accept.callback = self.accept_btn
        decline = ui.Button(
            label="Decline",
            style=ButtonStyle.danger,
            custom_id=f"tiv;decline;{self.team_id}",
            emoji="❌",
        )
        decline.callback = self.decline_btn
        container.add_item(ui.ActionRow(accept, decline))

        self.add_item(container)

    async def _refresh(self, interaction: Interaction) -> None:
        self._build()
        if interaction.message is None:
            return
        try:
            await interaction.message.edit(
                view=self,
                allowed_mentions=AllowedMentions.none(),
            )
        except discord.HTTPException:
            pass

    async def accept_btn(self, interaction: Interaction) -> None:
        season_id = tournament.active_season
        registration = Registration(season_id=season_id)
        db = registration.read()
        user_team_id = db.players.get(str(interaction.user.id))
        team_id = interaction.data["custom_id"].split(";")[2]
        team = db.teams.get(team_id)

        if not team:
            await interaction.response.send_message(
                "This team invitation is no longer active.", ephemeral=True
            )
            return

        if user_team_id != team_id:
            await interaction.response.send_message(
                "You are not on the invitation list for this team.", ephemeral=True
            )
            return

        if registration.is_expired(team):
            await interaction.response.send_message(
                "This invitation has expired.", ephemeral=True
            )
            return

        if registration.has_accepted(str(interaction.user.id)):
            await interaction.response.send_message(
                "You have already accepted.", ephemeral=True
            )
            return

        success = registration.accept(discord_id=str(interaction.user.id))
        if success is None:
            await interaction.response.send_message(
                "This invitation has expired.", ephemeral=True
            )
            return
        if not success:
            await interaction.response.send_message(
                "You are not registered in any team.", ephemeral=True
            )
            return

        role = participant_role(interaction.guild, season_id)
        if role:
            try:
                await interaction.user.add_roles(role)
            except discord.HTTPException:
                pass

        await interaction.response.send_message(
            "You've joined the team! Run `/verify` in the game to finish.",
            ephemeral=True,
        )
        await self._refresh(interaction)

    async def decline_btn(self, interaction: Interaction) -> None:
        season_id = tournament.active_season
        registration = Registration(season_id=season_id)
        db = registration.read()
        user_team_id = db.players.get(str(interaction.user.id))
        team_id = interaction.data["custom_id"].split(";")[2]
        team = db.teams.get(team_id)

        if not team:
            await interaction.response.send_message(
                "This team invitation is no longer active.", ephemeral=True
            )
            return

        if user_team_id != team_id:
            await interaction.response.send_message(
                "You are not on the invitation list for this team.", ephemeral=True
            )
            return

        if team.captain == str(interaction.user.id):
            await interaction.response.send_message(
                "You are the captain, you cannot decline your own team.", ephemeral=True
            )
            return

        role = participant_role(interaction.guild, season_id)
        if role and role in interaction.user.roles:
            try:
                await interaction.user.remove_roles(role)
            except discord.HTTPException:
                pass

        if team.role_id:
            try:
                team_role = interaction.guild.get_role(team.role_id)
                if team_role and team_role in interaction.user.roles:
                    await interaction.user.remove_roles(team_role)
            except discord.HTTPException:
                pass

        updated = registration.decline(str(interaction.user.id))

        await interaction.response.send_message(
            "You've declined the invitation.", ephemeral=True
        )

        if updated is None:
            return

        await self._refresh(interaction)
        await refresh_register_panel(interaction.client)

        if isinstance(interaction.channel, discord.Thread):
            try:
                await interaction.channel.send(
                    f"<@{updated.captain}> {interaction.user.mention} declined. "
                    f"Use `/tournament invite` to invite a replacement.",
                    allowed_mentions=allowed_mentions_for(interaction.user),
                )
            except discord.HTTPException:
                pass