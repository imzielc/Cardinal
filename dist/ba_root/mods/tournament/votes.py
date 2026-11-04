"""storage for permanent-swap community votes."""

import time

from server.storage import Storage
from tournament.storage import SEASONS_DIR
from tournament.schema import SwapVoteSchema, VotesSchema


class Votes(Storage):
    def __init__(self, season_id: str) -> None:
        self.season_id = season_id
        super().__init__("votes.json", SEASONS_DIR / season_id)
        if not self.path.exists():
            self.commit(VotesSchema())

    def read(self) -> VotesSchema:
        return VotesSchema.from_dict(super().read())

    def commit(self, data: VotesSchema) -> None:
        super().commit(data.to_dict())

    def open(
        self,
        team_id: str,
        out_discord_id: str,
        in_discord_id: str,
        in_account_id: str,
        requested_by: str,
        reason: str,
        duration_seconds: float,
    ) -> SwapVoteSchema:
        db = self.read()
        vote_id = f"vote-{db.next_id}"
        db.next_id += 1
        now = time.time()
        vote = SwapVoteSchema(
            id=vote_id,
            team_id=team_id,
            out_discord_id=out_discord_id,
            in_discord_id=in_discord_id,
            in_account_id=in_account_id,
            requested_by=requested_by,
            reason=reason,
            created_at=now,
            closes_at=now + duration_seconds,
        )
        db.votes[vote_id] = vote
        self.commit(db)
        return vote

    def get(self, vote_id: str) -> SwapVoteSchema | None:
        return self.read().votes.get(vote_id)

    def cast(self, vote_id: str, voter_discord_id: str, choice: str) -> SwapVoteSchema | None:
        if choice not in ("yes", "no"):
            return None
        db = self.read()
        vote = db.votes.get(vote_id)
        if not vote or vote.state != "OPEN":
            return None
        vote.votes[str(voter_discord_id)] = choice
        self.commit(db)
        return vote

    def close(self, vote_id: str) -> SwapVoteSchema | None:
        db = self.read()
        vote = db.votes.get(vote_id)
        if not vote or vote.state != "OPEN":
            return None
        yes = sum(1 for v in vote.votes.values() if v == "yes")
        no = sum(1 for v in vote.votes.values() if v == "no")
        total = yes + no
        if total < 3 or yes <= no:
            vote.state = "FAILED"
        else:
            vote.state = "PASSED"
        self.commit(db)
        return vote

    def set_message(self, vote_id: str, message_id: int, channel_id: int) -> None:
        db = self.read()
        vote = db.votes.get(vote_id)
        if not vote:
            return
        vote.message_id = int(message_id)
        vote.channel_id = int(channel_id)
        self.commit(db)

    def open_votes(self) -> list[SwapVoteSchema]:
        return [v for v in self.read().votes.values() if v.state == "OPEN"]