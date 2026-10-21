"""revoked JWT ids, checked on every authenticated request."""

import time

from server.storage import Storage

# keep revoked entries an hour longer than any token could possibly live.
BUFFER_SECONDS = 60 * 60


class Revoked(Storage):
    def __init__(self) -> None:
        super().__init__("revoked.json", "api")
        if not self.path.exists():
            self.commit({})

    def is_revoked(self, jti: str) -> bool:
        return str(jti) in self.read()

    def revoke(self, jti: str, exp: int) -> None:
        db = self.read()
        db[str(jti)] = int(exp)
        now = int(time.time())
        db = {k: v for k, v in db.items() if int(v) + BUFFER_SECONDS > now}
        self.commit(db)


revoked = Revoked()