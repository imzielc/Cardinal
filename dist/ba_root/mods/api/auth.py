"""jwt issuing and verifying, secret code comparison, admin gate."""

import secrets
import time

from server import config

JWT_ALG = "HS256"
JWT_TTL_SECONDS = 30 * 24 * 3600
REFRESH_THROTTLE_SECONDS = 60

_JWT = None
_refresh_last: dict[str, float] = {}


def _load_jwt():
    """returns the pyjwt module, installing it via uv on first call if needed."""
    global _JWT
    if _JWT is not None:
        return _JWT
    try:
        import jwt
    except ImportError:
        import subprocess
        from server.storage import MODS_DIR

        subprocess.run(
            ["uv", "add", "pyjwt"],
            check=True,
            cwd=str(MODS_DIR),
            stdout=subprocess.DEVNULL,
        )
        import jwt
    _JWT = jwt
    return _JWT


def verify_secret_code(input_code: str, stored: str) -> bool:
    """constant-time compare of the submitted code against the config value."""
    if not stored or not input_code:
        return False
    return secrets.compare_digest(input_code, stored)


def issue_jwt(
    sub: str,
    kind: str,
    *,
    is_admin: bool = False,
    discord_id: str | None = None,
    discord_name: str | None = None,
) -> str:
    jwt = _load_jwt()
    now = int(time.time())
    return jwt.encode(
        {
            "sub": sub,
            "kind": kind,
            "is_admin": bool(is_admin),
            "discord_id": discord_id,
            "discord_name": discord_name,
            "iat": now,
            "exp": now + JWT_TTL_SECONDS,
            "jti": secrets.token_hex(16),
        },
        config.api.jwt_secret,
        algorithm=JWT_ALG,
    )


def verify_jwt(token: str) -> dict | None:
    try:
        return _load_jwt().decode(token, config.api.jwt_secret, algorithms=[JWT_ALG])
    except Exception:
        return None


def is_master_token(token: str) -> bool:
    master = str(config.api.get("master_token") or "")
    if not master:
        return False
    return secrets.compare_digest(token, master)


def require_admin(request):
    """raises 401 if not authenticated, 403 if not admin. returns the auth dict."""
    from aiohttp import web

    auth = request.get("auth")
    if auth is None:
        raise web.HTTPUnauthorized(reason="Authentication required.")
    if not auth.get("is_admin"):
        raise web.HTTPForbidden(reason="Admin access required.")
    return auth


def note_refresh(discord_id: str) -> float:
    """records a refresh attempt. returns 0 if allowed, seconds-remaining if throttled."""
    now = time.monotonic()
    last = _refresh_last.get(discord_id, 0.0)
    elapsed = now - last
    if elapsed < REFRESH_THROTTLE_SECONDS:
        return REFRESH_THROTTLE_SECONDS - elapsed
    _refresh_last[discord_id] = now
    return 0.0