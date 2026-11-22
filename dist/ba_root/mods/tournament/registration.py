import time

from server import config
from server.enums import TeamStatus, TournamentStage, TournamentType
from server.notify import send_event
from server.storage import Storage
from tournament.link import link
from tournament.storage import SEASONS_DIR
from tournament.graphics import runner
from tournament.schema import RegistrationSchema, MemberSchema, TeamSchema

DEFAULT_INVITE_EXPIRE_HOURS = 24.0
DECLINE_COOLDOWN_SECONDS = 30 * 60

REMINDER_WINDOW_SECONDS = 24 * 3600
REMINDER_INTERVAL_SECONDS = 6 * 3600
MAX_REMINDERS = 4

BENCH_CAPACITY = {
    TournamentType.SOLO: 0,
    TournamentType.DUO: 1,
    TournamentType.TRIO: 1,
    TournamentType.SQUAD: 2,
}


def _default_expire_seconds() -> float:
    try:
        hours = float(config.discord.get("invite_expire_hours", DEFAULT_INVITE_EXPIRE_HOURS))
    except (TypeError, ValueError):
        hours = DEFAULT_INVITE_EXPIRE_HOURS
    return max(0.0, hours) * 3600


def _season_team_size(season_id: str) -> int:
    try:
        from tournament.storage import seasons
        season = seasons.get_season(season_id)
        return season.type.count if season else 0
    except Exception:
        return 0


def _bench_capacity(season_id: str) -> int:
    try:
        from tournament.storage import seasons
        season = seasons.get_season(season_id)
        if not season:
            return 0
        return BENCH_CAPACITY.get(season.type, 0)
    except Exception:
        return 0


