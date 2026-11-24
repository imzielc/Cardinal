"""public reads. team members are redacted when the request is anonymous."""

import json
from pathlib import Path

from aiohttp import web

from server.enums import TournamentStage
from tournament import seasons as seasons_storage
from tournament.brackets import Brackets
from tournament.registration import Registration
from tournament.schema import (
    GroupStageSchema,
    MatchSchema,
    PlacementSchema,
    PlayerStatsSchema,
    SeasonSchema,
    TeamSchema,
    WaitlistEntrySchema,
)
from tournament.storage import SEASONS_DIR
from tournament.waitlist import Waitlist
from api.registry import route
from api.schema import (
    HallOfFameEntry,
    HallOfFameResponse,
    RoundsResponse,
    SeasonDetailResponse,
)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _resolve(season_id: str) -> str:
    if season_id == "active":
        sid = seasons_storage.active_season
        if not int(sid):
            raise web.HTTPNotFound(reason="No active season.")
        return sid
    return season_id


@route(
    "GET",
    "/v1/seasons",
    response=dict[str, SeasonSchema],
    query={"status": "string", "mode": "string"},
)
async def list_seasons(request: web.Request) -> web.Response:
    """All seasons, plus which one is active. Filters: ?status=, ?mode=."""
    status_filter = request.query.get("status", "").upper()
    mode_filter = request.query.get("mode", "").upper()

    db = seasons_storage.read()
    out = {}
    for sid, season in db.seasons.items():
        if status_filter and season.stage.value != status_filter:
            continue
        if mode_filter and season.mode.value != mode_filter:
            continue
        out[sid] = season.to_dict()

    return web.json_response({
        "active_season": db.active_season,
        "seasons": out,
    })


@route("GET", "/v1/seasons/{season_id}", response=SeasonDetailResponse)
async def season_info(request: web.Request) -> web.Response:
    """One season's info. Use 'active' for the running one."""
    sid = _resolve(request.match_info["season_id"])
    season = seasons_storage.get_season(sid)
    if season is None:
        raise web.HTTPNotFound(reason="No such season.")

    placements = None
    if season.stage == TournamentStage.COMPLETED:
        try:
            meta = Brackets(sid).read_meta()
            placements = [p.to_dict() for p in meta.placements]
        except Exception:
            pass

    regs_open = (
        season.stage == TournamentStage.REGISTRATION
        if sid == seasons_storage.active_season
        else False
    )

    resp = SeasonDetailResponse(
        season_id=sid,
        series=season.series,
        type=season.type,
        mode=season.mode,
        stage=season.stage,
        created_at=season.created_at,
        completed_at=season.completed_at,
        participant_role_id=season.participant_role_id,
        champion=season.champion,
        team_count=season.team_count,
        is_active=sid == seasons_storage.active_season,
        registrations_open=regs_open,
        placements=placements,
    )
    return web.json_response(resp.to_dict())


@route(
    "GET",
    "/v1/hall-of-fame",
    response=HallOfFameResponse,
    query={"type": "string", "mode": "string"},
)
async def hall_of_fame(request: web.Request) -> web.Response:
    """Completed seasons with their final placements, newest first."""
    type_filter = request.query.get("type", "").upper()
    mode_filter = request.query.get("mode", "").upper()

    db = seasons_storage.read()
    entries = []
    for sid in sorted(db.seasons.keys(), key=int, reverse=True):
        season = db.seasons[sid]
        if season.stage != TournamentStage.COMPLETED:
            continue
        if type_filter and season.type.value != type_filter:
            continue
        if mode_filter and season.mode.value != mode_filter:
            continue

        placements = []
        try:
            meta = Brackets(sid).read_meta()
            placements = meta.placements
        except Exception:
            pass

        entry = HallOfFameEntry(
            season_id=sid,
            series=season.series,
            type=season.type,
            mode=season.mode,
            created_at=season.created_at,
            completed_at=season.completed_at,
            champion=season.champion,
            team_count=season.team_count,
            placements=placements,
        )
        entries.append(entry)

    resp = HallOfFameResponse(entries=entries)
    return web.json_response(resp.to_dict())


