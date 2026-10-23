"""read and write the running server's config from discord."""

import json

from discord import Interaction, app_commands
from discord.ext import commands

from discord_bot.helpers import require
from server import config
from server.enums import Authority

# paths that need more than an in-process re-read to take effect.
RESTART_NOTES = {
    "api.enable": "needs a game restart — the loader only checks at boot.",
    "discord.enable": "needs a game restart — the bot is spawned at boot.",
    "discord.token": "needs a game restart — the bot is spawned at boot.",
    "discord.port": "needs a game restart.",
    "discord.bot_port": "needs a game restart.",
}


def _parse_value(raw: str):
    raw = raw.strip()
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


def _read_path(data: dict, parts: list[str]):
    """walks data to parts, returns (value, missing_key). value is None on miss."""
    node = data
    for p in parts:
        if not isinstance(node, dict) or p not in node:
            return None, p
        node = node[p]
    return node, None


class ConfigCommands(
    commands.GroupCog,
    group_name="config",
    group_description="Read and write the running server's config.",
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()

    @app_commands.command(name="get")
    @app_commands.describe(path="Dot-separated config path, e.g. api.dev_reload")
    @require(Authority.LEADER)
    async def config_get(self, interaction: Interaction, path: str) -> None:
        """shows the current value of a config key."""
        parts = [p for p in path.strip().split(".") if p]
        if not parts:
            await interaction.response.send_message("Empty path.", ephemeral=True)
            return

        value, missing = _read_path(config.read(), parts)
        if missing is not None:
            await interaction.response.send_message(
                f"`{path}` has no key `{missing}`.", ephemeral=True
            )
            return

        pretty = json.dumps(value, indent=2, default=str)
        if len(pretty) > 1800:
            pretty = pretty[:1800] + "\n... (truncated)"
        await interaction.response.send_message(
            f"`{path}`:\n```json\n{pretty}\n```", ephemeral=True
        )

    @app_commands.command(name="set")
    @app_commands.describe(
        path="Dot-separated config path, e.g. api.dev_reload",
        value="Value to write. Parsed as JSON when possible (true/false/123).",
    )
    @require(Authority.LEADER)
    async def config_set(
        self, interaction: Interaction, path: str, value: str
    ) -> None:
        """writes a config value to the live config file."""
        parts = [p for p in path.strip().split(".") if p]
        if not parts:
            await interaction.response.send_message("Empty path.", ephemeral=True)
            return

        parsed = _parse_value(value)
        config.set_nested(parts, parsed)

        note = RESTART_NOTES.get(".".join(parts), "")
        if parts == ["api", "dev_reload"]:
            note = "applies to the API within 1 second."
        suffix = f"\n{note}" if note else ""

        await interaction.response.send_message(
            f"Set `{path}` to `{json.dumps(parsed, default=str)}`.{suffix}",
            ephemeral=True,
        )