from aiohttp import web

from server import config
from api.registry import route
from api.schema import HealthResponse


@route("GET", "/v1/health", response=HealthResponse)
async def handler(request: web.Request) -> web.Response:
    """Liveness check."""
    resp = HealthResponse(
        status="ok",
        public_url=str(config.api.get("public_url") or ""),
    )
    return web.json_response(resp.to_dict())