@route(
    "GET",
    "/v1/seasons/{season_id}/teams",
    response=list[TeamSchema],
    redacted=True,
)
async def season_teams(request: web.Request) -> web.Response:
    """Teams of a season. Redacted without auth, full data with auth."""
    sid = _resolve(request.match_info["season_id"])
    reg = Registration(sid).read()
    auth = request.get("auth")
    return web.json_response([
        t.to_dict() if auth is not None else t.to_public()
        for t in reg.teams.values()
    ])


@route("GET", "/v1/seasons/{season_id}/rounds", response=RoundsResponse)
async def season_rounds(request: web.Request) -> web.Response:
    """Round structure of the season's bracket."""
    sid = _resolve(request.match_info["season_id"])
    brackets = Brackets(sid)
    meta = brackets.read_meta()
    
    rounds = {}
    if "group-stage" in meta.active_rounds:
        try:
            gs = brackets.read_gs()
            rounds = {
                gid: {rid: rd.to_dict() for rid, rd in g.rounds.items()}
                for gid, g in gs.groups.items()
            }
        except Exception:
            pass
    else:
        try:
            stage = brackets.read_stage()
            rounds = {
                bracket: {rid: rd.to_dict() for rid, rd in rounds_dict.items()}
                for bracket, rounds_dict in stage.items()
            }
        except Exception:
            pass
    
    resp = RoundsResponse(
        season_id=sid,
        mode=meta.mode.value,
        started=meta.started,
        status=meta.status.value,
        active_rounds=list(meta.active_rounds),
        pending_round_start=list(meta.pending_round_start),
        rounds=rounds,
    )
    return web.json_response(resp.to_dict())


@route(
    "GET",
    "/v1/seasons/{season_id}/matches",
    response=dict[str, MatchSchema],
)
async def season_matches(request: web.Request) -> web.Response:
    """Matches playable right now, keyed by their match key."""
    sid = _resolve(request.match_info["season_id"])
    try:
        matches = Brackets(sid).list_matches()
    except Exception:
        matches = {}
    return web.json_response({k: m.to_dict() for k, m in matches.items()})


@route(
    "GET",
    "/v1/seasons/{season_id}/group-stage",
    response=GroupStageSchema,
)
async def season_group_stage(request: web.Request) -> web.Response:
    """Group stage with standings. 404 if the season has no group stage."""
    sid = _resolve(request.match_info["season_id"])
    try:
        gs = Brackets(sid).read_gs()
    except Exception:
        raise web.HTTPNotFound(reason="No group stage for this season.")
    return web.json_response(gs.to_dict())


@route(
    "GET",
    "/v1/seasons/{season_id}/placements",
    response=list[PlacementSchema],
)
async def season_placements(request: web.Request) -> web.Response:
    """Final rankings. 404 until the tournament completes."""
    sid = _resolve(request.match_info["season_id"])
    meta = Brackets(sid).read_meta()
    if not meta.placements:
        raise web.HTTPNotFound(reason="Tournament is not completed.")
    return web.json_response([p.to_dict() for p in meta.placements])


@route(
    "GET",
    "/v1/seasons/{season_id}/stats",
    response=dict[str, PlayerStatsSchema],
)
async def season_stats(request: web.Request) -> web.Response:
    """Per-season player stats, keyed by account id."""
    sid = _resolve(request.match_info["season_id"])
    return web.json_response(_read_json(SEASONS_DIR / sid / "stats.json"))


@route(
    "GET",
    "/v1/seasons/{season_id}/waitlist",
    response=list[WaitlistEntrySchema],
)
async def season_waitlist(request: web.Request) -> web.Response:
    """Teams queued for the season."""
    sid = _resolve(request.match_info["season_id"])
    try:
        entries = Waitlist(sid).read().entries
    except Exception:
        entries = []
    return web.json_response([e.to_dict() for e in entries])
