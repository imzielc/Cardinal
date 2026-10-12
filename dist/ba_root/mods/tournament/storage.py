"""storages for handling tournament data."""

from pathlib import Path
from typing import override

from server.storage import Storage, MODS_DIR
from server.enums import TournamentStage
from tournament.schema import SeasonSchema, TournamentSchema

SEASONS_DIR = MODS_DIR / "tournament" / "seasons"

class Tournament(Storage):
    """reads/writes json files."""

    def __init__(self):
        super().__init__("tournament.json", SEASONS_DIR)

    def bootstrap(self):
        """creates the file if not already existing."""
        if not self.path.exists():
            self.commit(TournamentSchema())

    @property
    def active_season(self) -> str:
        """returns the active season id."""
        return self.read().active_season

    @property
    def series_length(self) -> int:
        """returns the series length of the active season."""
        return self.read().seasons[self.active_season].series.count

    @override
    def read(self, external_path=None) -> TournamentSchema:
        """reads from the file and returns schema"""
        data = super().read(external_path=external_path)
        return TournamentSchema.from_dict(data)

    @override
    def commit(self, data: TournamentSchema, external_path=None) -> None:
        """commits the schema to the file as dict."""
        super().commit(data=data.to_dict(), external_path=external_path)

    def create_season(self, schema: SeasonSchema) -> None:
        """creates the season with the given schema"""
        data = self.read()
        # we get the next season count safely.
        season_id = str(max([int(id) for id in data.seasons], default=0) + 1)

        data.seasons[season_id] = schema
        # activate this season
        data.active_season = season_id

        self.commit(data)

        # create the directory of the season
        season_dir = self.directory / season_id
        season_dir.mkdir(parents=True, exist_ok=True)

    def get_season(self, season_id: str) -> SeasonSchema | None:
        """returns a seasonschmea by its id"""
        data = self.read()
        season = data.seasons.get(season_id)

        if season:
            return season

    def update_season(self, season_id: str, season: SeasonSchema) -> None:
        """updates a season using the given seasonschema."""
        data = self.read()
        data.seasons[season_id] = season
        self.commit(data)

    def cancel_season(self, season_id: str) -> bool:
        """cancels a season: it stops being the active one and its registrations close.
        its files stay as they are. returns whether there was such a season."""
        data = self.read()
        season = data.seasons.get(season_id)
        if season is None:
            return False
        season.stage = TournamentStage.CANCELLED
        data.registrations_status = False
        if data.active_season == season_id:
            data.active_season = "0"
        self.commit(data)
        return True

    @property
    def are_registrations_open(self):
        """as the name says"""
        return self.read().registrations_status

    def open_registrations(self):
        """opens the registrations."""
        db = self.read()
        db.registrations_status = True
        self.commit(db)

    def close_registrations(self):
        """closes the registrations."""
        db = self.read()
        db.registrations_status = False
        self.commit(db)


tournament = Tournament()
