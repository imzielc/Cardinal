from aiohttp import web

from server import config


def register(app: web.Application) -> None:
    app.router.add_get("/api/v1/health", handler)


async def handler(request: web.Request) -> web.Response:
    return web.json_response({
        "status": "ok",
        "public_url": str(config.api.get("public_url") or ""),
    })