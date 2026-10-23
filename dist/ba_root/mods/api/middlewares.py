"""aiohttp middlewares: error, cors, auth, cache-control."""

from traceback import format_exc

from aiohttp import web

from server import config
from api.auth import is_master_token, verify_jwt
from api.revoked import revoked


def _authenticate(request: web.Request) -> dict | None:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        token = header[7:].strip()
        if token and is_master_token(token):
            return {"kind": "master", "sub": "master", "jti": ""}
        if token:
            payload = verify_jwt(token)
            if payload and not revoked.is_revoked(payload.get("jti", "")):
                return payload
    cookie = request.cookies.get("admin_session")
    if cookie:
        payload = verify_jwt(cookie)
        if payload and not revoked.is_revoked(payload.get("jti", "")):
            return payload
    return None


@web.middleware
async def auth_middleware(request: web.Request, handler):
    request["auth"] = _authenticate(request)
    return await handler(request)


@web.middleware
async def cache_middleware(request: web.Request, handler):
    response = await handler(request)
    if request.get("auth") is not None:
        response.headers["Cache-Control"] = "private, no-store"
    elif "Cache-Control" not in response.headers:
        response.headers["Cache-Control"] = "public, max-age=30"
    return response


@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        response = web.Response(status=204)
    else:
        response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = str(
        config.api.get("frontend_url") or "*"
    )
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception:
        print(f"[api] handler error: {format_exc()}")
        return web.json_response({"error": "internal_error"}, status=500)