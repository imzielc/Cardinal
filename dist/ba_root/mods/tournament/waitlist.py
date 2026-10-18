"""waitlist.py — queued teams for a season, waiting for a slot to open up.

a slot opens when a registered team expires or is dissolved before the
tournament starts. the bot promotes the oldest queued entry into a real
team automatically (see Registration.promote_from_waitlist + the bot's
announce_waitlist_promotion).
"""

from __future__ import annotations

import time

from server.storage import Storage
from tournament.schema import WaitlistEntrySchema, WaitlistSchema
from tournament.storage import SEASONS_DIR


class Waitlist(Storage):
    """queued teams for a season, ordered by when they were added."""

    def __init__(self, season_id: str) -> None:
        self.season_id = season_id
        super().__init__("waitlist.json", SEASONS_DIR / season_id)
        self.bootstrap()

    def bootstrap(self) -> None:
        if not self.path.exists():
            self.commit(WaitlistSchema())

    def read(self) -> WaitlistSchema:
        return WaitlistSchema.from_dict(super().read())

    def commit(self, data: WaitlistSchema) -> None:
        super().commit(data.to_dict())

    def __len__(self) -> int:
        return len(self.read().entries)

    def is_waitlisted(self, captain_discord_id: str) -> bool:
        captain_discord_id = str(captain_discord_id)
        return any(
            e.captain_discord_id == captain_discord_id
            for e in self.read().entries
        )

    def position_of(self, captain_discord_id: str) -> int | None:
        captain_discord_id = str(captain_discord_id)
        for idx, entry in enumerate(self.read().entries, start=1):
            if entry.captain_discord_id == captain_discord_id:
                return idx
        return None

    def add(self, captain_discord_id: str, team_name: str, invited_member_ids: list) -> bool:
        """appends an entry. returns False if the captain is already queued."""
        captain_discord_id = str(captain_discord_id)
        db = self.read()
        if any(e.captain_discord_id == captain_discord_id for e in db.entries):
            return False
        db.entries.append(WaitlistEntrySchema(
            captain_discord_id=captain_discord_id,
            team_name=team_name,
            invited_member_ids=[str(m) for m in invited_member_ids],
            added_at=time.time(),
        ))
        self.commit(db)
        return True

    def remove(self, captain_discord_id: str) -> bool:
        captain_discord_id = str(captain_discord_id)
        db = self.read()
        before = len(db.entries)
        db.entries = [e for e in db.entries if e.captain_discord_id != captain_discord_id]
        if len(db.entries) == before:
            return False
        self.commit(db)
        return True

    def pop_next(self) -> WaitlistEntrySchema | None:
        """removes and returns the oldest queued entry, or None when empty."""
        db = self.read()
        if not db.entries:
            return None
        entry = db.entries.pop(0)
        self.commit(db)
        return entry