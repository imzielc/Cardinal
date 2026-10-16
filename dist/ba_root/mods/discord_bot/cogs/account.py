"""account link / status / unlink commands."""

import discord
from discord import Interaction, app_commands
from discord.ext import commands

from discord_bot.helpers import fetch_account_info, require
from discord_bot.ui import LinkedAccountView
from server.enums import Authority
from tournament import tournament
from tournament.link import link
from tournament.registration import Registration


class AccountCommands(
    commands.GroupCog,
    group_name="account",
    group_description="Manage the link between your discord and in-game account.",
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()

    @app_commands.command(name="link")
    async def link_account(self, interaction: Interaction) -> None:
        """links your discord account to your in-game account. required before registering."""
        if link.is_linked(str(interaction.user.id)):
            await interaction.response.send_message(
                "You're already linked. Nothing to do!", ephemeral=True
            )
            return

        code = link.issue_code(str(interaction.user.id))
        await interaction.response.send_message(
            f"Your link code is `{code}`. It expires in **24 hours**.\n"
            f"Join the game server and run `/link {code}` to finish.",
            ephemeral=True,
        )

    @app_commands.command(name="status")
    async def account_status(self, interaction: Interaction) -> None:
        """shows the game account linked to your discord."""
        account_id = link.account_of(str(interaction.user.id))
        if not account_id:
            await interaction.response.send_message(
                "You haven't linked an account yet. Use `/account link`.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        data = await fetch_account_info(account_id)
        if data is None:
            await interaction.followup.send(
                "Couldn't fetch your account info right now. Try again later or contact an admin.",
                ephemeral=True,
            )
            return

        view = LinkedAccountView(data, discord_id=str(interaction.user.id))
        await interaction.followup.send(view=view, ephemeral=True)

    @app_commands.command(name="whois")
    @app_commands.describe(user="the user to look up")
    @require(Authority.ADMIN)
    async def account_whois(
        self, interaction: Interaction, user: discord.Member
    ) -> None:
        """shows which game account a discord user has linked (admin only)."""
        account_id = link.account_of(str(user.id))
        if not account_id:
            await interaction.response.send_message(
                f"{user.mention} hasn't linked an account.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        data = await fetch_account_info(account_id)
        if data is None:
            await interaction.followup.send(
                f"Couldn't fetch account info for {user.mention}. Try again later.",
                ephemeral=True,
            )
            return

        view = LinkedAccountView(data, discord_id=str(user.id))
        await interaction.followup.send(view=view, ephemeral=True)

    @app_commands.command(name="unlink")
    async def unlink_account(self, interaction: Interaction) -> None:
        """removes the link between your discord and in-game account."""
        discord_id = str(interaction.user.id)
        if not link.is_linked(discord_id):
            await interaction.response.send_message(
                "You don't have an account linked.", ephemeral=True
            )
            return

        # block while on an active tournament team — that would leave the roster stale.
        season_id = tournament.active_season
        if int(season_id):
            team = Registration(season_id=season_id).team_of(discord_id)
            if team:
                await interaction.response.send_message(
                    f"You're on team **{team.id}** for the ongoing tournament. "
                    f"Leave first with `/tournament leave`.",
                    ephemeral=True,
                )
                return

        link.unlink(discord_id)
        await interaction.response.send_message(
            "Your account has been unlinked. You can link again with `/account link`.",
            ephemeral=True,
        )