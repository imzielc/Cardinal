"""route registration. importing each module triggers the @route decorators."""

from aiohttp import web

from api.registry import attach


def register(app: web.Application) -> list[str]:
    from api.routes import auth, health, private, public, spec

    return attach(app)