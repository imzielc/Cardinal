from datetime import datetime
import bascenev1

from server.clients import Client, fetch_client
from server.storage import MODS_DIR
from tournament import seasons

from . import patch_method

LOG_FILE = MODS_DIR / "server.log"

def log(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    message = f"[{timestamp}] {message}"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(message + "\n")

@patch_method(bascenev1._session.Session, "on_player_request", initial=True)
def on_player_request(self, player: bascenev1.SessionPlayer) -> bool:
    client = Client(player.inputdevice.client_id, player.get_account_id())
    if not client.authenticity:
        auth_code = client.get_auth_code()
        client.error(f"Your auth code is: {auth_code}\nPlease enter in chat to verify.")
        return False
    return on_player_request.result


@patch_method(bascenev1.DualTeamSession, "on_player_leave", initial=True)
def on_player_leave(self, player: bascenev1.SessionPlayer) -> None:
    identifier = self._player_requested_identifiers.get(player.id)
    if identifier and int(seasons.active_season):
        from tournament.manager import manager

        manager.handle_player_leave(identifier)


@patch_method(bascenev1._hooks, "on_client_joined")
def on_client_joined(client_id: int) -> None:
    client = fetch_client(client_id)
    message = f"{client.name} Joined the server (Addr: {client.address}, uuid: {client.public_uuid})"
    log(message)
