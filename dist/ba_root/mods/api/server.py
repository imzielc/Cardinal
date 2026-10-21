"""entry point: wraps the aiohttp app in a self-healing loop."""

import sys
from pathlib import Path

# sys.path[0] is the directory of whatever file was launched. when we're
# run as a script, that's mods/api/, and `from server import config` would
# find this file instead of the server/ package. prepend mods/ so packages
# resolve correctly no matter how this file is started.
_MODS = Path(__file__).resolve().parent.parent
if str(_MODS) not in sys.path:
    sys.path.insert(0, str(_MODS))

import time
from traceback import format_exc

from api.app import run_app

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


if __name__ == "__main__":
    main()