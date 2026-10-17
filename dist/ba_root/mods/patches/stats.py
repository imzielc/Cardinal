from bascenev1._activitytypes import ScoreScreenActivity

from server import config
from stats import update_stats
from stats import stats
from . import patch_method
import os, bascenev1


@patch_method(ScoreScreenActivity, "on_begin", initial=True)
def new_on_begin(self) -> None:
    """modified."""
    if os.getenv("TOURNAMENT_MATCH") is not None:
        if stats.tournament_path.is_dir():
            stats.tournament_path = (
                stats.tournament_path / os.getenv("TOURNAMENT_MATCH") / "stats.json"
            )
        if hasattr(bascenev1, "is_elimination") and not bascenev1.is_elimination:
            return
        update_stats(self._stats, tournament=True)
        return
    if config.stats.enable:
        update_stats(self._stats)
