"""api envelopes and request bodies. domain types live in tournament/schema.py."""

from dataclasses import dataclass, field

from tournament.schema import MatchSchema, RoundSchema, SeasonSchema


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
class SeasonsListResponse:
    active_season: str = ""
    seasons: dict[str, SeasonSchema] = field(default_factory=dict)


@dataclass
class SeasonInfoResponse(SeasonSchema):
    season_id: str = ""
    is_active: bool = False
    registrations_open: bool = False


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