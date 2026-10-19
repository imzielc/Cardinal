"""general slash commands."""

import discord
from discord import Interaction, app_commands
from discord.ext import commands

from discord_bot.client import GameClient
from discord_bot.helpers import require
from roles import roles
from server.enums import Authority, Role


class GeneralCommands(commands.Cog):
    """cog for general slash commands"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.client = GameClient()

    @app_commands.command(name="say")
    @app_commands.describe(message="The text to send")
    @require(Authority.ADMIN)
    async def say(self, interaction: Interaction, message: str) -> None:
        """sends message in game chat"""
        self.client.send_action(
            action="message", text=message, sender=interaction.user.display_name
        )
        await interaction.response.send_message("Done!", ephemeral=True)

    @app_commands.command(name="cmd")
    @app_commands.describe(command="The chat-command to execute")
    async def cmd(self, interaction: Interaction, command: str) -> None:
        """executes a chat command in game."""
        self.client.send_action(
            action="command", command=command, account_id=interaction.user.id
        )
        await interaction.response.send_message("Done!", ephemeral=True)

    @app_commands.command(name="list")
    async def list(self, interaction: Interaction) -> None:
        """lists all the players from game"""
        await interaction.response.defer(ephemeral=True)
        response = await self.client.send_action(action="list", response=True)
        if response is None:
            await interaction.followup.send("The game server did not respond.")
            return
        if not response["players"]:
            await interaction.followup.send("There are no players in the server")
            return
        heads = "{0:^16}{1:^15}\n"
        result = ""
        for player in response["players"]:
            result += heads.format(player["name"], player["client_id"])
        await interaction.followup.send(result)

    @app_commands.command(name="announce")
    @app_commands.describe(
        target="The channel or thread to send to (mention or id)",
        message="The text to send",
        role="Optional role to ping (single role only)",
    )
    @require(Authority.LEADER)
    async def announce(
        self,
        interaction: Interaction,
        target: str,
        message: str,
        role: discord.Role = None,
    ) -> None:
        """sends a bot message to a channel or thread. leader only."""
        target_clean = target.strip().strip("<#>")
        try:
            target_id = int(target_clean)
        except ValueError:
            await interaction.response.send_message(
                "Invalid target. Use a channel/thread mention or id.",
                ephemeral=True,
            )
            return

        try:
            channel = self.bot.get_channel(target_id) or await self.bot.fetch_channel(target_id)
        except Exception:
            channel = None
        if channel is None or not isinstance(channel, discord.abc.Messageable):
            await interaction.response.send_message(
                "Couldn't find that channel or thread.",
                ephemeral=True,
            )
            return

        if role is not None:
            content = f"{role.mention} {message}"
            allowed = discord.AllowedMentions(roles=[role], users=False, everyone=False)
        else:
            content = message
            allowed = discord.AllowedMentions.none()

        try:
            await channel.send(content, allowed_mentions=allowed)
        except discord.HTTPException as e:
            await interaction.response.send_message(
                f"Couldn't send: {e}", ephemeral=True
            )
            return

        print(
            f"[leader] announce: {interaction.user.name} -> "
            f"#{getattr(channel, 'name', target_id)}"
        )
        mention = getattr(channel, "mention", f"<#{target_id}>")
        await interaction.response.send_message(
            f"Sent to {mention}.", ephemeral=True
        )

    @app_commands.command(name="admin")
    @app_commands.describe(user="the user to add/remove")
    @require(Authority.LEADER)
    async def admin(self, interaction: Interaction, user: discord.Member) -> None:
        """to add/remove a user to admins"""
        if roles.has_role(Role.ADMIN, user.id):
            roles.remove(Role.ADMIN, user.id)
            await interaction.response.send_message(
                f"{user.name} has been removed from admins", ephemeral=True
            )
        else:
            roles.add(Role.ADMIN, user.id)
            await interaction.response.send_message(
                f"{user.name} has been added to admins", ephemeral=True
            )

    @app_commands.command(name="owner")
    @app_commands.describe(user="the user to add/remove")
    @require(Authority.HOST)
    async def owner(self, interaction: Interaction, user: discord.Member) -> None:
        """to add/remove a user to owners"""
        if roles.has_role(Role.LEADER, user.id):
            roles.remove(Role.LEADER, user.id)
            await interaction.response.send_message(
                f"{user.name} has been removed from owners", ephemeral=True
            )
        else:
            roles.add(Role.LEADER, user.id)
            await interaction.response.send_message(
                f"{user.name} has been added to owners", ephemeral=True
            )