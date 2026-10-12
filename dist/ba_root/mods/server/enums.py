"""types file for storage."""

from enum import IntEnum, StrEnum


class Authority(IntEnum):
    """enum class for authority levels."""

    USER = 0
    WHITELIST = 1
    ADMIN = 2
    LEADER = 3
    HOST = 10  # for server.


class Role(StrEnum):
    """enum class for roles."""

    LEADER = "leaders"
    ADMIN = "admins"
    WHITELIST = "whitelist"
    BANLIST = "banlist"


class Utility(StrEnum):
    """enum class for utilities."""

    WHITELIST = "whitelist"
    SPECTATOR = "spectator"
    POWERUPS = "powerups"


class Playlist(IntEnum):
    """enum class for playlists."""

    TEAMS = 624678
    FFA = 617193


################# Tournament Enums ####################


class TournamentStage(StrEnum):
    REGISTRATION = "REGISTRATION"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class TournamentType(StrEnum):
    SOLO = "SOLO"
    DUO = "DUO"
    TRIO = "TRIO"
    SQUAD = "SQUAD"

    @property
    def count(self) -> int:
        """Returns the max count of players for a team"""
        mapping = {
            TournamentType.SOLO: 1,
            TournamentType.DUO: 2,
            TournamentType.TRIO: 3,
            TournamentType.SQUAD: 4,
        }
        return mapping[self]


class SeriesType(StrEnum):
    BO1 = "BEST_OF_1"
    BO3 = "BEST_OF_3"
    BO5 = "BEST_OF_5"
    BO7 = "BEST_OF_7"

    @property
    def count(self) -> int:
        """Returns the count of series needed for a team to win the match"""
        mapping = {
            SeriesType.BO1: 1,
            SeriesType.BO3: 2,
            SeriesType.BO5: 3,
            SeriesType.BO7: 4,
        }
        return mapping[self]


class TournamentMode(StrEnum):
    """how the teams are knocked out."""

    SINGLE = "SINGLE_ELIMINATION"
    DOUBLE = "DOUBLE_ELIMINATION"


class Status(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class TeamStatus(StrEnum):
    IN_INVITATION = "IN_INVITATION"
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
