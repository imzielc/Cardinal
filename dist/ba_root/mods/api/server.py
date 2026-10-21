"""entry point: wraps the aiohttp app in a self-healing loop."""

import time
from traceback import format_exc

from .app import run_app

RETRY_DELAY = 10


def main() -> None:
    while True:
        try:
            run_app()
            # a clean return from run_app means it was stopped on purpose.
            return
        except (KeyboardInterrupt, SystemExit):
            return
        except Exception:
            print(f"[api] crashed, restarting in {RETRY_DELAY}s:\n{format_exc()}")
            time.sleep(RETRY_DELAY)