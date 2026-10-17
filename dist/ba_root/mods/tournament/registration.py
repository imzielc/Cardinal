import time

from server import config
from server.enums import TeamStatus
from server.notify import send_event
from server.storage import Storage
from tournament.link import link
from tournament.storage import SEASONS_DIR
from tournament.graphics import runner
from tournament.schema import RegistrationSchema, MemberSchema, TeamSchema

DEFAULT_INVITE_EXPIRE_HOURS = 24.0
DECLINE_COOLDOWN_SECONDS = 30 * 60


def _default_expire_seconds() -> float:
    """the invitation expiry, in seconds (from config, defaults to 24h)."""
    try:
        hours = float(config.discord.get("invite_expire_hours", DEFAULT_INVITE_EXPIRE_HOURS))
    except (TypeError, ValueError):
        hours = DEFAULT_INVITE_EXPIRE_HOURS
    return max(0.0, hours) * 3600


def _season_team_size(season_id: str) -> int:
    """the number of players a team needs in this season, or 0 when unknown."""
    try:
        from tournament.storage import tournament as tournament_storage
        season = tournament_storage.get_season(season_id)
        return season.type.count if season else 0
    except Exception:
        return 0


class Registration(Storage):
    """storage class for registration"""

    def __init__(self, season_id: str):
        self.season_id = season_id
        super().__init__("registrations.json", SEASONS_DIR / season_id)
        self.bootstrap()

    def read(self) -> RegistrationSchema:
        """reads the registration."""
        data = super().read()
        return RegistrationSchema.from_dict(data)

    def commit(self, data: RegistrationSchema) -> None:
        """commits the registration."""
        super().commit(data.to_dict())

    def bootstrap(self):
        """creates the file if not already existing."""
        if not self.path.exists():
            self.commit(RegistrationSchema())

    # ---- lookups ----

    def is_registered(self, id: str) -> bool:
        """whether this discord id or account id is already on a team this season."""
        return str(id) in self.read().players

    def has_accepted(self, discord_id: str) -> bool:
        """whether this invited member already clicked accept."""
        db = self.read()
        team = db.teams.get(db.players.get(str(discord_id), ""))
        if not team:
            return False
        for m in team.members:
            if m.discord_id == str(discord_id):
                return m.accepted
        return False

    def get_team(self, team_id: str) -> TeamSchema | None:
        """returns a team by its id."""
        return self.read().teams.get(team_id)

    def team_of(self, discord_id: str) -> TeamSchema | None:
        """the team the discord user belongs to, if any."""
        db = self.read()
        return db.teams.get(db.players.get(str(discord_id), ""))

    # ---- expiration ----

    @staticmethod
    def is_expired(team: TeamSchema) -> bool:
        """whether the invitation of a team has run out."""
        if team.expires_at <= 0:
            return False
        return time.time() > team.expires_at

    def expired_teams(self) -> list[TeamSchema]:
        """all the teams whose invitation has expired."""
        return [team for team in self.read().teams.values() if self.is_expired(team)]

    def expire_team(self, team_id: str) -> TeamSchema | None:
        """removes an expired team, and any traces of its members. returns the removed team."""
        db = self.read()
        team = db.teams.get(team_id)
        if not team:
            return None
        for member in team.members:
            db.players.pop(member.discord_id, None)
            if member.account_id:
                db.players.pop(member.account_id, None)
        del db.teams[team_id]
        self.commit(db)
        return team

    # ---- cooldown ----

    def cooldown_remaining(self, team_id: str, discord_id: str) -> float:
        """seconds left before this member can be re-invited, 0 when no cooldown."""
        team = self.get_team(team_id)
        if not team:
            return 0.0
        ts = team.declined.get(str(discord_id), 0.0)
        if not ts:
            return 0.0
        remaining = DECLINE_COOLDOWN_SECONDS - (time.time() - ts)
        return max(0.0, remaining)

    # ---- registration ----

    def register(
        self,
        team_name: str,
        captain_discord_id: str,
        invited_members: list = [],
        expire_seconds: float | None = None,
    ):
        """registers a team/solo, pulling account ids from the link store.

        returns the team id on success, None if the name is taken,
        False if anyone is unlinked, already registered, or the roster
        does not match the season's team size."""
        captain_discord_id = str(captain_discord_id)
        invited_members = [str(x) for x in invited_members]

        if captain_discord_id in invited_members or len(set(invited_members)) != len(invited_members):
            # a person cannot be in a team twice, and the captain cannot invite himself.
            return False

        expected_team = _season_team_size(self.season_id)
        if expected_team and len(invited_members) + 1 != expected_team:
            return False

        size = len(invited_members) + 1
        everyone = [captain_discord_id] + invited_members

        accounts = {}
        for did in everyone:
            if self.is_registered(did):
                # if any of them is registered, decline it.
                return False
            aid = link.account_of(did)
            if not aid:
                return False
            if self.is_registered(aid):
                return False
            accounts[did] = aid

        if expire_seconds is None:
            expire_seconds = _default_expire_seconds()

        db = self.read()
        # case: team-name already exists
        if team_name in db.teams:
            return None
        # team_id = f"team-{len(db['teams']) + 1}"
        # we use team-name as team-id
        team_id = team_name
        now = time.time()

        members = [
            MemberSchema(
                discord_id=did,
                account_id=accounts[did],
                accepted=(did == captain_discord_id),
                invited_at=now,
            )
            for did in everyone
        ]

        # saving in players dict will help us do team lookup and verification much faster.
        for member in members:
            db.players[member.discord_id] = team_id
            db.players[member.account_id] = team_id

        # solo has nothing to accept, no point in a timer either.
        expires_at = now + expire_seconds if size > 1 and expire_seconds > 0 else 0.0

        db.teams[team_id] = TeamSchema(
            id=team_id,
            captain=captain_discord_id,
            status=TeamStatus.IN_INVITATION if size > 1 else TeamStatus.UNVERIFIED,
            members=members,
            expires_at=expires_at,
        )
        self.commit(db)
        return team_id

    def set_role(self, team_id: str, role_id: int) -> bool:
        """remembers the discord role that mentions this team."""
        db = self.read()
        team = db.teams.get(team_id)
        if not team:
            return False
        team.role_id = int(role_id)
        self.commit(db)
        return True

    def add_member(self, team_id: str, discord_id: str) -> bool:
        """adds a replacement member to an existing team."""
        discord_id = str(discord_id)
        if self.is_registered(discord_id):
            return False
        aid = link.account_of(discord_id)
        if not aid or self.is_registered(aid):
            return False

        db = self.read()
        team = db.teams.get(team_id)
        if not team:
            return False
        if any(m.discord_id == discord_id for m in team.members):
            return False

        team.members.append(MemberSchema(
            discord_id=discord_id,
            account_id=aid,
            invited_at=time.time(),
        ))
        db.players[discord_id] = team_id
        db.players[aid] = team_id
        # adding a member brings the team back to the invitation stage.
        if team.status != TeamStatus.IN_INVITATION:
            team.status = TeamStatus.IN_INVITATION
        self.commit(db)
        return True

    def delete(self, team_id: str) -> str:
        """deletes the team when someone declines the invitation."""
        db = self.read()
        team = db.teams.get(team_id)
        if not team:
            return ""

        for member in team.members:
            account_id = member.account_id
            if account_id in db.players:
                del db.players[account_id]
            discord_id = member.discord_id
            if discord_id in db.players:
                del db.players[discord_id]
        captain = team.captain
        del db.teams[team_id]
        self.commit(db)
        return captain

    def decline(self, discord_id: str) -> TeamSchema | None:
        """removes the member from their team, and records a re-invite cooldown.
        refuses once the team is fully verified."""
        team = self.team_of(discord_id)
        if team and team.status == TeamStatus.VERIFIED:
            return None
        return self._remove_member(discord_id, cooldown=True)

    def leave(self, discord_id: str) -> TeamSchema | None:
        """removes the member from their team (their own choice), no cooldown."""
        return self._remove_member(discord_id, cooldown=False)

    def _remove_member(self, discord_id: str, cooldown: bool) -> TeamSchema | None:
        discord_id = str(discord_id)
        db = self.read()
        team_id = db.players.get(discord_id)
        if not team_id:
            return None
        team = db.teams.get(team_id)
        if not team:
            return None
        # a captain never gets removed this way (they either dissolve or stay).
        if team.captain == discord_id:
            return None

        member = next((m for m in team.members if m.discord_id == discord_id), None)
        if member is None:
            return None

        db.players.pop(discord_id, None)
        if member.account_id:
            db.players.pop(member.account_id, None)

        team.members = [m for m in team.members if m.discord_id != discord_id]
        team.status = TeamStatus.IN_INVITATION
        if cooldown:
            team.declined[discord_id] = time.time()
        self.commit(db)
        return team

    def set_thread(self, team_id: str, thread_id: int) -> bool:
        """remembers the private thread an invitation lives in."""
        db = self.read()
        team = db.teams.get(team_id)
        if not team:
            return False
        team.thread_id = thread_id
        self.commit(db)
        return True

    def set_invitation_message(self, team_id: str, message_id: int) -> bool:
        """remembers the tracked invitation message inside the team's thread."""
        db = self.read()
        team = db.teams.get(team_id)
        if not team:
            return False
        team.invitation_message_id = int(message_id)
        self.commit(db)
        return True

    def accept(self, discord_id: str) -> bool | None:
        """marks an invited member as accepted. None when the invitation expired."""
        discord_id = str(discord_id)
        db = self.read()
        # lookup team id from players map
        team_id = db.players.get(discord_id)
        if not team_id:
            return False
        team = db.teams.get(team_id)
        if not team:
            return False
        if self.is_expired(team):
            return None
        if team.status == TeamStatus.VERIFIED:
            return False

        for member in team.members:
            if member.discord_id == discord_id:
                member.accepted = True
                member.invited_at = time.time()

                # check if everyone on the team has accepted the invitation.
                if all(m.accepted for m in team.members):
                    # update the team status
                    team.status = TeamStatus.UNVERIFIED
                self.commit(db)
                return True
        return False

    def verify(self, account_id: str, device_uuid: str):
        """binds the device uuid of an already-linked account.

        returns True on success, or a reason string:
        "not_registered" — the account is not on any team this season.
        "already"        — this account already verified."""
        account_id = str(account_id)
        db = self.read()
        team_id = db.players.get(account_id)
        if not team_id:
            return "not_registered"
        team = db.teams.get(team_id)
        if not team:
            return "not_registered"

        for member in team.members:
            if member.account_id == account_id:
                # case: the guy is already verified
                if member.device_uuid:
                    return "already"
                member.device_uuid = device_uuid
                all_verified = all(m.device_uuid for m in team.members)
                if all_verified:
                    # update the team status
                    team.status = TeamStatus.VERIFIED
                # commit first, so the bot reads fresh state when it handles the event.
                self.commit(db)

                # notify the bot on every verify, so the invitation view can
                # show who is left to verify in real time.
                send_event("team_verified", season_id=self.season_id, team_id=team_id)

                if all_verified:
                    # the registration card only fires once, on the last member.
                    mention = f"<@&{team.role_id}>" if team.role_id else f"<@{team.captain}>"
                    runner.run(data={
                        "type": "registration",
                        "name": team_id,
                        "season_id": self.season_id,
                        "mention": mention,
                    })
                return True
        return "not_registered"

    def change_uuid(self, discord_id: str, new_uuid: str) -> bool:
        """changes the uuid of a player"""
        db = self.read()
        team_id = db.players.get(str(discord_id))
        if not team_id:
            return False
        team = db.teams.get(team_id)
        if not team:
            return False
        for member in team.members:
            if member.discord_id == str(discord_id):
                member.device_uuid = new_uuid
                self.commit(db)
                return True
        return False