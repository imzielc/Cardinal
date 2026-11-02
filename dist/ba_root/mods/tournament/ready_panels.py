"""storage for the ready-panel message ids, so they survive bot restarts."""

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
        """iterates (match_key, entry) pairs for a season."""
        bucket = self.read().get(str(season_id)) or {}
        for match_key, entry in bucket.items():
            yield match_key, entry


ready_panels = ReadyPanels()