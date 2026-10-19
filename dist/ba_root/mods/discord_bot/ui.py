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
from tournament.waitlist import Waitlist

TEAM_NAME_MAX_LENGTH = 40
THREAD_ARCHIVE_MINUTES = 1440
THREAD_NAME_SUFFIX_MAX = 6
TEAM_ROLE_PREFIX = "[Team] "

REGISTER_BUTTON_ID = "tournament;register"


def team_role_name(team_name: str) -> str:
    """the discord role name for a team. discord caps role names at 100 chars."""
    return f"{TEAM_ROLE_PREFIX}{team_name}"[:100]


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


def _expiry_label(expires_at: float) -> str:
    """a compact human label for how much time a team has left before it expires.

    shows hours for anything over an hour, minutes for anything under, and
    "Expiring soon" in the last minute. matches the invite_expire_hours
    setting directly rather than the relative timestamp discord would render.
    """
    remaining = expires_at - datetime.now(timezone.utc).timestamp()
    if remaining <= 0:
        return "**Expired**"
    if remaining >= 3600:
        hours = int(remaining // 3600)
        return f"**Expires in** {hours}h"
    if remaining >= 60:
        minutes = int(remaining // 60)
        return f"**Expires in** {minutes}m"
    return "**Expiring soon**"


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
            name=team_role_name(team_name),
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


async def find_archived_thread(channel, name: str):
    """looks for an archived private thread with this name in the channel."""
    try:
        async for thread in channel.archived_threads(limit=100, private=True):
            if thread.name == name and thread.archived:
                return thread
    except (discord.Forbidden, discord.HTTPException, AttributeError):
        pass
    return None


async def _allow_thread_chat(thread) -> None:
    """allow thread members to send messages in the thread.

    discord threads inherit permissions from their parent channel. if the
    registration channel restricts @everyone from sending messages, then
    private thread members are blocked too. overriding at the thread level
    fixes this. safe because the thread is private: only added members and
    moderators can even see it, so relaxing @everyone here has no wider effect.

    skips the API call if the override is already in place, so calling this
    on an already-fixed thread is free (no audit log entry, no HTTP request).
    """
    if thread.guild is None:
        return
    default_role = thread.guild.default_role
    overwrite = thread.overwrites.get(default_role)
    if overwrite is not None and overwrite.send_messages is True:
        return
    try:
        await thread.set_permissions(
            default_role,
            send_messages=True,
        )
    except discord.HTTPException:
        pass


async def create_team_thread(channel, team_name: str, season_id: str,
                             captain_user, invited_members: list):
    """returns the private thread for the team, reusing an archived one if it exists."""
    if channel is None or not hasattr(channel, "create_thread"):
        return None

    name = thread_name(team_name, season_id)

    existing = await find_archived_thread(channel, name)
    if existing is not None:
        try:
            if existing.locked:
                await existing.edit(archived=False, locked=False)
            else:
                await existing.edit(archived=False)
            await existing.send("**Team re-created.** Fresh invitation below.")
            try:
                await existing.add_user(captain_user)
            except discord.HTTPException:
                pass
            for member in invited_members:
                try:
                    await existing.add_user(member)
                except discord.HTTPException:
                    pass
            # ensure members can chat (parent-channel restriction may apply).
            await _allow_thread_chat(existing)
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
        await thread.add_user(captain_user)
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

    # ensure members can chat (parent-channel restriction may apply).
    await _allow_thread_chat(thread)
    return thread


# ---- register panel ----


class RegisterView(ui.LayoutView):
    """the persistent registration panel. rebuilt from state every time it is shown.

    the button's label reflects the current state:
      - no season:         disabled
      - regs open:         "Register"
      - regs closed:       "Join Waitlist"
    the actual behavior is decided in the modal on submit, so a stale button
    can't produce a wrong outcome.
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self._build()

    def _build(self) -> None:
        self.clear_items()

        season_id = tournament.active_season
        season = tournament.get_season(season_id) if int(season_id) else None
        regs_open = season is not None and tournament.are_registrations_open

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

            waitlist_count = 0
            try:
                waitlist_count = len(Waitlist(season_id))
            except Exception:
                pass

            container.add_item(
                ui.TextDisplay(
                    f"**Season** `{season_id}`\n"
                    f"**Format** {type_str} • {series_str}\n"
                    f"**Mode** {mode_str}\n"
                    f"**Teams** {total} registered • {verified} verified\n"
                    f"**Waitlist** {waitlist_count} queued"
                )
            )
            container.add_item(ui.Separator())

            if regs_open:
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
            else:
                container.add_item(
                    ui.TextDisplay(
                        "🔒 Registrations are closed. Click below to join the waitlist — "
                        "if a slot opens up you'll be promoted automatically."
                    )
                )
                button = ui.Button(
                    label="Join Waitlist",
                    style=ButtonStyle.primary,
                    emoji="<:register:1560371261459267654>",
                    custom_id=REGISTER_BUTTON_ID,
                )
            button.callback = self.register_btn

        container.add_item(ui.ActionRow(button))
        self.add_item(container)

    async def register_btn(self, interaction: Interaction) -> None:
        season_id = tournament.active_season
        if not int(season_id):
            await interaction.response.send_message(
                "There is no tournament ongoing right now.", ephemeral=True
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

        # state decides: open -> register, closed -> waitlist.
        if not tournament.are_registrations_open:
            wl = Waitlist(self.season_id)
            if wl.is_waitlisted(str(interaction.user.id)):
                await interaction.response.send_message(
                    "You are already on the waitlist.", ephemeral=True
                )
                return
            if not wl.add(str(interaction.user.id), self.team_name.value, []):
                await interaction.response.send_message(
                    "Could not add you to the waitlist.", ephemeral=True
                )
                return
            pos = wl.position_of(str(interaction.user.id)) or 0
            await interaction.response.send_message(
                f"Registrations are closed. You've been added to the waitlist at position **{pos}**.",
                ephemeral=True,
            )
            await refresh_register_panel(interaction.client)
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
        self.expected = size
        self.select = ui.Label(
            text="Select your teammates",
            component=ui.UserSelect(
                placeholder=f"Select {size}", min_values=size, max_values=size
            ),
        )
        self.add_item(self.select)

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

        if len(invited_members) != self.expected:
            await interaction.response.send_message(
                f"Select exactly {self.expected} teammate(s) for this format.",
                ephemeral=True,
            )
            return

        if any(member.id == interaction.user.id for member in invited_members):
            await interaction.response.send_message(
                "You cannot include yourself as a teammate.", ephemeral=True
            )
            return

        if any(member.bot for member in invited_members):
            await interaction.response.send_message(
                "You cannot invite bots as teammates.", ephemeral=True
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

        invited_ids = [str(m.id) for m in invited_members]

        # state decides: open -> register, closed -> waitlist.
        if not tournament.are_registrations_open:
            wl = Waitlist(self.season_id)
            if wl.is_waitlisted(str(interaction.user.id)):
                await interaction.response.send_message(
                    "You are already on the waitlist.", ephemeral=True
                )
                return
            if not wl.add(str(interaction.user.id), self.team_name.value, invited_ids):
                await interaction.response.send_message(
                    "Could not add you to the waitlist.", ephemeral=True
                )
                return
            pos = wl.position_of(str(interaction.user.id)) or 0
            await interaction.response.send_message(
                f"Registrations are closed. You've been added to the waitlist at position **{pos}**.",
                ephemeral=True,
            )
            await refresh_register_panel(interaction.client)
            return

        await interaction.response.defer(ephemeral=True)

        team_id = registration.register(
            team_name=self.team_name.value,
            captain_discord_id=str(interaction.user.id),
            invited_members=invited_ids,
        )

        if team_id is None:
            await interaction.followup.send(
                "The name you have chosen is already taken.", ephemeral=True
            )
            return

        if not team_id:
            await interaction.followup.send(
                "Failed to register. Check the roster size and make sure nobody is on another team.",
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

        thread = await create_team_thread(
            interaction.channel,
            self.team_name.value,
            self.season_id,
            interaction.user,
            invited_members,
        )

        team = registration.get_team(team_id)
        invite_text = (
            f"{' '.join(member.mention for member in invited_members)}, "
            f"you have been invited to join **{self.team_name.value}** by {interaction.user.mention}."
        )
        view = invitation_view(team, self.season_id, invite_text=invite_text)

        target = thread if thread else interaction.channel
        try:
            invitation_message = await target.send(
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
            registration.set_invitation_message(team_id, invitation_message.id)

        await interaction.followup.send(
            f"Team **{self.team_name.value}** created! "
            f"Teammates must accept the invite, then everyone runs `/verify` in-game.",
            ephemeral=True,
        )
        await refresh_register_panel(interaction.client)


# team invitation view for teammates to accept/reject (components v2).


class TeamInvitationView(ui.LayoutView):
    """accept or reject a team invitation, rendered with components v2.

    three states, driven by the team's data:
      - IN_INVITATION   -- not everyone has accepted. buttons enabled.
      - UNVERIFIED      -- everyone accepted. buttons disabled, per-member verify shown.
      - VERIFIED        -- everyone verified. no buttons at all, view is final.
    """

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

        if team is None:
            container = ui.Container()
            container.add_item(ui.TextDisplay(f"### Team invitation — **{self.team_id}**"))
            container.add_item(ui.TextDisplay("*This invitation is no longer active.*"))
            self.add_item(container)
            return

        all_accepted = all(m.accepted for m in team.members)
        all_verified = all(m.device_uuid for m in team.members)
        verified_count = sum(1 for m in team.members if m.device_uuid)
        total = len(team.members)

        if not self.invite_text:
            invited = [m for m in team.members if m.discord_id != team.captain]
            if invited:
                mentions = " ".join(f"<@{m.discord_id}>" for m in invited)
                self.invite_text = (
                    f"{mentions}, you have been invited to join **{team.id}** "
                    f"by <@{team.captain}>."
                )

        container = ui.Container(
            accent_color=discord.Color.green() if all_verified else discord.Color.blurple()
        )

        if self.invite_text:
            container.add_item(ui.TextDisplay(self.invite_text))
            container.add_item(ui.Separator())

        container.add_item(ui.TextDisplay(f"### Team invitation — **{team.id}**"))

        if all_verified:
            container.add_item(
                ui.TextDisplay("Everyone has joined and verified. Team is ready.")
            )
            container.add_item(ui.Separator())
        elif all_accepted:
            container.add_item(
                ui.TextDisplay("Everyone has joined. Waiting for verification.")
            )
            container.add_item(ui.Separator())

        member_lines = []
        for member in team.members:
            is_captain = member.discord_id == team.captain
            verified = bool(member.device_uuid)

            if is_captain:
                base = "Captain"
            elif member.accepted:
                base = "Joined"
            else:
                base = "Pending"

            parts = [f"<@{member.discord_id}>", base]
            if verified:
                parts.append("Verified")
            elif member.accepted or is_captain:
                parts.append("Awaiting verification")
            member_lines.append(" · ".join(parts))

        container.add_item(ui.TextDisplay("\n".join(member_lines)))
        container.add_item(ui.Separator())

        # the footer: always carries the expiry countdown while the team can
        # still expire (i.e. not fully verified). the countdown is relevant
        # in both the pending state AND the "all accepted, waiting for
        # verification" state, since neither is exempt from the timer.
        meta = f"**Roster** {total}/{size}" if size else f"**Roster** {total}"
        if all_verified:
            meta += " · All verified"
        else:
            if all_accepted:
                meta += f" · {verified_count}/{total} verified"
            if team.expires_at > 0:
                meta += f" · {_expiry_label(team.expires_at)}"
        container.add_item(ui.TextDisplay(meta))

        if all_verified:
            self.add_item(container)
            return

        if all_accepted:
            accept = ui.Button(
                label="Accept",
                style=ButtonStyle.success,
                custom_id=f"tiv;accept;{self.team_id}",
                disabled=True,
            )
            decline = ui.Button(
                label="Decline",
                style=ButtonStyle.danger,
                custom_id=f"tiv;decline;{self.team_id}",
                disabled=True,
            )
        else:
            accept = ui.Button(
                label="Accept",
                style=ButtonStyle.success,
                custom_id=f"tiv;accept;{self.team_id}",
            )
            accept.callback = self.accept_btn
            decline = ui.Button(
                label="Decline",
                style=ButtonStyle.danger,
                custom_id=f"tiv;decline;{self.team_id}",
            )
            decline.callback = self.decline_btn

        container.add_item(ui.ActionRow(accept, decline))
        self.add_item(container)

    async def _edit_interaction_message(self, interaction: Interaction) -> None:
        """rebuilds the view from state and edits the message that triggered the interaction.

        named to avoid colliding with discord.py's own View._refresh, which is
        called internally by the library with a different signature.
        """
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

        if team.status == TeamStatus.VERIFIED:
            await interaction.response.send_message(
                "Your team is already verified.", ephemeral=True
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
        await self._edit_interaction_message(interaction)

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

        if team.status == TeamStatus.VERIFIED:
            await interaction.response.send_message(
                "Your team is already verified. Contact an admin if something needs to change.",
                ephemeral=True,
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

        await self._edit_interaction_message(interaction)
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