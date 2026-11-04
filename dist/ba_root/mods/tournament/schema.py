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
    series: SeriesType = SeriesType.BO3
    type: TournamentType = TournamentType.SOLO
    mode: TournamentMode = TournamentMode.SINGLE
    stage: TournamentStage = TournamentStage.NONE
    created_at: str = ""
    completed_at: str = ""
    participant_role_id: int = 0


@dataclass
class SeasonsSchema(BaseSchema):
    active_season: str = "0"
    seasons: dict[str, SeasonSchema] = field(default_factory=dict)
    panel_channel_id: int = 0
    panel_message_id: int = 0


@dataclass
class MatchTeamSchema(BaseSchema):
    idx: int
    name: str | None = None
    score: int = 0
    series: int = 0
    source: str | None = None
    bye: bool = False


@dataclass
class BenchActivationSchema(BaseSchema):
    """a bench member stepping in for a main, for one match."""
    team: str
    out_discord_id: str
    out_account_id: str
    in_discord_id: str
    in_account_id: str
    in_uuid: str
    requested_by: str
    reason: str = ""
    state: str = "PENDING"  # PENDING | ACCEPTED | DECLINED | FORCE
    at: float = 0.0
    expires_at: float = 0.0


@dataclass
class MatchSchema(BaseSchema):
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
    bench_activations: dict[str, BenchActivationSchema] = field(default_factory=dict)


@dataclass
class PendingMatchSchema(BaseSchema):
    key: str
    match: MatchSchema
    uuids: list[str]
    players: list[str]
    teams: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class RoundSchema(BaseSchema):
    matches: dict[str, MatchSchema] = field(default_factory=dict)
    status: Status = Status.PENDING
    number: int = 0


@dataclass
class GroupSchema(BaseSchema):
    rounds: dict[str, RoundSchema] = field(default_factory=dict)
    standings: list[str] = field(default_factory=list)
    standings_sorted: list[dict] = field(default_factory=list)
    role_id: int = 0


@dataclass
class GroupStageSchema(BaseSchema):
    groups: dict[str, GroupSchema] = field(default_factory=dict)
    winning_teams_per_group: int = 0
    status: Status = Status.PENDING


@dataclass
class PlacementSchema(BaseSchema):
    rank: int = 0
    team: str = ""


@dataclass
class BracketsMetaSchema(BaseSchema):
    mode: TournamentMode = TournamentMode.SINGLE
    started: bool = False
    active_rounds: list[str] = field(default_factory=list)
    total_rounds: int = 0
    status: Status = Status.PENDING
    placements: list[PlacementSchema] = field(default_factory=list)
    pending_round_start: list[dict] = field(default_factory=list)


@dataclass
class MemberSchema(BaseSchema):
    discord_id: str
    account_id: str = ""
    device_uuid: str = ""
    accepted: bool = False
    invited_at: float = 0.0
    role: str = "main"  # "main" | "bench"

    def to_public(self) -> dict:
        data = self.to_dict()
        if data["device_uuid"]:
            data["device_uuid"] = "***"
        if data["account_id"]:
            data["account_id"] = "***"
        return data


@dataclass
class TeamSchema(BaseSchema):
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

    @property
    def mains(self) -> list[MemberSchema]:
        return [m for m in self.members if m.role == "main"]

    @property
    def bench(self) -> list[MemberSchema]:
        return [m for m in self.members if m.role == "bench"]

    def to_public(self) -> dict:
        data = self.to_dict()
        data["members"] = [m.to_public() for m in self.members]
        return data


@dataclass
class RegistrationSchema(BaseSchema):
    teams: dict[str, TeamSchema] = field(default_factory=dict)
    players: dict[str, str] = field(default_factory=dict)


@dataclass
class LinkCodeSchema(BaseSchema):
    discord_id: str
    expires_at: float = 0.0


@dataclass
class LinkedSchema(BaseSchema):
    codes: dict[str, LinkCodeSchema] = field(default_factory=dict)
    links: dict[str, str] = field(default_factory=dict)


@dataclass
class MatchThreadsSchema(BaseSchema):
    season_id: str = ""
    threads: dict[str, int] = field(default_factory=dict)


@dataclass
class WaitlistEntrySchema(BaseSchema):
    captain_discord_id: str
    team_name: str
    invited_member_ids: list[str] = field(default_factory=list)
    added_at: float = 0.0


@dataclass
class WaitlistSchema(BaseSchema):
    entries: list[WaitlistEntrySchema] = field(default_factory=list)


@dataclass
class PlayerStatsSchema(BaseSchema):
    name: str = ""
    score: int = 0
    kills: int = 0
    deaths: int = 0
    games: int = 0
    rank: int = 0


@dataclass
class SwapVoteSchema(BaseSchema):
    id: str
    team_id: str
    out_discord_id: str
    in_discord_id: str
    in_account_id: str
    requested_by: str
    reason: str = ""
    created_at: float = 0.0
    closes_at: float = 0.0
    votes: dict[str, str] = field(default_factory=dict)  # discord_id -> "yes" | "no"
    state: str = "OPEN"  # OPEN | PASSED | FAILED
    message_id: int = 0
    channel_id: int = 0


@dataclass
class VotesSchema(BaseSchema):
    votes: dict[str, SwapVoteSchema] = field(default_factory=dict)
    next_id: int = 1