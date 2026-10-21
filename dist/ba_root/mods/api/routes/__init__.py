"""route registration, grouped by concern."""

from aiohttp import web


def register(app: web.Application) -> None:
    from . import auth, health, private, public

    health.register(app)
    public.register(app)
    private.register(app)
    auth.register(app)