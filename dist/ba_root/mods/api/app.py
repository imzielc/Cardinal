"""aiohttp application factory."""

from aiohttp import web

from server import config
from api import cloudflared
from api.auth import _load_jwt
from api.middlewares import (
    auth_middleware,
    cache_middleware,
    cors_middleware,
    error_middleware,
)
from api.routes import register


def run_app() -> None:
    if not config.api.enable:
        raise RuntimeError("api is not enabled in config")
    if not config.api.jwt_secret:
        raise RuntimeError("api.jwt_secret is empty — restart the server to generate it")

    _load_jwt()
    cloudflared.start()

    app = web.Application(middlewares=[
        error_middleware,
        cors_middleware,
        auth_middleware,
        cache_middleware,
    ])
    for warning in register(app):
        print(f"[api] {warning}")

    host = str(config.api.host)
    port = int(config.api.port)
    public = str(config.api.get("public_url") or "")
    line = f"[api] listening on http://{host}:{port}"
    if public:
        line += f"  public: {public}"
    print(line)
    web.run_app(app, host=host, port=port, print=None, access_log=None)