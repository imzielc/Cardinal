"""secret-code login, discord oauth, logout."""

import time

import aiohttp
from aiohttp import web

from server import config
from roles import roles
from server.enums import Authority
from .. import auth as auth_lib
from ..revoked import revoked


def register(app: web.Application) -> None:
    app.router.add_post("/v1/auth/login", login)
    app.router.add_post("/v1/auth/logout", logout)
    app.router.add_get("/v1/auth/discord/url", discord_url)
    app.router.add_get("/v1/auth/discord/callback", discord_callback)


def _oauth_configured() -> bool:
    oauth = config.api.get("discord_oauth") or {}
    return bool(oauth.get("client_id") and oauth.get("client_secret"))


def _set_session_cookie(response: web.Response, token: str) -> None:
    kwargs = {
        "httponly": True,
        "secure": True,
        "samesite": "Lax",
        "max_age": auth_lib.JWT_TTL_SECONDS,
        "path": "/",
    }
    domain = str(config.api.get("cookie_domain") or "")
    if domain:
        kwargs["domain"] = domain
    response.set_cookie("admin_session", token, **kwargs)


async def login(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)

    code = str(body.get("secret_code") or "")
    if not code:
        return web.json_response({"error": "missing_secret_code"}, status=400)

    stored = str(config.api.get("secret_code") or "")
    if not auth_lib.verify_secret_code(code, stored):
        return web.json_response({"error": "invalid_secret_code"}, status=401)

    token = auth_lib.issue_jwt(sub="secret", kind="secret")
    response = web.json_response({"token": token, "kind": "secret"})
    _set_session_cookie(response, token)
    return response


async def logout(request: web.Request) -> web.Response:
    auth = request.get("auth")
    if auth and auth.get("jti"):
        exp = int(time.time() + auth_lib.JWT_TTL_SECONDS)
        revoked.revoke(auth["jti"], exp)
    response = web.json_response({"ok": True})
    response.del_cookie("admin_session")
    return response


async def discord_url(request: web.Request) -> web.Response:
    if not _oauth_configured():
        return web.json_response({"error": "oauth_not_configured"}, status=501)
    oauth = config.api.get("discord_oauth")
    redirect = str(oauth.get("redirect_uri") or "") or (
        f"{config.api.public_url}/v1/auth/discord/callback"
    )
    url = (
        "https://discord.com/oauth2/authorize"
        f"?client_id={oauth['client_id']}"
        "&response_type=code"
        "&scope=identify"
        f"&redirect_uri={redirect}"
    )
    return web.json_response({"url": url})


async def discord_callback(request: web.Request) -> web.Response:
    if not _oauth_configured():
        return web.json_response({"error": "oauth_not_configured"}, status=501)

    code = request.query.get("code")
    if not code:
        return web.json_response({"error": "missing_code"}, status=400)

    oauth = config.api.get("discord_oauth")
    redirect = str(oauth.get("redirect_uri") or "") or (
        f"{config.api.public_url}/v1/auth/discord/callback"
    )

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id": oauth["client_id"],
                "client_secret": oauth["client_secret"],
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect,
            },
        ) as resp:
            if resp.status != 200:
                return web.json_response({"error": "oauth_token_failed"}, status=401)
            tokens = await resp.json()
        access = tokens.get("access_token")
        if not access:
            return web.json_response({"error": "oauth_no_token"}, status=401)

        async with session.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {access}"},
        ) as resp:
            if resp.status != 200:
                return web.json_response({"error": "oauth_user_failed"}, status=401)
            user = await resp.json()

    discord_id = str(user.get("id") or "")
    if not discord_id:
        return web.json_response({"error": "oauth_no_user"}, status=401)
    if roles.get_authority_level(discord_id) < Authority.LEADER:
        return web.json_response({"error": "not_leader"}, status=403)

    token = auth_lib.issue_jwt(sub=f"discord:{discord_id}", kind="discord")
    response = web.json_response({"token": token, "kind": "discord"})
    _set_session_cookie(response, token)
    return response