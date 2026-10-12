"""all the dataclass schema for tournament."""
from __future__ import annotations

from dataclasses import asdict, field
from pydantic.dataclasses import dataclass
from pydantic import TypeAdapter
from server.enums import (
    SeriesType,
    Status,
    TeamStatus,
    TournamentMode,
    TournamentStage,
    TournamentType,
)

class BaseSchema:
    """base schema for all the schemas."""
    @classmethod
    def from_dict(cls, data: dict):
        """validates the data into the schema."""
        if not data:
            return cls()
        return TypeAdapter(cls).validate_python(data)

    def to_dict(self) -> dict:
        """serializes the schmea back into a dict."""
        return asdict(self)

@dataclass
class SeasonSchema(BaseSchema):
    """schema for a unique season."""

    series: SeriesType = SeriesType.BO3
    type: TournamentType = TournamentType.SOLO
    mode: TournamentMode = TournamentMode.SINGLE
    stage: TournamentStage = TournamentStage.REGISTRATION
    created_at: str = ""
    participant_role_id: int = 0

@dataclass
class TournamentSchema(BaseSchema):
    """Schema for all the seasons."""

    active_season: str = "0"
    registrations_status: bool = False
    seasons: dict[str, SeasonSchema] = field(default_factory=dict)

@dataclass
class MatchTeamSchema(BaseSchema):
    """schema for a team."""
    idx: int
    name: str | None = None
    score: int = 0
    series: int = 0
    # double elimination: a slot is filled by the result of another match, "W:<bracket>:<round>:<match>"
    # for its winner and "L:..." for its loser. a bye slot never gets a team.
    source: str | None = None
    bye: bool = False

@dataclass
class MatchSchema(BaseSchema):
    """schema for a match."""
    match_id: str
    teams: list[MatchTeamSchema] = field(default_factory=list)
    status: Status = Status.PENDING
    winner_idx: int | None = None
    loser_idx: int | None = None
    group_id: str | None = None
    round_id: str | None = None
    last_scores: list[int] = field(default_factory=lambda : [0, 0])
    # double elimination: "winners", "losers" or "grand-final", and whether a bye decided the match.
    bracket: str | None = None
    is_bye: bool = False

@dataclass
class PendingMatchSchema(BaseSchema):
    """schema for a pending match."""
    key: str
    match: MatchSchema
    uuids: list[str]
    players: list[str]

@dataclass
class RoundSchema(BaseSchema):
    """schema for a round."""
    matches: dict[str, MatchSchema] = field(default_factory=dict)
    status: Status = Status.PENDING
    # the position of the round in its bracket (1 is the first), the round ids are not always numbers.
    number: int = 0

@dataclass
class GroupSchema(BaseSchema):
    """schema for a group."""
    rounds: dict[str, RoundSchema] = field(default_factory=dict)
    standings: list[str] = field(default_factory=list)
    standings_sorted: list[dict] = field(default_factory=list)
    role_id: int = 0

@dataclass
class GroupStageSchema(BaseSchema):
    """schema for the group stage."""
    groups: dict[str, GroupSchema] = field(default_factory=dict)
    winning_teams_per_group: int = 0
    status: Status = Status.PENDING

@dataclass
class BracketsMetaSchema(BaseSchema):
    """schema for the brackets meta."""
    mode: TournamentMode = TournamentMode.SINGLE
    started: bool = False
    # the rounds that can be played right now, as "group-stage" or "<bracket>/<round>"
    active_rounds: list[str] = field(default_factory=list)
    total_rounds: int = 0
    status: Status = Status.PENDING
    # {"rank": 1, "team": "name"}, filled in when the tournament is over.
    placements: list[dict] = field(default_factory=list)

@dataclass
class MemberSchema(BaseSchema):
    """schema for a member."""
    discord_id: str
    code: str = ""
    account_id: str = ""
    device_uuid: str = ""

    def to_public(self) -> dict:
        """ masks the uuid and returns the dict. """
        data = self.to_dict()
        uuid = data["device_uuid"]
        if uuid:
            data["device_uuid"] = "***"
        return data

@dataclass
class TeamSchema(BaseSchema):
    """schema for a team."""
    id: str
    captain: str
    status: TeamStatus = TeamStatus.IN_INVITATION
    members: list[MemberSchema] = field(default_factory=list)

@dataclass
class RegistrationSchema(BaseSchema):
    """schema for the registration."""
    teams: dict[str, TeamSchema] = field(default_factory=dict)
    players: dict[str, str] = field(default_factory=dict)
    codes: dict[str, str] = field(default_factory=dict)
