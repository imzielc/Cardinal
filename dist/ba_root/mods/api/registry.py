"""route registry: register handlers with metadata, build the spec from schemas."""

from __future__ import annotations

import re
import time
from typing import Callable

REGISTRY: list[dict] = []

_PATH_PARAM = re.compile(r"{(\w+)}")


def route(
    method: str,
    path: str,
    *,
    response: type | None = None,
    body: type | None = None,
    auth: str = "none",
    redacted: bool = False,
    params: dict[str, str] | None = None,
    query: dict[str, str] | None = None,
):
    """register a handler with its metadata. decorator."""

    def decorator(func: Callable) -> Callable:
        REGISTRY.append({
            "method": method.upper(),
            "path": path,
            "handler": func,
            "response": response,
            "body": body,
            "auth": auth,
            "redacted": redacted,
            "params": params or {},
            "query": query or {},
            "description": (func.__doc__ or "").strip().split("\n")[0],
        })
        return func

    return decorator


def attach(app) -> list[str]:
    """attach every registered handler. returns warnings for duplicate routes."""
    seen = set()
    warnings = []
    for entry in REGISTRY:
        key = (entry["method"], entry["path"])
        if key in seen:
            warnings.append(f"duplicate route: {entry['method']} {entry['path']}")
            continue
        seen.add(key)
        app.router.add_route(entry["method"], entry["path"], entry["handler"])
    return warnings


# ---- spec ----

_SPEC_TTL = 60.0
_spec_cache: dict | None = None
_spec_at: float = 0.0


def build_spec() -> dict:
    """builds the spec from the registry and the schemas they point at.
    cached for a minute so spec spam doesn't rebuild anything."""
    global _spec_cache, _spec_at
    now = time.monotonic()
    if _spec_cache is not None and now - _spec_at < _SPEC_TTL:
        return _spec_cache
    spec = _build()
    _spec_cache = spec
    _spec_at = now
    return spec


def _build() -> dict:
    endpoints = []
    all_defs: dict[str, dict] = {}

    for entry in REGISTRY:
        item = {
            "method": entry["method"],
            "path": entry["path"],
            "auth": entry["auth"],
            "description": entry["description"],
        }
        if entry["redacted"]:
            item["redacted"] = True

        path_params = _path_params(entry["path"], entry["params"])
        if path_params:
            item["path_params"] = path_params

        if entry["query"]:
            item["query_params"] = [
                {"name": k, "type": v} for k, v in entry["query"].items()
            ]

        if entry["body"] is not None:
            item["request_body"] = _schema_and_defs(entry["body"], all_defs)

        if entry["response"] is not None:
            item["response"] = _schema_and_defs(entry["response"], all_defs)

        endpoints.append(item)

    return {
        "version": "1",
        "generated_at": int(time.time()),
        "endpoints": endpoints,
        "schemas": all_defs,
    }


def _schema_and_defs(cls: type, defs: dict[str, dict]) -> dict:
    """returns a $ref-and-$defs schema for cls, merging its defs into defs."""
    from pydantic import TypeAdapter

    try:
        raw = TypeAdapter(cls).json_schema(ref_template="#/schemas/{model}")
    except Exception as e:
        name = getattr(cls, "__name__", str(cls))
        return {"error": f"could not build schema for {name}: {e}"}

    for name, sub in (raw.pop("$defs", None) or {}).items():
        defs.setdefault(name, sub)

    return raw


def _path_params(path: str, overrides: dict[str, str]) -> list[dict]:
    return [
        {"name": name, "type": overrides.get(name, "string")}
        for name in _PATH_PARAM.findall(path)
    ]