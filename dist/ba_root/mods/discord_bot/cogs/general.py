"""general slash commands."""

import asyncio
import io
import subprocess

import discord
from discord import Interaction, app_commands
from discord.ext import commands

from discord_bot.client import GameClient
from discord_bot.helpers import require
from roles import roles
from server.enums import Authority, Role
from server.storage import MODS_DIR

SSH_TIMEOUT_SECONDS = 30
SSH_OUTPUT_INLINE_MAX = 1800
SSH_OUTPUT_PREVIEW_CHARS = 800


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

    @app_commands.command(name="ssh")
    @app_commands.describe(
        command="Shell command to run in the project root (30s timeout)"
    )
    @require(Authority.LEADER)
    async def ssh(self, interaction: Interaction, command: str) -> None:
        """runs a shell command on the server, in the project root. leader only.

        the working directory is the repo root (where .git, config.toml,
        mods_config.json live). shell features like pipes, redirects, &&,
        and subshells all work like they would over ssh. cd does not persist
        between calls since each invocation is a fresh subprocess.
        """
        command = command.strip()
        if not command:
            await interaction.response.send_message(
                "Empty command.", ephemeral=True
            )
            return

        # repo root: mods/ -> ba_root/ -> dist/ -> project_root
        repo_root = str(MODS_DIR.parents[2])

        await interaction.response.defer(ephemeral=True)

        def _run() -> tuple[int, str, str]:
            try:
                result = subprocess.run(
                    command,
                    shell=True,
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                    timeout=SSH_TIMEOUT_SECONDS,
                )
                return result.returncode, result.stdout or "", result.stderr or ""
            except subprocess.TimeoutExpired:
                return (
                    -1,
                    "",
                    f"Command timed out after {SSH_TIMEOUT_SECONDS} seconds.",
                )
            except Exception as e:
                return -2, "", f"Error: {e}"

        returncode, stdout, stderr = await asyncio.to_thread(_run)

        combined = ""
        if stdout:
            combined += stdout
        if stderr:
            if combined and not combined.endswith("\n"):
                combined += "\n"
            combined += stderr
        if not combined:
            combined = "(no output)"

        # run the command and its output inside the same code block, so a
        # user reading the reply sees exactly what produced what.
        body = f"$ {command}\n\n{combined}"
        # a stray ``` inside command output would close our code block early.
        body = body.replace("```", "'''")

        emoji = "✅" if returncode == 0 else "❌"
        header = f"{emoji} exit `{returncode}`"

        print(
            f"[leader] ssh: {interaction.user.name}: {command} (exit {returncode})"
        )

        # short output: inline in a code block.
        if len(body) <= SSH_OUTPUT_INLINE_MAX:
            await interaction.followup.send(
                f"{header}\n```\n{body}\n```",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        # long output: preview inline, full output as a file.
        preview = body[:SSH_OUTPUT_PREVIEW_CHARS] + "\n... (see attached output.txt)"
        buffer = io.BytesIO(body.encode("utf-8"))
        buffer.seek(0)
        file = discord.File(buffer, filename="output.txt")
        await interaction.followup.send(
            f"{header}\n```\n{preview}\n```",
            file=file,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
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