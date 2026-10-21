"""public reads, all redacted."""

import json
from pathlib import Path

from aiohttp import web

from server.storage import DATA_DIR
from tournament import tournament as tournament_storage
from tournament.brackets import Brackets
from tournament.registration import Registration
from tournament.storage import SEASONS_DIR
from tournament.waitlist import Waitlist
from .. import serializers as s


def register(app: web.Application) -> None:
    app.router.add_get("/api/v1/seasons", list_seasons)
    app.router.add_get("/api/v1/seasons/{season_id}", season_info)
    app.router.add_get("/api/v1/seasons/{season_id}/teams", season_teams)
    app.router.add_get("/api/v1/seasons/{season_id}/rounds", season_rounds)
    app.router.add_get("/api/v1/seasons/{season_id}/matches", season_matches)
    app.router.add_get("/api/v1/seasons/{season_id}/group-stage", season_group_stage)
    app.router.add_get("/api/v1/seasons/{season_id}/placements", season_placements)
    app.router.add_get("/api/v1/seasons/{season_id}/stats", season_stats)
    app.router.add_get("/api/v1/seasons/{season_id}/waitlist", season_waitlist)
    app.router.add_get("/api/v1/stats", global_stats)


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


async def list_seasons(request: web.Request) -> web.Response:
    db = tournament_storage.read()
    return web.json_response({
        "active_season": db.active_season,
        "seasons": {sid: s.season(sch) for sid, sch in db.seasons.items()},
    })


async def season_info(request: web.Request) -> web.Response:
    sid = _resolve(request.match_info["season_id"])
    season = tournament_storage.get_season(sid)
    if season is None:
        raise web.HTTPNotFound(reason="No such season.")
    return web.json_response({
        "season_id": sid,
        "is_active": sid == tournament_storage.active_season,
        "registrations_open": tournament_storage.are_registrations_open,
        **s.season(season),
    })


async def season_teams(request: web.Request) -> web.Response:
    sid = _resolve(request.match_info["season_id"])
    reg = Registration(sid).read()
    auth = request.get("auth")
    return web.json_response({
        "season_id": sid,
        "teams": [s.team(t, auth) for t in reg.teams.values()],
    })


async def season_rounds(request: web.Request) -> web.Response:
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
                gid: {rid: s.round_(rd) for rid, rd in g.rounds.items()}
                for gid, g in gs.groups.items()
            }
        except Exception:
            pass
    else:
        try:
            stage = brackets.read_stage()
            out["rounds"] = {
                bracket: {rid: s.round_(rd) for rid, rd in rounds.items()}
                for bracket, rounds in stage.items()
            }
        except Exception:
            pass
    return web.json_response(out)


async def season_matches(request: web.Request) -> web.Response:
    sid = _resolve(request.match_info["season_id"])
    try:
        matches = Brackets(sid).list_matches()
    except Exception:
        matches = {}
    return web.json_response({
        "season_id": sid,
        "matches": [{"key": k, **s.match(m)} for k, m in matches.items()],
    })


async def season_group_stage(request: web.Request) -> web.Response:
    sid = _resolve(request.match_info["season_id"])
    try:
        gs = Brackets(sid).read_gs()
    except Exception:
        raise web.HTTPNotFound(reason="No group stage for this season.")
    return web.json_response({
        "season_id": sid,
        "winning_teams_per_group": gs.winning_teams_per_group,
        "status": gs.status.value,
        "groups": {gid: s.group(g) for gid, g in gs.groups.items()},
    })


async def season_placements(request: web.Request) -> web.Response:
    sid = _resolve(request.match_info["season_id"])
    meta = Brackets(sid).read_meta()
    if not meta.placements:
        raise web.HTTPNotFound(reason="Tournament is not completed.")
    return web.json_response({
        "season_id": sid,
        "placements": [s.placement(p) for p in meta.placements],
    })


async def season_stats(request: web.Request) -> web.Response:
    sid = _resolve(request.match_info["season_id"])
    data = _read_json(SEASONS_DIR / sid / "stats.json")
    return web.json_response({"season_id": sid, "stats": data})


async def season_waitlist(request: web.Request) -> web.Response:
    sid = _resolve(request.match_info["season_id"])
    try:
        entries = Waitlist(sid).read().entries
    except Exception:
        entries = []
    return web.json_response({
        "season_id": sid,
        "waitlist": [s.waitlist_entry(e) for e in entries],
    })


async def global_stats(request: web.Request) -> web.Response:
    return web.json_response({"stats": _read_json(DATA_DIR / "stats" / "stats.json")})