from . import patch_method
from bascenev1._activity import Activity
from bascenev1lib.game.elimination import EliminationGame
from utilities.endvote import EndVote
from utilities.team_balancer import check_team_balance
from time import monotonic
from utilities import server_text
import bascenev1

@patch_method(Activity, "end", initial=True)
def end(*args, **kwargs):
    # if there is an end vote ongoing, just close it.
    EndVote.end()
    # balance teams if needed.
    check_team_balance()

@patch_method(Activity, "on_begin", initial=True)
def on_begin(*args, **kwargs):
    # make our server text.
    server_text.make_server_text()
    # don't allow player's to start endvote if the game just started, for that. log the time.
    EndVote.relaxation = monotonic() + 60  # don't allow for initial 60 seconds

    # we need to store what activity it is, so we can avoid counting kills in the wrong activity for tournament matches.
    activity = bascenev1.getactivity()
    actlow = str(activity).lower()
    if "elimination" in actlow:
        bascenev1.is_elimination = True
        return
    elif "scorescreen" in actlow:
        return

    bascenev1.is_elimination = False

@patch_method(EliminationGame, "on_player_leave")
def on_player_leave(self, player):
    # balance the lives if the player had any.
    if player.lives > 0:
        from utilities.lives_balancer import balance_lives
        balance_lives(self, player)
    # return original method result
    return on_player_leave.original(self, player)