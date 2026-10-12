import os

import bascenev1

from server.enums import Status
from tournament.brackets import Brackets
from tournament.schema import MatchSchema, PendingMatchSchema


class Manager:
    """manager class for tournament matches."""

    def __init__(self):
        self.pending_matches: dict[str, PendingMatchSchema] = {}
        self.players = {}
        self.ready_players = {}
        self.pause_players = {}

        self.season_id: str = "0"
        self.brackets: Brackets | None = None

        self.active_match: PendingMatchSchema | None = None

    def initialize(self, season_id: str):
        """initializes the manager."""
        self.season_id = season_id
        if not int(season_id):
            # no season is going on, nothing to load (and no junk season folder).
            self.brackets = None
            self.pending_matches.clear()
            self.players.clear()
            return
        self.brackets = Brackets(season_id=season_id)
        # repairs a bracket a crash only saved partly, before the matches are loaded.
        self.brackets.refresh()
        self.load_pending_matches()

    def sync(self) -> None:
        """picks up a season that was created, cancelled or moved on after the server started."""
        from tournament import tournament

        if self.active_match:
            return
        if tournament.active_season != self.season_id:
            self.initialize(tournament.active_season)
        elif self.brackets:
            # a win given from the bot, or a new round, can change what is pending.
            self.load_pending_matches()

    def cancel(self) -> None:
        """forgets the tournament, the season was cancelled. a match that is being played is stopped
        and the server restarts, like it does when a match ends."""
        was_playing = self.active_match is not None
        self.active_match = None
        self.pending_matches.clear()
        self.players.clear()
        self.ready_players.clear()
        self.pause_players.clear()
        self.brackets = None
        self.season_id = "0"
        if was_playing:
            bascenev1.broadcastmessage("The tournament has been cancelled.")
            self.end_tournament_session()

    def load_pending_matches(self):
        """load all pending matches from the database."""
        self.pending_matches.clear()
        self.players.clear()

        # we need to load all the matches from the rounds.
        matches = self.brackets.list_matches()
        for key, match in matches.items():
            if match.status == Status.PENDING:
                self.register_pending_match(
                    key=key,
                    match=match,
                )

    def register_pending_match(
        self,
        key: str,
        match: MatchSchema,
    ):
        """registers a pending match."""
        team1 = match.teams[0].name
        team2 = match.teams[1].name

        players1 = self.extract_from_team_players(team1, key="account_id")
        players2 = self.extract_from_team_players(team2, key="account_id")

        self.pending_matches[key] = PendingMatchSchema(
            key=key,
            match=match,
            uuids=self.extract_from_team_players(team1, key="device_uuid")
            + self.extract_from_team_players(team2, key="device_uuid"),
            players=players1 + players2,
        )

        if key not in self.ready_players:
            self.ready_players[key] = set()
        if key not in self.pause_players:
            self.pause_players[key] = set()
        for team, players in ((team1, players1), (team2, players2)):
            for player in players:
                self.players[player] = [key, team]

    def extract_from_team_players(self, team_id: str, key: str) -> list:
        """extracts the key from team dict players."""
        return [
            getattr(member, key) for member in self.brackets.get_team(team_id=team_id).members
        ]

    def handle_player_ready(self, account_id: str, uuid: str) -> dict:
        """handles the player ready event."""
        self.sync()
        match_id = self.players.get(account_id, [None, None])[0]
        if not match_id:
            return {
                "status": "error",
                "message": "You are not registered for any matches.",
            }

        # if a match is already active, we cannot accept the player.
        if self.active_match:
            return {"status": "error", "message": "A match is already active."}

        if account_id in self.ready_players.get(match_id, set()):
            return {"status": "error", "message": "You are already ready."}

        match = self.pending_matches[match_id]

        if uuid not in match.uuids:
            return {"status": "error", "message": "Your device uuid seems to have changed, contact the server admins."}

        self.ready_players[match_id].add(account_id)

        result = {"status": "success", "message": "You have been marked as ready."}

        # if all players of a match are ready, we can start the match.
        if self.ready_players[match_id] == set(match.players):
            self.active_match = match
            # clean-up them from the pending matches and ready players.
            del self.pending_matches[match_id]
            del self.ready_players[match_id]
            with bascenev1.ContextRef.empty():
                bascenev1.apptimer(5.0, self.start_tournament_session)
            result["start"] = True

        return result

    def handle_player_pause(self, account_id: str) -> dict:
        if not self.active_match:
            return {
                "status": "error",
                "message": "There is no active match."
            }

        if account_id not in self.active_match.players:
            return {
                "status": "error",
                "message": "You are not a member of the active match."
            }

        match_id = self.active_match.key
        self.pause_players.setdefault(match_id, set())
        if account_id in self.pause_players[match_id]:
            return {
                "status": "error",
                "message": "You are already marked for match pause."
            }

        result = {"status": "success", "message": "You have been marked for match pause."}
        self.pause_players[match_id].add(account_id)
        if self.pause_players[match_id] == set(self.active_match.players):
            self.save_score()
            # if all players are marked for pause, we can pause the match.
            # for now, we will just restart the server.
            self.end_tournament_session()
            result["pause"] = True
        return result

    def save_score(self) -> None:
        """saves the score of active match in database."""
        if not self.active_match:
            return

        if self.active_match.match.group_id:
            # its a group stage match.
            gs = self.brackets.read_gs()
            gs.groups[self.active_match.match.group_id].rounds[self.active_match.match.round_id].matches[self.active_match.match.match_id] = self.active_match.match
            self.brackets.commit_gs(gs)
        else:
            # its a knockout stage match.
            self.brackets.save_match(self.active_match.match)

    def handle_player_leave(self, account_id: str) -> None:
        """handles the player leaving."""
        match_id = self.players.get(account_id, [None, None])[0]
        if not match_id:
            return

        if self.active_match:
            return

        if account_id in self.ready_players.get(match_id, set()):
            self.ready_players[match_id].remove(account_id)

    def conclude_active_match(self) -> None:
        """concludes the active match."""
        if not self.active_match:
            return

        self.save_score()

        if self.active_match.match.group_id:
            self.brackets.update_gs_match(
                match = self.active_match.match,
            )
        else:
            self.brackets.update_match(match=self.active_match.match)

        self.brackets.send_results(self.active_match.match, self.active_match.key)
        self.brackets.send_players_dashboard()
        self.end_tournament_session()

    def start_tournament_session(self) -> None:
        """starts the tournament session."""
        # set os env to stop server from restarting in between a match and collect player stats.
        os.environ["TOURNAMENT_MATCH"] = self.season_id
        # send the announcement to the discord server
        self.brackets.announce_match_start(self.active_match.match.teams[0].name, self.active_match.match.teams[1].name)
        from .activity import TournamentTransitionActivity

        session = bascenev1.get_foreground_host_session()
        with session.context:
            session.setactivity(bascenev1.newactivity(TournamentTransitionActivity))

    def end_tournament_session(self) -> None:
        """ends the tournament session."""
        bascenev1.broadcastmessage("Server will restart in 10 seconds.")
        with bascenev1.ContextRef.empty():
            bascenev1.apptimer(10.0, self._shutdown)

    def _shutdown(self) -> None:
        os.environ.pop("TOURNAMENT_MATCH", None)
        bascenev1.app.classic.server._execute_shutdown()


manager = Manager()
