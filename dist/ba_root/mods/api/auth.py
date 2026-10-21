"""jwt issuing and verifying, secret code comparison."""

import secrets
import time

from server import config

JWT_ALG = "HS256"
JWT_TTL_SECONDS = 30 * 24 * 3600

_JWT = None


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


def issue_jwt(sub: str, kind: str) -> str:
    jwt = _load_jwt()
    now = int(time.time())
    return jwt.encode(
        {
            "sub": sub,
            "kind": kind,
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