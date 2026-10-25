"""tournament-related commands."""

from server.clients import Client
from tournament import seasons
from tournament.link import link
from tournament.manager import manager
from tournament.registration import Registration

from . import on_command


@on_command(name="/ready")
def ready(client: Client):
    """marks the player as ready and starts the match if everyone is ready."""
    season_id = seasons.active_season
    if not int(season_id):
        client.error("There is no tournament ongoing.")
        return
    response = manager.handle_player_ready(account_id=client.account_id, uuid=client.public_uuid)
    if response["status"] == "success":
        client.success(response["message"])
        if response.get("start", False):
            from server.utils import success

            success(
                message=f"Tournament match between {manager.active_match.match.teams[0].name} and {manager.active_match.match.teams[1].name} is starting in 2 seconds."
            )
        return
    if response["status"] == "error":
        client.error(response["message"])


@on_command(name="/mpause")
def pause(client: Client):
    """pauses the match if everyone agrees."""
    season_id = seasons.active_season
    if not int(season_id):
        client.error("There is no tournament ongoing.")
        return
    response = manager.handle_player_pause(account_id=client.account_id)
    if response["status"] == "success":
        client.success(response["message"])
        if response.get("pause", False):
            from server.utils import success

            success(
                message=f"Tournament match between {manager.active_match.match.teams[0].name} and {manager.active_match.match.teams[1].name} has been paused."
            )
        return
    if response["status"] == "error":
        client.error(response["message"])


@on_command(name="/link", usage="/link <code>")
def link_account(client: Client, args: list[str]):
    """links this game account to your discord account."""
    if not args:
        client.error("Usage: /link <code>")
        return
    code = args[0]
    discord_id = link.redeem(code, account_id=client.account_id)
    if discord_id is None:
        client.error("That code is invalid or has expired, use /link on discord to get a new one.")
        return
    client.success("Linked! You can now register in discord.")


@on_command(name="/verify")
def verify(client: Client):
    """finishes the tournament registration for this game account."""
    season_id = seasons.active_season
    if not int(season_id):
        client.error("There is no tournament ongoing.")
        return

    registration = Registration(season_id=season_id)
    status = registration.verify(
        account_id=client.account_id,
        device_uuid=client.public_uuid,
    )
    if status is True:
        client.success("You're verified!")
        return
    if status == "already":
        client.error("You're already verified.")
        return
    if status == "not_registered":
        # give a reason instead of a blunt "not registered".
        discord_id = link.discord_of(client.account_id)
        if discord_id is None:
            client.error("This game account isn't linked. Run /account link in discord.")
            return
        team = registration.team_of(discord_id)
        if team is None:
            client.error("You're not on a tournament team. Register in discord first.")
            return
        # on a team, but with a different account.
        client.error("Your discord is on a team with a different account. Contact an admin.")
        return
    client.error("Something went wrong. Contact an admin.")