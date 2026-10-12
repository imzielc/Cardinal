from typing import Any, override

import bascenev1
from bascenev1._dualteamsession import DualTeamSession

from server import utils
from server.clients import Client
from tournament import tournament
from tournament.manager import manager


class TournamentSession(DualTeamSession):
    """dual team session configured for tournament"""

    def __init__(self):
        super().__init__()

    @override
    def on_team_join(self, team: bascenev1.Team) -> None:
        super().on_team_join(team)
        # change the team name to their actual team name.
        team.name = manager.active_match.match.teams[team.id].name
        # if the match was resumed, update the score to what they had.
        team.customdata["score"] = manager.active_match.match.last_scores[team.id]

    @override
    def on_player_request(self, player: bascenev1.SessionPlayer):
        client = Client(
            client_id=player.inputdevice.client_id, account_id=player.get_account_id()
        )
        if (
            manager.active_match
            and not client.account_id in manager.active_match.players
        ):
            # a match is active, if the player is not any of the teams of the match, dont let them join.
            client.error("A match is active. You cannot join.")
            return False

        return super().on_player_request(player)

    def create_scoreboard(self) -> None:
        """ adds score, series texts with their team names."""
        bascenev1.newnode(
            "text",
            attrs={
                "text": "vs",
                "position": (-600, 250),
                "color": (0.5, 0.5, 1),
                "scale": 0.75,
                "h_align": "center",
            },
        )
        team_y_positions = [275, 220]
        for team in manager.active_match.match.teams:
            team.text = bascenev1.newnode(
                "text",
                attrs={
                    "text": f"{team.name}: {manager.active_match.match.last_scores[team.idx]}/{team.series}",
                    "position": (-600, team_y_positions[team.idx]),
                    "color": (1, 1, 0),
                    "scale": 0.8,
                    "h_align": "center",
                },
            )

    @override
    def handlemessage(self, msg: Any) -> Any:
        from bascenev1._lobby import ChangeMessage, PlayerReadyMessage

        if isinstance(msg, PlayerReadyMessage):
            player = msg.chooser.getplayer()
            if not player:
                return
            team = msg.chooser.sessionteam
            identifier = player.get_account_id()
            team_name = manager.players.get(identifier, [None, None])[1]
            if team.name != team_name:
                # if this is not the team of the player, we move him into his team.
                msg.chooser.handlemessage(ChangeMessage("team", 1))
                return

            self._on_player_ready(chooser=msg.chooser)
        else:
            super().handlemessage(msg)

    @override
    def _switch_to_score_screen(self, results: bascenev1.GameResults) -> None:
        if manager.active_match is None:
            # the tournament was cancelled in between, this is a normal session now.
            return super()._switch_to_score_screen(results)

        from bascenev1lib.activity.drawscore import DrawScoreScreenActivity
        from bascenev1lib.activity.dualteamscore import (
            TeamVictoryScoreScreenActivity,
        )
        from bascenev1lib.activity.multiteamvictory import (
            TeamSeriesVictoryScoreScreenActivity,
        )

        winnergroups = results.winnergroups

        # If everyone has the same score, call it a draw.
        if len(winnergroups) < 2:
            self.setactivity(bascenev1.newactivity(DrawScoreScreenActivity))
        else:
            winner = winnergroups[0].teams[0]
            loser = winnergroups[1].teams[0]
            winner.customdata["score"] += 1

            manager.active_match.match.teams[winner.id].score += 1
            manager.active_match.match.last_scores[winner.id] += 1

            # If a team has won, show final victory screen.
            if winner.customdata["score"] >= (self._series_length - 1) / 2 + 1:
                manager.active_match.match.teams[winner.id].series += 1
                manager.active_match.match.last_scores = [0, 0]
                self.setactivity(
                    bascenev1.newactivity(
                        TeamSeriesVictoryScoreScreenActivity,
                        {"winner": winner},
                    )
                )

                if manager.active_match.match.teams[winner.id].series >= tournament.series_length:
                    manager.active_match.match.winner_idx = winner.id
                    manager.active_match.match.loser_idx = loser.id
                    utils.success(
                        message=f"Match concluded. Winner: {winner.name}, Loser: {loser.name}\nResults will be announced in discord."
                    )
                    manager.conclude_active_match()
            else:
                self.setactivity(
                    bascenev1.newactivity(
                        TeamVictoryScoreScreenActivity, {"winner": winner}
                    )
                )
            manager.save_score()
