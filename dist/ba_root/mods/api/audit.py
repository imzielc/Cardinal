"""best-effort audit trail of admin mutations."""

import time

from server.storage import Storage

MAX_ENTRIES = 5000


class Audit(Storage):
    def __init__(self) -> None:
        super().__init__("audit.json", "api")
        if not self.path.exists():
            self.commit([])

    def log(self, actor: str, action: str, details: dict) -> None:
        # no awaits between read and commit, so single-process asyncio
        # handlers cannot interleave and lose entries.
        entries = self.read()
        if not isinstance(entries, list):
            entries = []
        entries.append({
            "at": int(time.time()),
            "actor": actor,
            "action": action,
            "details": details,
        })
        self.commit(entries[-MAX_ENTRIES:])


audit = Audit()