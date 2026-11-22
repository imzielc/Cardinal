"""types file for storage."""

from enum import IntEnum, StrEnum


class Authority(IntEnum):
    USER = 0
    WHITELIST = 1
    ADMIN = 2
    LEADER = 3
    HOST = 10


class Role(StrEnum):
    LEADER = "leaders"
    ADMIN = "admins"
    WHITELIST = "whitelist"
    BANLIST = "banlist"


class Utility(StrEnum):
    WHITELIST = "whitelist"
    SPECTATOR = "spectator"
    POWERUPS = "powerups"


class Playlist(IntEnum):
    TEAMS = 624678
    FFA = 617193


class TournamentStage(StrEnum):
    NONE = "NONE"
    REGISTRATION = "REGISTRATION"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class TournamentType(StrEnum):
    SOLO = "SOLO"
    DUO = "DUO"
    TRIO = "TRIO"
    SQUAD = "SQUAD"

    @property
    def count(self) -> int:
        return {
            TournamentType.SOLO: 1,
            TournamentType.DUO: 2,
            TournamentType.TRIO: 3,
            TournamentType.SQUAD: 4,
        }[self]


class SeriesType(StrEnum):
    BO1 = "BEST_OF_1"
    BO3 = "BEST_OF_3"
    BO5 = "BEST_OF_5"
    BO7 = "BEST_OF_7"

    @property
    def count(self) -> int:
        return {
            SeriesType.BO1: 1,
            SeriesType.BO3: 2,
            SeriesType.BO5: 3,
            SeriesType.BO7: 4,
        }[self]


class TournamentMode(StrEnum):
    SINGLE = "SINGLE_ELIMINATION"
    DOUBLE = "DOUBLE_ELIMINATION"


class Status(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class TeamStatus(StrEnum):
    IN_INVITATION = "IN_INVITATION"
    REGISTERED = "REGISTERED"