class Registration(Storage):
    def __init__(self, season_id: str):
        self.season_id = season_id
        super().__init__("registrations.json", SEASONS_DIR / season_id)
        self.bootstrap()

    def read(self) -> RegistrationSchema:
        return RegistrationSchema.from_dict(super().read())

    def commit(self, data: RegistrationSchema) -> None:
        super().commit(data.to_dict())

    def bootstrap(self):
        if not self.path.exists():
            self.commit(RegistrationSchema())

    # ---- lookups ----

    def is_registered(self, id: str) -> bool:
        return str(id) in self.read().players

    def has_accepted(self, discord_id: str) -> bool:
        db = self.read()
        team = db.teams.get(db.players.get(str(discord_id), ""))
        if not team:
            return False
        for m in team.mains:
            if m.discord_id == str(discord_id):
                return m.accepted
        return False

    def get_team(self, team_id: str) -> TeamSchema | None:
        return self.read().teams.get(team_id)

    def team_of(self, discord_id: str) -> TeamSchema | None:
        db = self.read()
        return db.teams.get(db.players.get(str(discord_id), ""))

    def find_member(self, discord_id: str) -> tuple[TeamSchema, MemberSchema] | None:
        db = self.read()
        team_id = db.players.get(str(discord_id))
        if not team_id:
            return None
        team = db.teams.get(team_id)
        if not team:
            return None
        for member in team.members:
            if member.discord_id == str(discord_id):
                return team, member
        return None

    # ---- expiration ----

    @staticmethod
    def is_expired(team: TeamSchema) -> bool:
        if team.status == TeamStatus.REGISTERED:
            return False
        if team.expires_at <= 0:
            return False
        return time.time() > team.expires_at

    def expired_teams(self) -> list[TeamSchema]:
        return [team for team in self.read().teams.values() if self.is_expired(team)]

    def expire_team(self, team_id: str) -> TeamSchema | None:
        db = self.read()
        team = db.teams.get(team_id)
        if not team:
            return None
        if team.status == TeamStatus.REGISTERED:
            return None
        for member in team.members:
            db.players.pop(member.discord_id, None)
            if member.account_id:
                db.players.pop(member.account_id, None)
        del db.teams[team_id]
        self.commit(db)
        return team

    def extend_all_expiries(self, hours: float) -> int:
        seconds = hours * 3600
        db = self.read()
        count = 0
        for team in db.teams.values():
            if team.expires_at <= 0:
                continue
            team.expires_at += seconds
            count += 1
        if count:
            self.commit(db)
        return count

    # ---- reminders ----

    def reminder_due(self, team: TeamSchema) -> bool:
        if team.status == TeamStatus.REGISTERED:
            return False
        if team.expires_at <= 0:
            return False
        if team.reminders_sent >= MAX_REMINDERS:
            return False

        if all(m.accepted for m in team.mains):
            return False

        now = time.time()
        window_start = team.expires_at - REMINDER_WINDOW_SECONDS
        if now < window_start:
            return False

        if team.last_reminded_at > 0:
            reference = team.last_reminded_at
        else:
            invites = [m.invited_at for m in team.mains if m.invited_at > 0]
            if not invites:
                return False
            reference = min(invites)
        return (now - reference) >= REMINDER_INTERVAL_SECONDS

    def mark_reminded(self, team_id: str) -> None:
        db = self.read()
        team = db.teams.get(team_id)
        if not team:
            return
        team.reminders_sent += 1
        team.last_reminded_at = time.time()
        self.commit(db)

    # ---- cooldown ----

    def cooldown_remaining(self, team_id: str, discord_id: str) -> float:
        team = self.get_team(team_id)
        if not team:
            return 0.0
        ts = team.declined.get(str(discord_id), 0.0)
        if not ts:
            return 0.0
        return max(0.0, DECLINE_COOLDOWN_SECONDS - (time.time() - ts))

    # ---- registration ----

    def register(
        self,
        team_name: str,
        captain_discord_id: str,
        invited_members: list = [],
        expire_seconds: float | None = None,
    ):
        captain_discord_id = str(captain_discord_id)
        invited_members = [str(x) for x in invited_members]

        if captain_discord_id in invited_members or len(set(invited_members)) != len(invited_members):
            return False

        expected_team = _season_team_size(self.season_id)
        if expected_team and len(invited_members) + 1 != expected_team:
            return False

        size = len(invited_members) + 1
        everyone = [captain_discord_id] + invited_members

        accounts = {}
        for did in everyone:
            if self.is_registered(did):
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
        if team_name in db.teams:
            return None
        team_id = team_name
        now = time.time()

        members = [
            MemberSchema(
                discord_id=did,
                account_id=accounts[did],
                accepted=(did == captain_discord_id),
                invited_at=now,
                role="main",
            )
            for did in everyone
        ]

        for member in members:
            db.players[member.discord_id] = team_id
            db.players[member.account_id] = team_id

        expires_at = now + expire_seconds if size > 1 and expire_seconds > 0 else 0.0

        db.teams[team_id] = TeamSchema(
            id=team_id,
            captain=captain_discord_id,
            status=TeamStatus.IN_INVITATION if size > 1 else TeamStatus.REGISTERED,
            members=members,
            expires_at=expires_at,
        )
        self.commit(db)

        if size == 1:
            self._post_registration_card(team_id, db.teams[team_id])
        return team_id

    def _post_registration_card(self, team_id: str, team: TeamSchema) -> None:
        mention = f"<@&{team.role_id}>" if team.role_id else f"<@{team.captain}>"
        runner.run(data={
            "type": "registration",
            "name": team_id,
            "season_id": self.season_id,
            "mention": mention,
        })

    def set_role(self, team_id: str, role_id: int) -> bool:
        db = self.read()
        team = db.teams.get(team_id)
        if not team:
            return False
        team.role_id = int(role_id)
        self.commit(db)
        return True

    def change_account_id(self, team_id: str, discord_id: str, new_account_id: str) -> bool:
        db = self.read()
        team = db.teams.get(team_id)
        if not team:
            return False

        member = next((m for m in team.mains if m.discord_id == str(discord_id)), None)
        if member is None:
            return False

        old_account_id = member.account_id
        if old_account_id == new_account_id:
            return False

        if old_account_id and db.players.get(old_account_id) == team_id:
            del db.players[old_account_id]
        db.players[new_account_id] = team_id

        member.account_id = new_account_id
        member.device_uuid = ""
        member.uuid_history = []
        member.pending_uuid = ""
        member.pending_at = 0.0
        member.last_rejected_uuid = ""

        self.commit(db)
        return True

    def add_member(self, team_id: str, discord_id: str) -> bool:
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
            role="main",
        ))
        db.players[discord_id] = team_id
        db.players[aid] = team_id
        if team.status != TeamStatus.IN_INVITATION:
            team.status = TeamStatus.IN_INVITATION
        team.reminders_sent = 0
        team.last_reminded_at = 0.0
        self.commit(db)
        return True

    # ---- bench ----

    def add_bench(self, team_id: str, discord_id: str) -> str | bool:
        discord_id = str(discord_id)
        team = self.get_team(team_id)
        if not team:
            return "team not found"
        if len(team.bench) >= _bench_capacity(self.season_id):
            return "bench is full"
        if any(m.discord_id == discord_id for m in team.members):
            return "already on this team"
        aid = link.account_of(discord_id)
        if not aid:
            return "not linked"
        if self.is_registered(discord_id) or self.is_registered(aid):
            return "already on another team"

        db = self.read()
        db.teams[team_id].members.append(MemberSchema(
            discord_id=discord_id,
            account_id=aid,
            invited_at=time.time(),
            accepted=True,
            role="bench",
        ))
        db.players[discord_id] = team_id
        db.players[aid] = team_id
        self.commit(db)
        return True

    def remove_bench(self, team_id: str, discord_id: str) -> bool:
        discord_id = str(discord_id)
        db = self.read()
        team = db.teams.get(team_id)
        if not team:
            return False
        member = next((b for b in team.bench if b.discord_id == discord_id), None)
        if member is None:
            return False
        db.players.pop(discord_id, None)
        if member.account_id:
            db.players.pop(member.account_id, None)
        team.members = [m for m in team.members if m.discord_id != discord_id]
        self.commit(db)
        return True

    # ---- uuid ----

    def uuid_accept(self, discord_id: str) -> bool:
        db = self.read()
        team_id = db.players.get(str(discord_id))
        if not team_id:
            return False
        team = db.teams.get(team_id)
        if not team:
            return False
        for member in team.members:
            if member.discord_id == str(discord_id):
                target = member.pending_uuid or member.last_rejected_uuid
                if not target:
                    return False
                member.device_uuid = target
                member.uuid_history = [target]
                member.pending_uuid = ""
                member.pending_at = 0.0
                member.last_rejected_uuid = ""
                self.commit(db)
                return True
        return False

    def uuid_reject(self, discord_id: str) -> bool:
        db = self.read()
        team_id = db.players.get(str(discord_id))
        if not team_id:
            return False
        team = db.teams.get(team_id)
        if not team:
            return False
        for member in team.members:
            if member.discord_id == str(discord_id):
                if not member.pending_uuid:
                    return False
                member.last_rejected_uuid = member.pending_uuid
                member.pending_uuid = ""
                member.pending_at = 0.0
                self.commit(db)
                return True
        return False

    def change_uuid(self, discord_id: str, new_uuid: str) -> bool:
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
                member.uuid_history = [new_uuid]
                member.pending_uuid = ""
                member.pending_at = 0.0
                member.last_rejected_uuid = ""
                self.commit(db)
                return True
        return False

    # ---- member removal ----

    def delete(self, team_id: str) -> str:
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
        team = self.team_of(discord_id)
        if team and team.status == TeamStatus.REGISTERED:
            return None
        return self._remove_member(discord_id, cooldown=True)

    def leave(self, discord_id: str) -> TeamSchema | None:
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
        if team.captain == discord_id:
            return None

        member = next((m for m in team.mains if m.discord_id == discord_id), None)
        if member is None:
            return None

        db.players.pop(discord_id, None)
        if member.account_id:
            db.players.pop(member.account_id, None)

        team.members = [m for m in team.members if m.discord_id != discord_id]
        team.status = TeamStatus.IN_INVITATION
        team.reminders_sent = 0
        team.last_reminded_at = 0.0
        if cooldown:
            team.declined[discord_id] = time.time()
        self.commit(db)
        return team

    def set_thread(self, team_id: str, thread_id: int) -> bool:
        db = self.read()
        team = db.teams.get(team_id)
        if not team:
            return False
        team.thread_id = thread_id
        self.commit(db)
        return True

    def set_invitation_message(self, team_id: str, message_id: int) -> bool:
        db = self.read()
        team = db.teams.get(team_id)
        if not team:
            return False
        team.invitation_message_id = int(message_id)
        self.commit(db)
        return True

    def accept(self, discord_id: str) -> bool | None:
        discord_id = str(discord_id)
        db = self.read()
        team_id = db.players.get(discord_id)
        if not team_id:
            return False
        team = db.teams.get(team_id)
        if not team:
            return False
        if self.is_expired(team):
            return None
        if team.status == TeamStatus.REGISTERED:
            return False

        for member in team.mains:
            if member.discord_id == discord_id:
                member.accepted = True
                member.invited_at = time.time()
                if all(m.accepted for m in team.mains):
                    team.status = TeamStatus.REGISTERED
                    self.commit(db)
                    send_event("team_verified", season_id=self.season_id, team_id=team_id)
                    self._post_registration_card(team_id, team)
                    return True
                self.commit(db)
                return True
        return False

    # ---- waitlist ----

    def promote_from_waitlist(self, slots: int = 1) -> list[dict]:
        try:
            from tournament.storage import seasons
            season = seasons.get_season(self.season_id)
        except Exception:
            return []
        if season is None or season.stage not in (TournamentStage.NONE, TournamentStage.REGISTRATION):
            return []

        try:
            from tournament.waitlist import Waitlist
            wl = Waitlist(self.season_id)
        except Exception:
            return []

        promoted: list[dict] = []
        for _ in range(max(0, slots)):
            entry = wl.pop_next()
            if entry is None:
                break
            team_id = self.register(
                team_name=entry.team_name,
                captain_discord_id=entry.captain_discord_id,
                invited_members=entry.invited_member_ids,
            )
            if not team_id:
                continue
            promoted.append({
                "team_id": team_id,
                "captain_discord_id": entry.captain_discord_id,
            })
        return promoted