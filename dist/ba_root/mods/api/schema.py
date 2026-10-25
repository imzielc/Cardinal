"""api envelopes and request bodies. domain types live in tournament/schema.py."""

from dataclasses import dataclass, field

from server.enums import SeriesType, TournamentMode, TournamentType
from tournament.schema import (
    MatchSchema,
    PlacementSchema,
    RoundSchema,
    SeasonSchema,
)


@dataclass
class HealthResponse:
    status: str = ""
    public_url: str = ""


@dataclass
class SpecResponse:
    version: str = ""
    generated_at: int = 0
    endpoints: list[dict] = field(default_factory=list)
    schemas: dict[str, dict] = field(default_factory=dict)


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
class SeasonListItemSchema(SeasonSchema):
    """a season in the list response, with aggregations."""
    team_count: int = 0
    champion: str | None = None


@dataclass
class SeasonsListResponse:
    active_season: str = ""
    seasons: dict[str, SeasonListItemSchema] = field(default_factory=dict)


@dataclass
class SeasonInfoResponse(SeasonSchema):
    season_id: str = ""
    is_active: bool = False
    registrations_open: bool = False
    team_count: int = 0
    champion: str | None = None
    placements: list[PlacementSchema] | None = None


@dataclass
class HallOfFameEntry:
    season_id: str = ""
    type: TournamentType = TournamentType.SOLO
    mode: TournamentMode = TournamentMode.SINGLE
    series: SeriesType = SeriesType.BO3
    created_at: str = ""
    completed_at: str = ""
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
    match: MatchSchema | None = None


@dataclass
class UuidUpdateRequest:
    uuid: str = ""