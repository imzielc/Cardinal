from aiohttp import web

from api.registry import build_spec, route
from api.schema import SpecResponse


@route("GET", "/v1/spec", response=SpecResponse)
async def handler(request: web.Request) -> web.Response:
    """This document, built live from the registered routes and schemas."""
    return web.json_response(build_spec())