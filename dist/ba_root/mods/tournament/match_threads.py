"""match_threads.py — the bot's registry of live match threads for the current round."""

from __future__ import annotations

from server.storage import Storage
from tournament.schema import MatchThreadsSchema


class MatchThreads(Storage):
    """a single slot: the current season's round threads. cleared on each new round."""

    def __init__(self) -> None:
        super().__init__("match_threads.json", "tournament")
        self.bootstrap()

    def bootstrap(self) -> None:
        if not self.path.exists():
            self.commit(MatchThreadsSchema())

    def read(self) -> MatchThreadsSchema:
        try:
            return MatchThreadsSchema.from_dict(super().read())
        except Exception:
            # a file from an older shape, just start clean.
            return MatchThreadsSchema()

    def commit(self, data: MatchThreadsSchema) -> None:
        super().commit(data.to_dict())

    def get(self, season_id: str) -> dict[str, int]:
        """match key -> thread id for this season, or {} when the stored one is stale."""
        data = self.read()
        if data.season_id != str(season_id):
            return {}
        return dict(data.threads)

    def set(self, season_id: str, threads: dict[str, int]) -> None:
        self.commit(MatchThreadsSchema(
            season_id=str(season_id),
            threads={str(k): int(v) for k, v in threads.items()},
        ))

    def remove(self, season_id: str, key: str) -> int | None:
        """forgets one match's thread, returning its id, or None when unknown."""
        data = self.read()
        if data.season_id != str(season_id):
            return None
        tid = data.threads.pop(str(key), None)
        if tid is None:
            return None
        self.commit(data)
        return int(tid)

    def clear(self) -> None:
        self.commit(MatchThreadsSchema())


match_threads = MatchThreads()