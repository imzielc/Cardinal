"""secret-code login, discord oauth, session refresh, account linkage."""

import time
from dataclasses import asdict

import aiohttp
from aiohttp import web

from server import config
from roles import roles
from server.enums import Authority
from tournament.link import LINK_EXPIRE_SECONDS, link
from api import auth as auth_lib
from api.registry import route
from api.revoked import revoked
from api.schema import (
    DiscordUrlResponse,
    LinkRequestResponse,
    LoginRequest,
    LoginResponse,
    OkResponse,
    RefreshResponse,
    WhoAmIResponse,
)


def _oauth_configured() -> bool:
    oauth = config.api.get("discord_oauth") or {}
    return bool(oauth.get("client_id") and oauth.get("client_secret"))


def _oauth_redirect() -> str:
    oauth = config.api.get("discord_oauth") or {}
    return str(oauth.get("redirect_uri") or "") or (
        f"{config.api.public_url}/v1/auth/discord/callback"
    )


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


@route("POST", "/v1/auth/login", body=LoginRequest, response=LoginResponse)
async def login(request: web.Request) -> web.Response:
    """Logs in with the secret code from config."""
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

    token = auth_lib.issue_jwt(sub="secret", kind="secret", is_admin=True)
    response = web.json_response({"token": token, "kind": "secret"})
    _set_session_cookie(response, token)
    return response


@route("POST", "/v1/auth/logout", response=OkResponse, auth="required")
async def logout(request: web.Request) -> web.Response:
    """Revokes the caller's JWT."""
    auth = request.get("auth")
    if auth and auth.get("jti"):
        exp = int(time.time() + auth_lib.JWT_TTL_SECONDS)
        revoked.revoke(auth["jti"], exp)
    response = web.json_response({"ok": True})
    response.del_cookie("admin_session")
    return response


@route("GET", "/v1/auth/discord/url", response=DiscordUrlResponse)
async def discord_url(request: web.Request) -> web.Response:
    """Discord OAuth URL. 501 if OAuth not configured."""
    if not _oauth_configured():
        return web.json_response({"error": "oauth_not_configured"}, status=501)
    oauth = config.api.get("discord_oauth")
    url = (
        "https://discord.com/oauth2/authorize"
        f"?client_id={oauth['client_id']}"
        "&response_type=code"
        "&scope=identify"
        f"&redirect_uri={_oauth_redirect()}"
    )
    return web.json_response({"url": url})


@route(
    "GET",
    "/v1/auth/discord/callback",
    response=LoginResponse,
    query={"code": "string"},
)
async def discord_callback(request: web.Request) -> web.Response:
    """Discord OAuth callback. Issues a session for anyone."""
    if not _oauth_configured():
        return web.json_response({"error": "oauth_not_configured"}, status=501)

    code = request.query.get("code")
    if not code:
        return web.json_response({"error": "missing_code"}, status=400)

    oauth = config.api.get("discord_oauth")
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id": oauth["client_id"],
                "client_secret": oauth["client_secret"],
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _oauth_redirect(),
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

    display = user.get("global_name") or user.get("username") or ""
    is_admin = roles.get_authority_level(discord_id) >= Authority.ADMIN

    token = auth_lib.issue_jwt(
        sub=f"discord:{discord_id}",
        kind="discord",
        is_admin=is_admin,
        discord_id=discord_id,
        discord_name=display,
    )
    response = web.json_response({"token": token, "kind": "discord"})
    _set_session_cookie(response, token)
    return response


@route("GET", "/v1/auth/whoami", response=WhoAmIResponse)
async def whoami(request: web.Request) -> web.Response:
    """Identity of the caller. Called on every frontend page load."""
    auth = request.get("auth")
    if auth is None:
        return web.json_response(asdict(WhoAmIResponse()))

    discord_id = auth.get("discord_id")
    linked = bool(discord_id and link.is_linked(discord_id))
    account_id = link.account_of(discord_id) if linked else None

    return web.json_response(asdict(WhoAmIResponse(
        logged_in=True,
        kind=auth.get("kind", ""),
        is_admin=bool(auth.get("is_admin")),
        discord_id=discord_id,
        discord_name=auth.get("discord_name"),
        linked=linked,
        account_id=account_id,
        expires_at=int(auth.get("exp") or 0),
    )))


@route(
    "POST",
    "/v1/auth/link-request",
    response=LinkRequestResponse,
    auth="required",
)
async def link_request(request: web.Request) -> web.Response:
    """Issues a code for linking a game account to the caller's Discord id."""
    auth = request.get("auth")
    discord_id = auth.get("discord_id") if auth else None
    if not discord_id:
        return web.json_response(
            {
                "error": "no_discord_identity",
                "message": "Only Discord sessions can link an account.",
            },
            status=400,
        )

    if link.is_linked(discord_id):
        return web.json_response(
            {
                "error": "already_linked",
                "message": "This Discord account is already linked to a game account.",
            },
            status=400,
        )

    code = link.issue_code(discord_id)
    return web.json_response({
        "code": code,
        "expires_at": time.time() + LINK_EXPIRE_SECONDS,
        "instruction": (
            f"Join the Cardinal game server and run /link {code} to finish."
        ),
    })


@route(
    "POST",
    "/v1/auth/refresh",
    response=RefreshResponse,
    auth="required",
)
async def refresh(request: web.Request) -> web.Response:
    """Re-checks the caller's admin status and re-issues their JWT."""
    auth = request.get("auth")
    if auth.get("kind") != "discord":
        return web.json_response(
            {
                "error": "not_refreshable",
                "message": "Refresh only applies to Discord sessions.",
            },
            status=400,
        )

    discord_id = auth.get("discord_id")
    if not discord_id:
        return web.json_response({"error": "missing_discord_id"}, status=400)

    wait = auth_lib.note_refresh(discord_id)
    if wait > 0:
        return web.json_response(
            {"error": "throttled", "retry_after": round(wait, 1)},
            status=429,
        )

    is_admin = roles.get_authority_level(discord_id) >= Authority.ADMIN

    token = auth_lib.issue_jwt(
        sub=f"discord:{discord_id}",
        kind="discord",
        is_admin=is_admin,
        discord_id=discord_id,
        discord_name=auth.get("discord_name"),
    )
    payload = auth_lib.verify_jwt(token) or {}
    expires_at = int(payload.get("exp") or 0)

    response = web.json_response({
        "token": token,
        "kind": "discord",
        "is_admin": is_admin,
        "expires_at": expires_at,
    })
    _set_session_cookie(response, token)
    return response