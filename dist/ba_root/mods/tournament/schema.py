"""all the dataclass schema for tournament."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

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
        # pydantic is imported here, not at module load
        from pydantic import TypeAdapter
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
    # the one registration panel the bot tracks (0 means none).
    panel_channel_id: int = 0
    panel_message_id: int = 0


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
    account_id: str = ""
    device_uuid: str = ""
    accepted: bool = False
    # the unix time the invitation was sent (or last refreshed).
    invited_at: float = 0.0

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
    # the private thread the invitation lives in (0 when there is none).
    thread_id: int = 0
    # the tracked invitation message inside that thread (0 when not set).
    invitation_message_id: int = 0
    # the unix time the invitation expires (0 means no expiration).
    expires_at: float = 0.0
    # discord_id -> unix time they declined. used to cool down re-invites.
    declined: dict[str, float] = field(default_factory=dict)
    # the discord role that mentions this team (0 when not set, e.g. solo teams).
    role_id: int = 0


@dataclass
class RegistrationSchema(BaseSchema):
    """schema for the registration."""
    teams: dict[str, TeamSchema] = field(default_factory=dict)
    # keys can be a discord id or an account id, values are team ids.
    players: dict[str, str] = field(default_factory=dict)


# ---- account linking ----

@dataclass
class LinkCodeSchema(BaseSchema):
    """a short-lived code waiting to be redeemed in-game."""
    discord_id: str
    expires_at: float = 0.0


@dataclass
class LinkedSchema(BaseSchema):
    """discord <-> game account links, independent of any season.

    codes: pending link codes, keyed by the code itself.
    links: permanent bindings, discord id -> game account id.
    """
    codes: dict[str, LinkCodeSchema] = field(default_factory=dict)
    links: dict[str, str] = field(default_factory=dict)


# ---- match threads (bot side) ----

@dataclass
class MatchThreadsSchema(BaseSchema):
    """the match threads the bot created for the current round of a season.

    key is the match's stable id (same format as Brackets.list_matches keys),
    value is the thread id.
    """
    season_id: str = ""
    threads: dict[str, int] = field(default_factory=dict)