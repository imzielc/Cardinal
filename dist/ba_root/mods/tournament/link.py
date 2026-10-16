"""link.py — binds a discord account to an in-game account, once, across seasons.

a player asks the bot for a code (`/account link`), runs `/link <code>` in the game,
and from then on their discord id and account id know each other. registration uses this
to skip the per-season code dance entirely.
"""

from __future__ import annotations

import secrets
import time

from server.storage import Storage
from tournament.schema import LinkCodeSchema, LinkedSchema

LINK_EXPIRE_SECONDS = 24 * 3600


class Link(Storage):
    """short-lived codes and the permanent links they produce."""

    def __init__(self) -> None:
        super().__init__("linked.json", "tournament")
        self.bootstrap()

    def bootstrap(self) -> None:
        """creates the file if it is missing."""
        if not self.path.exists():
            self.commit(LinkedSchema())

    def read(self) -> LinkedSchema:
        return LinkedSchema.from_dict(super().read())

    def commit(self, data: LinkedSchema) -> None:
        super().commit(data.to_dict())

    # ---- reading ----

    def is_linked(self, discord_id: str) -> bool:
        """whether this discord user already has a game account bound."""
        return str(discord_id) in self.read().links

    def account_of(self, discord_id: str) -> str | None:
        """the game account id bound to this discord id, if any."""
        return self.read().links.get(str(discord_id))

    def discord_of(self, account_id: str) -> str | None:
        """reverse lookup: the discord id bound to this game account, if any."""
        account_id = str(account_id)
        for did, aid in self.read().links.items():
            if aid == account_id:
                return did
        return None

    # ---- codes ----

    def issue_code(self, discord_id: str) -> str:
        """gives a fresh code to a discord user, valid for LINK_EXPIRE_SECONDS."""
        db = self.read()
        self._prune(db)

        # only one pending code per user, drop any older one.
        db.codes = {
            code: entry
            for code, entry in db.codes.items()
            if entry.discord_id != str(discord_id)
        }

        code = self._unique_code(db)
        db.codes[code] = LinkCodeSchema(
            discord_id=str(discord_id),
            expires_at=time.time() + LINK_EXPIRE_SECONDS,
        )
        self.commit(db)
        return code

    def redeem(self, code: str, account_id: str) -> str | None:
        """redeems a code and binds the account id. returns the discord id or None."""
        db = self.read()
        self._prune(db)

        entry = db.codes.get(code)
        if entry is None:
            # save any pruned codes we just dropped.
            self.commit(db)
            return None

        discord_id = entry.discord_id
        del db.codes[code]
        # one discord <-> one account. relinking overrides the old binding.
        db.links[discord_id] = str(account_id)
        self.commit(db)
        return discord_id

    def unlink(self, discord_id: str) -> bool:
        """removes a discord user's link. returns whether there was one."""
        db = self.read()
        did = str(discord_id)
        if did not in db.links:
            return False
        del db.links[did]
        self.commit(db)
        return True

    # ---- internals ----

    @staticmethod
    def _unique_code(db: LinkedSchema) -> str:
        code = f"{secrets.randbelow(1_000_000):06d}"
        while code in db.codes:
            code = f"{secrets.randbelow(1_000_000):06d}"
        return code

    @staticmethod
    def _prune(db: LinkedSchema) -> None:
        """drops expired codes in place (cheap, happens on every read)."""
        now = time.time()
        db.codes = {
            code: entry
            for code, entry in db.codes.items()
            if entry.expires_at > now
        }


link = Link()