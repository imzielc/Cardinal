"""shared helpers for the discord bot: authority checks and account API calls."""

from __future__ import annotations

import asyncio

import requests
from discord import Interaction, app_commands

from roles import roles
from server.enums import Authority

ACCOUNT_API = "https://account.thecardinal.workers.dev"


async def fetch_account_info(account_id: str) -> dict | None:
    """fetches the account info from the account API. None on any failure."""
    url = f"{ACCOUNT_API}/{account_id}"

    def _fetch() -> dict | None:
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                return None
            return resp.json()
        except Exception:
            return None

    return await asyncio.to_thread(_fetch)


def require(authority: Authority):
    """decorator for authority check"""

    async def examine(interaction: Interaction) -> bool:
        if interaction.user.id == interaction.client.owner_id:
            return True
        if roles.get_authority_level(interaction.user.id) >= authority:
            return True
        await interaction.response.send_message(
            "You are not authorised", ephemeral=True
        )
        return False

    return app_commands.check(examine)