"""public reads. team members are redacted when the request is anonymous."""

import json
from pathlib import Path

from aiohttp import web

from tournament import tournament as tournament_storage
from tournament.brackets import Brackets
from tournament.registration import Registration
from tournament.schema import (
    GroupStageSchema,
    MatchSchema,
    PlacementSchema,
    PlayerStatsSchema,
    TeamSchema,
    WaitlistEntrySchema,
)
from tournament.storage import SEASONS_DIR
from tournament.waitlist import Waitlist
from api.registry import route
from api.schema import RoundsResponse, SeasonsListResponse, SeasonInfoResponse


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _resolve(season_id: str) -> str:
    if season_id == "active":
        sid = tournament_storage.active_season
        if not int(sid):
            raise web.HTTPNotFound(reason="No active season.")
        return sid
    return season_id


@route("GET", "/v1/seasons", response=SeasonsListResponse)
async def list_seasons(request: web.Request) -> web.Response:
    """All seasons, plus which one is active."""
    db = tournament_storage.read()
    return web.json_response({
        "active_season": db.active_season,
        "seasons": {sid: sch.to_dict() for sid, sch in db.seasons.items()},
    })


@route("GET", "/v1/seasons/{season_id}", response=SeasonInfoResponse)
async def season_info(request: web.Request) -> web.Response:
    """One season's info. Use 'active' for the running one."""
    sid = _resolve(request.match_info["season_id"])
    season = tournament_storage.get_season(sid)
    if season is None:
        raise web.HTTPNotFound(reason="No such season.")
    
    return web.json_response({
        **season.to_dict(),
        "season_id": sid,
        "is_active": sid == tournament_storage.active_season,
        "registrations_open": (
            tournament_storage.are_registrations_open
            if sid == tournament_storage.active_season
            else False
        ),
    })


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
    out = {
        "season_id": sid,
        "mode": meta.mode.value,
        "started": meta.started,
        "status": meta.status.value,
        "active_rounds": list(meta.active_rounds),
        "pending_round_start": list(meta.pending_round_start),
        "rounds": {},
    }
    if "group-stage" in meta.active_rounds:
        try:
            gs = brackets.read_gs()
            out["rounds"] = {
                gid: {rid: rd.to_dict() for rid, rd in g.rounds.items()}
                for gid, g in gs.groups.items()
            }
        except Exception:
            pass
    else:
        try:
            stage = brackets.read_stage()
            out["rounds"] = {
                bracket: {rid: rd.to_dict() for rid, rd in rounds.items()}
                for bracket, rounds in stage.items()
            }
        except Exception:
            pass
    return web.json_response(out)


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