"""storages for handling tournament data."""

import shutil
from pathlib import Path
from typing import override

from server.storage import Storage, DATA_DIR
from server.enums import TournamentStage
from tournament.schema import SeasonSchema, SeasonsSchema

# seasons live under DATA_DIR, not MODS_DIR — the mods tree holds code only.
SEASONS_DIR = DATA_DIR / "tournament" / "seasons"


class Seasons(Storage):
    """reads/writes season state."""

    def __init__(self):
        super().__init__("seasons.json", SEASONS_DIR)

    def bootstrap(self) -> None:
        """creates the file if missing, migrates from the old tournament.json."""
        old_file = self.directory / "tournament.json"
        if old_file.exists() and not self.path.exists():
            # keep the original as .old so a bad migration can be undone by hand.
            shutil.copy2(old_file, self.path)
            old_file.rename(old_file.with_suffix(".json.old"))

        if not self.path.exists():
            self.commit(SeasonsSchema())
            return

        raw = super().read()
        if "registrations_status" in raw:
            flag = bool(raw.pop("registrations_status"))
            for season in raw.get("seasons", {}).values():
                if season.get("stage") == "REGISTRATION" and not flag:
                    season["stage"] = "NONE"
            super().commit(raw)

    @property
    def active_season(self) -> str:
        """returns the active season id."""
        return self.read().active_season

    @property
    def series_length(self) -> int:
        """returns the series length of the active season."""
        return self.read().seasons[self.active_season].series.count

    @property
    def are_registrations_open(self) -> bool:
        """true when the active season is in REGISTRATION stage."""
        data = self.read()
        if not int(data.active_season):
            return False
        season = data.seasons.get(data.active_season)
        return season is not None and season.stage == TournamentStage.REGISTRATION

    @override
    def read(self, external_path=None) -> SeasonsSchema:
        """reads from the file and returns schema."""
        data = super().read(external_path=external_path)
        return SeasonsSchema.from_dict(data)

    @override
    def commit(self, data: SeasonsSchema, external_path=None) -> None:
        """commits the schema to the file as dict."""
        super().commit(data=data.to_dict(), external_path=external_path)

    def create_season(self, schema: SeasonSchema) -> None:
        """creates the season with the given schema. stage defaults to NONE."""
        data = self.read()
        season_id = str(max([int(id) for id in data.seasons], default=0) + 1)
        data.seasons[season_id] = schema
        data.active_season = season_id
        self.commit(data)
        season_dir = self.directory / season_id
        season_dir.mkdir(parents=True, exist_ok=True)

    def get_season(self, season_id: str) -> SeasonSchema | None:
        """returns a seasonschema by its id."""
        return self.read().seasons.get(season_id)

    def update_season(self, season_id: str, season: SeasonSchema) -> None:
        """updates a season using the given seasonschema."""
        data = self.read()
        data.seasons[season_id] = season
        self.commit(data)

    def cancel_season(self, season_id: str) -> bool:
        """cancels a season: stops being active, stage becomes CANCELLED."""
        data = self.read()
        season = data.seasons.get(season_id)
        if season is None:
            return False
        season.stage = TournamentStage.CANCELLED
        if data.active_season == season_id:
            data.active_season = "0"
        self.commit(data)
        return True

    def open_registrations(self) -> bool:
        """moves the active season from NONE to REGISTRATION. returns True on success."""
        data = self.read()
        if not int(data.active_season):
            return False
        season = data.seasons.get(data.active_season)
        if season is None or season.stage != TournamentStage.NONE:
            return False
        season.stage = TournamentStage.REGISTRATION
        self.commit(data)
        return True

    def close_registrations(self) -> bool:
        """moves the active season from REGISTRATION to NONE. returns True on success."""
        data = self.read()
        if not int(data.active_season):
            return False
        season = data.seasons.get(data.active_season)
        if season is None or season.stage != TournamentStage.REGISTRATION:
            return False
        season.stage = TournamentStage.NONE
        self.commit(data)
        return True

    # ---- the registration panel the bot keeps in sync ----

    def get_panel(self) -> tuple[int, int]:
        """(channel_id, message_id) of the tracked register panel (0, 0 when none)."""
        data = self.read()
        return data.panel_channel_id, data.panel_message_id

    def set_panel(self, channel_id: int, message_id: int) -> None:
        """remembers where the register panel lives."""
        data = self.read()
        data.panel_channel_id = int(channel_id)
        data.panel_message_id = int(message_id)
        self.commit(data)

    def clear_panel(self) -> None:
        """forgets the tracked register panel."""
        data = self.read()
        data.panel_channel_id = 0
        data.panel_message_id = 0
        self.commit(data)


seasons = Seasons()