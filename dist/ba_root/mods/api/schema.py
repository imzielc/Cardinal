"""api envelopes and request bodies. domain types live in tournament/schema.py."""

from dataclasses import dataclass, field

from server.enums import SeriesType, TournamentMode, TournamentStage, TournamentType
from tournament.schema import PlacementSchema, RoundSchema


@dataclass
class HealthResponse:
    status: str = ""
    public_url: str = ""


@dataclass
class SpecResponse:
    version: str = ""
    generated_at: int = 0
    endpoints: list[dict] = field(default_factory=list)


@dataclass
class LoginRequest:
    secret_code: str = ""


@dataclass
class LoginResponse:
    token: str = ""
    kind: str = ""


@dataclass
class OkResponse:
    ok: bool = False


@dataclass
class DiscordUrlResponse:
    url: str = ""


@dataclass
class WhoAmIResponse:
    logged_in: bool = False
    kind: str = ""
    is_admin: bool = False
    discord_id: str | None = None
    discord_name: str | None = None
    linked: bool = False
    account_id: str | None = None
    expires_at: int = 0


@dataclass
class LinkRequestResponse:
    code: str = ""
    expires_at: float = 0.0
    instruction: str = ""


@dataclass
class RefreshResponse:
    token: str = ""
    kind: str = ""
    is_admin: bool = False
    expires_at: int = 0


@dataclass
class SeasonDetailResponse:
    """response for GET /v1/seasons/{season_id}"""
    season_id: str = ""
    series: SeriesType = SeriesType.BO3
    type: TournamentType = TournamentType.SOLO
    mode: TournamentMode = TournamentMode.SINGLE
    stage: TournamentStage = TournamentStage.NONE
    created_at: str = ""
    completed_at: str = ""
    participant_role_id: int = 0
    champion: str = ""
    team_count: int = 0
    is_active: bool = False
    registrations_open: bool = False
    placements: list[PlacementSchema] | None = None


@dataclass
class HallOfFameEntry:
    season_id: str = ""
    series: SeriesType = SeriesType.BO3
    type: TournamentType = TournamentType.SOLO
    mode: TournamentMode = TournamentMode.SINGLE
    created_at: str = ""
    completed_at: str = ""
    champion: str = ""
    team_count: int = 0
    placements: list[PlacementSchema] = field(default_factory=list)


@dataclass
class HallOfFameResponse:
    entries: list[HallOfFameEntry] = field(default_factory=list)


@dataclass
class RoundsResponse:
    season_id: str = ""
    mode: str = ""
    started: bool = False
    status: str = ""
    active_rounds: list[str] = field(default_factory=list)
    pending_round_start: list[dict] = field(default_factory=list)
    rounds: dict[str, dict[str, RoundSchema]] = field(default_factory=dict)


@dataclass
class LinksResponse:
    links: dict[str, str] = field(default_factory=dict)
    pending_codes: int = 0


@dataclass
class WinResponse:
    ok: bool = False
    message: str = ""
    match: dict | None = None


@dataclass
class UuidUpdateRequest:
    uuid: str = ""
