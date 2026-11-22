"""storage for ready-panel and bench-panel message ids."""

from server.storage import Storage


class ReadyPanels(Storage):
    def __init__(self) -> None:
        super().__init__("ready_panels.json", "tournament")
        if not self.path.exists():
            self.commit({})

    def get(self, season_id: str, match_key: str) -> dict | None:
        db = self.read()
        return (db.get(str(season_id)) or {}).get(str(match_key))

    def set(
        self,
        season_id: str,
        match_key: str,
        message_id: int,
        channel_id: int,
        can_force: bool = False,
    ) -> None:
        db = self.read()
        db.setdefault(str(season_id), {})[str(match_key)] = {
            "message_id": int(message_id),
            "channel_id": int(channel_id),
            "can_force": bool(can_force),
        }
        self.commit(db)

    def update_can_force(self, season_id: str, match_key: str, can_force: bool) -> None:
        db = self.read()
        entry = (db.get(str(season_id)) or {}).get(str(match_key))
        if not entry:
            return
        entry["can_force"] = bool(can_force)
        self.commit(db)

    def remove(self, season_id: str, match_key: str) -> None:
        db = self.read()
        bucket = db.get(str(season_id))
        if bucket and str(match_key) in bucket:
            del bucket[str(match_key)]
            self.commit(db)

    def clear_season(self, season_id: str) -> None:
        db = self.read()
        if str(season_id) in db:
            del db[str(season_id)]
            self.commit(db)

    def items(self, season_id: str):
        bucket = self.read().get(str(season_id)) or {}
        for match_key, entry in bucket.items():
            yield match_key, entry


class BenchPanels(Storage):
    def __init__(self) -> None:
        super().__init__("bench_panels.json", "tournament")
        if not self.path.exists():
            self.commit({})

    @staticmethod
    def _key(match_key: str, team: str) -> str:
        return f"{match_key}|{team}"

    def get(self, season_id: str, match_key: str, team: str) -> dict | None:
        bucket = self.read().get(str(season_id)) or {}
        return bucket.get(self._key(match_key, team))

    def set(
        self,
        season_id: str,
        match_key: str,
        team: str,
        message_id: int,
        channel_id: int,
        out_discord_id: str,
    ) -> None:
        db = self.read()
        db.setdefault(str(season_id), {})[self._key(match_key, team)] = {
            "message_id": int(message_id),
            "channel_id": int(channel_id),
            "out_discord_id": str(out_discord_id),
        }
        self.commit(db)

    def remove(self, season_id: str, match_key: str, team: str) -> None:
        db = self.read()
        bucket = db.get(str(season_id))
        if not bucket:
            return
        if self._key(match_key, team) in bucket:
            del bucket[self._key(match_key, team)]
            self.commit(db)

    def clear_season(self, season_id: str) -> None:
        db = self.read()
        if str(season_id) in db:
            del db[str(season_id)]
            self.commit(db)

    def items(self, season_id: str):
        bucket = self.read().get(str(season_id)) or {}
        for key, entry in bucket.items():
            match_key, team = key.split("|", 1)
            yield match_key, team, entry


ready_panels = ReadyPanels()
bench_panels = BenchPanels()