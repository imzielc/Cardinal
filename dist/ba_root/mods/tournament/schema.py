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
        from pydantic import TypeAdapter
        return TypeAdapter(cls).validate_python(data)

    def to_dict(self) -> dict:
        """serializes the schema back into a dict."""
        return asdict(self)


@dataclass
class SeasonSchema(BaseSchema):
    """schema for a unique season."""

    series: SeriesType = SeriesType.BO3
    type: TournamentType = TournamentType.SOLO
    mode: TournamentMode = TournamentMode.SINGLE
    stage: TournamentStage = TournamentStage.NONE
    created_at: str = ""
    completed_at: str = ""
    participant_role_id: int = 0


@dataclass
class SeasonsSchema(BaseSchema):
    """schema for all the seasons."""

    active_season: str = "0"
    seasons: dict[str, SeasonSchema] = field(default_factory=dict)
    panel_channel_id: int = 0
    panel_message_id: int = 0


@dataclass
class MatchTeamSchema(BaseSchema):
    """schema for a team."""
    idx: int
    name: str | None = None
    score: int = 0
    series: int = 0
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
    last_scores: list[int] = field(default_factory=lambda: [0, 0])
    bracket: str | None = None
    is_bye: bool = False
    label: str = ""


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
class PlacementSchema(BaseSchema):
    """final rank of a team in a completed bracket."""
    rank: int = 0
    team: str = ""


@dataclass
class BracketsMetaSchema(BaseSchema):
    """schema for the brackets meta."""
    mode: TournamentMode = TournamentMode.SINGLE
    started: bool = False
    active_rounds: list[str] = field(default_factory=list)
    total_rounds: int = 0
    status: Status = Status.PENDING
    placements: list[PlacementSchema] = field(default_factory=list)
    pending_round_start: list[dict] = field(default_factory=list)


@dataclass
class MemberSchema(BaseSchema):
    """schema for a member."""
    discord_id: str
    account_id: str = ""
    device_uuid: str = ""
    accepted: bool = False
    invited_at: float = 0.0

    def to_public(self) -> dict:
        data = self.to_dict()
        if data["device_uuid"]:
            data["device_uuid"] = "***"
        if data["account_id"]:
            data["account_id"] = "***"
        return data


@dataclass
class TeamSchema(BaseSchema):
    """schema for a team."""
    id: str
    captain: str
    status: TeamStatus = TeamStatus.IN_INVITATION
    members: list[MemberSchema] = field(default_factory=list)
    thread_id: int = 0
    invitation_message_id: int = 0
    expires_at: float = 0.0
    declined: dict[str, float] = field(default_factory=dict)
    role_id: int = 0
    reminders_sent: int = 0
    last_reminded_at: float = 0.0

    def to_public(self) -> dict:
        data = self.to_dict()
        data["members"] = [m.to_public() for m in self.members]
        return data


@dataclass
class RegistrationSchema(BaseSchema):
    """schema for the registration."""
    teams: dict[str, TeamSchema] = field(default_factory=dict)
    players: dict[str, str] = field(default_factory=dict)


@dataclass
class LinkCodeSchema(BaseSchema):
    """a short-lived code waiting to be redeemed in-game."""
    discord_id: str
    expires_at: float = 0.0


@dataclass
class LinkedSchema(BaseSchema):
    """discord <-> game account links, independent of any season."""
    codes: dict[str, LinkCodeSchema] = field(default_factory=dict)
    links: dict[str, str] = field(default_factory=dict)


@dataclass
class MatchThreadsSchema(BaseSchema):
    """the match threads the bot created for the current round of a season."""
    season_id: str = ""
    threads: dict[str, int] = field(default_factory=dict)


@dataclass
class WaitlistEntrySchema(BaseSchema):
    """one queued team waiting for a slot to open up."""
    captain_discord_id: str
    team_name: str
    invited_member_ids: list[str] = field(default_factory=list)
    added_at: float = 0.0


@dataclass
class WaitlistSchema(BaseSchema):
    """queued teams for a season, oldest first."""
    entries: list[WaitlistEntrySchema] = field(default_factory=list)


@dataclass
class PlayerStatsSchema(BaseSchema):
    """a player's stats in a season (mirrors the shape in stats.json)."""
    name: str = ""
    score: int = 0
    kills: int = 0
    deaths: int = 0
    games: int = 0
    rank: int = 0