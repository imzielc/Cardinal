"""auth-gated routes: reads over the private surface, and the two mutations."""

from aiohttp import web

from tournament import tournament as tournament_storage
from tournament.brackets import Brackets
from tournament.link import link as link_storage
from tournament.registration import Registration
from .. import serializers as s
from ..audit import audit

def register(app: web.Application) -> None:
    app.router.add_get("/v1/admin/links", admin_links)
    app.router.add_post(
        "/v1/seasons/{season_id}/matches/{match_key}/win/{team_idx}", give_win
    )
    app.router.add_post(
        "/v1/seasons/{season_id}/players/{discord_id}/uuid", change_uuid
    )


def _require_auth(request: web.Request) -> None:
    if request.get("auth") is None:
        raise web.HTTPUnauthorized(reason="Authentication required.")


def _actor(request: web.Request) -> str:
    return str((request.get("auth") or {}).get("sub") or "?")


def _resolve(season_id: str) -> str:
    if season_id == "active":
        sid = tournament_storage.active_season
        if not int(sid):
            raise web.HTTPNotFound(reason="No active season.")
        return sid
    return season_id


async def admin_links(request: web.Request) -> web.Response:
    _require_auth(request)
    db = link_storage.read()
    return web.json_response({
        "links": dict(db.links),
        "pending_codes": len(db.codes),
    })


async def give_win(request: web.Request) -> web.Response:
    _require_auth(request)
    sid = _resolve(request.match_info["season_id"])
    try:
        match_key = request.match_info["match_key"]
        team_idx = int(request.match_info["team_idx"])
    except (KeyError, ValueError):
        return web.json_response({"error": "bad_request"}, status=400)

    ok, message, match = Brackets(sid).give_win_by_key(match_key, team_idx)
    if not ok:
        return web.json_response({"error": "failed", "message": message}, status=400)

    audit.log(
        _actor(request),
        "give_win",
        {"season": sid, "match": match_key, "team": team_idx, "result": message},
    )
    return web.json_response({
        "ok": True,
        "message": message,
        "match": s.match(match) if match else None,
    })


async def change_uuid(request: web.Request) -> web.Response:
    _require_auth(request)
    sid = _resolve(request.match_info["season_id"])
    discord_id = str(request.match_info["discord_id"])

    try:
        body = await request.json()
    except Exception:
        body = {}
    new_uuid = str(body.get("uuid") or "").strip()
    if not new_uuid:
        return web.json_response({"error": "missing_uuid"}, status=400)

    ok = Registration(sid).change_uuid(discord_id=discord_id, new_uuid=new_uuid)
    if not ok:
        return web.json_response({"error": "not_found"}, status=404)

    audit.log(
        _actor(request),
        "change_uuid",
        {"season": sid, "discord_id": discord_id},
    )
    return web.json_response({"ok": True})