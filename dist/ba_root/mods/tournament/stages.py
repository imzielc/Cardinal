"""helpers for brackets.py: the rounds of the knockout stage, for both elimination modes.

a stage is {bracket: {round id: RoundSchema}}, each round is a file in a folder of its bracket:

    single elimination   main-stage/     round-of-16.json, quarter-finals.json, semi-finals.json, finals.json
    double elimination   winners/        1.json, 2.json ...
                         losers/         1.json, 2.json ...
                         grand-final/    1.json

the rounds are built whole at the start. a slot that waits for another match knows where its team
comes from (its "source": the winner "W" or loser "L" of that match), so when a match ends, its
teams are moved on to the slots waiting for them (`settle`). that is all the two modes need in common,
only building the rounds (`build_single`, `build_double`) and the end of the bracket (`outcome`) differ.

the bracket is sized to a power of two, the empty places of a double elimination bracket are byes: a
match with a bye decides itself and the other team goes on.
"""

from __future__ import annotations

import random

from server.enums import Status, TournamentMode
from tournament.schema import MatchSchema, MatchTeamSchema, RoundSchema

Stage = dict[str, dict[str, RoundSchema]]

MAIN_STAGE = "main-stage"
WINNERS = "winners"
LOSERS = "losers"
GRAND_FINAL = "grand-final"

# the order the brackets are listed in
BRACKET_RANK = {MAIN_STAGE: 0, WINNERS: 0, LOSERS: 1, GRAND_FINAL: 2}
MINIMUM_TEAMS = 4


# ---- names ----


def source(kind: str, bracket: str, round_id: str | int, match_id: str | int) -> str:
    """where a slot gets its team from. kind is "W" (winner of that match) or "L" (loser of it)."""
    return f"{kind}:{bracket}:{round_id}:{match_id}"


def parse_source(text: str) -> tuple[str, str, str, str]:
    kind, bracket, round_id, match_id = text.split(":")
    return kind, bracket, round_id, match_id


def match_key(match: MatchSchema) -> str:
    """a name that is unique among all the matches of a stage."""
    return f"{match.bracket}-{match.round_id}-{match.match_id}"


def round_name(teams_count: int) -> str:
    """the name of a single elimination round, by the number of teams playing in it."""
    if teams_count >= 16:
        return f"round-of-{teams_count}"
    if teams_count == 8:
        return "quarter-finals"
    if teams_count == 4:
        return "semi-finals"
    return "finals"


def seed_order(size: int) -> list[int]:
    """the seeds in bracket order (1 v size, 2 v size-1 ... spread out so the top seeds meet late)."""
    if size == 1:
        return [1]
    order = []
    for seed in seed_order(size // 2):
        order += [seed, size + 1 - seed]
    return order


# ---- building ----


def _slot(idx: int, origin: str | None = None, name: str | None = None, bye: bool = False) -> MatchTeamSchema:
    return MatchTeamSchema(idx=idx, name=name, source=origin, bye=bye)


def _match(bracket: str, round_id: str | int, match_id: str | int, first: MatchTeamSchema, second: MatchTeamSchema) -> MatchSchema:
    return MatchSchema(
        match_id=str(match_id),
        teams=[first, second],
        bracket=bracket,
        round_id=str(round_id),
    )


def build_single(pairings: list[tuple[str, str]]) -> Stage:
    """single elimination: the first round is the pairings (a power of two of them), every later
    round is the winners of the one before. the finals round also has the third place match,
    between the losers of the semi-finals."""
    count = len(pairings)
    if count < 1 or count & (count - 1):
        raise ValueError("The number of pairings must be a power of 2.")

    rounds: dict[str, RoundSchema] = {}
    previous: str | None = None
    number = 1
    while count >= 1:
        name = round_name(count * 2)
        matches = {}
        for match_id in range(1, count + 1):
            if previous is None:
                first, second = pairings[match_id - 1]
                slots = (_slot(0, name=first), _slot(1, name=second))
            else:
                slots = (
                    _slot(0, source("W", MAIN_STAGE, previous, 2 * match_id - 1)),
                    _slot(1, source("W", MAIN_STAGE, previous, 2 * match_id)),
                )
            matches[str(match_id)] = _match(MAIN_STAGE, name, match_id, *slots)

        if count == 1 and previous is not None:
            # the finals: "1" is for 1st/2nd, "2" is for 3rd/4th, between the losers of the semi-finals.
            matches["2"] = _match(
                MAIN_STAGE,
                name,
                2,
                _slot(0, source("L", MAIN_STAGE, previous, 1)),
                _slot(1, source("L", MAIN_STAGE, previous, 2)),
            )
        rounds[name] = RoundSchema(matches=matches, number=number)
        previous, number, count = name, number + 1, count // 2
    return {MAIN_STAGE: rounds}


def build_double(teams: list[str], rng: random.Random | None = None) -> Stage:
    """double elimination for the teams (any number, at least 4): the whole bracket, with the
    byes settled. the seeding is random."""
    if len(teams) < MINIMUM_TEAMS:
        raise ValueError(f"Double elimination needs at least {MINIMUM_TEAMS} teams.")
    rng = rng or random.Random()

    size = 1 << (len(teams) - 1).bit_length()  # the next power of two
    rounds = size.bit_length() - 1

    seeded = list(teams)
    rng.shuffle(seeded)
    order = seed_order(size)

    def team_at(seed: int) -> str | None:
        return None if seed > len(seeded) else seeded[seed - 1]

    # ---- winners bracket ----
    winners: dict[str, RoundSchema] = {}
    first_round = {}
    for match_id in range(1, size // 2 + 1):
        slots = []
        for idx, seed in enumerate(order[2 * match_id - 2 : 2 * match_id]):
            name = team_at(seed)
            slots.append(_slot(idx, name=name, bye=name is None))
        first_round[str(match_id)] = _match(WINNERS, 1, match_id, *slots)
    winners["1"] = RoundSchema(matches=first_round, number=1)

    for round_id in range(2, rounds + 1):
        matches = {}
        for match_id in range(1, size // 2**round_id + 1):
            matches[str(match_id)] = _match(
                WINNERS,
                round_id,
                match_id,
                _slot(0, source("W", WINNERS, round_id - 1, 2 * match_id - 1)),
                _slot(1, source("W", WINNERS, round_id - 1, 2 * match_id)),
            )
        winners[str(round_id)] = RoundSchema(matches=matches, number=round_id)

    # ---- losers bracket ----
    losers: dict[str, RoundSchema] = {}
    for round_id in range(1, 2 * (rounds - 1) + 1):
        matches = {}
        if round_id == 1:
            # the losers of the first winners round play each other.
            for match_id in range(1, size // 4 + 1):
                matches[str(match_id)] = _match(
                    LOSERS,
                    round_id,
                    match_id,
                    _slot(0, source("L", WINNERS, 1, 2 * match_id - 1)),
                    _slot(1, source("L", WINNERS, 1, 2 * match_id)),
                )
        elif round_id % 2 == 0:
            # the survivors meet the teams that just dropped from the winners bracket.
            dropping = round_id // 2 + 1  # the winners round the drop comes from
            count = size >> dropping
            for match_id in range(1, count + 1):
                # alternate the order of the drops, so a team does not meet who it just played.
                dropped = count - match_id + 1 if (round_id // 2) % 2 == 1 else match_id
                matches[str(match_id)] = _match(
                    LOSERS,
                    round_id,
                    match_id,
                    _slot(0, source("W", LOSERS, round_id - 1, match_id)),
                    _slot(1, source("L", WINNERS, dropping, dropped)),
                )
        else:
            # the survivors play each other.
            for match_id in range(1, len(losers[str(round_id - 1)].matches) // 2 + 1):
                matches[str(match_id)] = _match(
                    LOSERS,
                    round_id,
                    match_id,
                    _slot(0, source("W", LOSERS, round_id - 1, 2 * match_id - 1)),
                    _slot(1, source("W", LOSERS, round_id - 1, 2 * match_id)),
                )
        losers[str(round_id)] = RoundSchema(matches=matches, number=round_id)

    # ---- grand final: the winners champion vs the losers champion ----
    grand_final = RoundSchema(
        matches={
            "1": _match(
                GRAND_FINAL,
                1,
                1,
                _slot(0, source("W", WINNERS, rounds, 1)),
                _slot(1, source("W", LOSERS, 2 * (rounds - 1), 1)),
            )
        },
        number=1,
    )
    return {WINNERS: winners, LOSERS: losers, GRAND_FINAL: {"1": grand_final}}


# ---- reading ----


def sorted_rounds(rounds: dict[str, RoundSchema]) -> list[tuple[str, RoundSchema]]:
    return sorted(rounds.items(), key=lambda item: item[1].number)


def all_matches(stage: Stage) -> list[MatchSchema]:
    """every match, bracket by bracket, round by round."""
    matches = []
    for bracket in sorted(stage, key=lambda name: BRACKET_RANK.get(name, 9)):
        for _, round_data in sorted_rounds(stage[bracket]):
            for match_id in sorted(round_data.matches, key=int):
                matches.append(round_data.matches[match_id])
    return matches


def _index(stage: Stage) -> dict[tuple[str, str, str], MatchSchema]:
    return {(m.bracket, m.round_id, m.match_id): m for m in all_matches(stage)}


def is_known(slot: MatchTeamSchema) -> bool:
    """the slot has a team, or is known to stay empty (a bye)."""
    return slot.name is not None or slot.bye


def _outcome(match: MatchSchema, kind: str) -> MatchTeamSchema:
    """the slot that won (W) or lost (L) a completed match."""
    return match.teams[match.winner_idx if kind == "W" else match.loser_idx]


# ---- moving teams along ----


def settle(stage: Stage, mode: TournamentMode) -> None:
    """moves the results of the completed matches into the slots waiting for them, decides the
    matches that have a bye, and then handles the end of the bracket (the grand final's reset) and
    the status of every round. running it again changes nothing, so it also repairs a stage that
    was only partly saved."""
    matches = all_matches(stage)
    index = _index(stage)

    changed = True
    while changed:
        changed = False
        for match in matches:
            if match.status == Status.COMPLETED:
                continue
            for slot in match.teams:
                if is_known(slot) or slot.source is None:
                    continue
                kind, bracket, round_id, match_id = parse_source(slot.source)
                origin = index[(bracket, round_id, match_id)]
                if origin.status != Status.COMPLETED:
                    continue
                taken = _outcome(origin, kind)
                slot.name, slot.bye = taken.name, taken.bye
                changed = True

            if all(is_known(slot) for slot in match.teams) and any(slot.bye for slot in match.teams):
                # a bye decides the match, the other team goes on (two byes make a bye).
                bye_slots = [slot for slot in match.teams if slot.bye]
                match.winner_idx = 1 - bye_slots[0].idx if len(bye_slots) == 1 else 0
                match.loser_idx = 1 - match.winner_idx
                match.status = Status.COMPLETED
                match.is_bye = True
                changed = True

    if mode == TournamentMode.DOUBLE:
        _settle_grand_final(stage)
    _update_statuses(stage)


def _settle_grand_final(stage: Stage) -> None:
    matches = stage[GRAND_FINAL]["1"].matches
    first = matches["1"]
    if first.status == Status.COMPLETED and first.winner_idx == 1 and "2" not in matches:
        # the losers champion won, both teams have lost once now. they play again.
        matches["2"] = _match(
            GRAND_FINAL,
            1,
            2,
            _slot(0, name=first.teams[0].name),
            _slot(1, name=first.teams[1].name),
        )


def _update_statuses(stage: Stage) -> None:
    """a round is completed when all its matches are, in progress when a match of it can be played."""
    for rounds in stage.values():
        for round_data in rounds.values():
            matches = round_data.matches.values()
            if all(match.status == Status.COMPLETED for match in matches):
                round_data.status = Status.COMPLETED
            elif any(
                match.status != Status.COMPLETED and all(slot.name is not None for slot in match.teams)
                for match in matches
            ):
                round_data.status = Status.IN_PROGRESS
            else:
                round_data.status = Status.PENDING


def active_rounds(stage: Stage) -> list[str]:
    """the rounds that can be played right now, as "bracket/round"."""
    return [
        f"{bracket}/{round_id}"
        for bracket in sorted(stage, key=lambda name: BRACKET_RANK.get(name, 9))
        for round_id, round_data in sorted_rounds(stage[bracket])
        if round_data.status == Status.IN_PROGRESS
    ]


def depth(stage: Stage) -> dict[str, int]:
    """how many matches deep each match is, the matches that feed a match are shallower."""
    index = _index(stage)
    result: dict[str, int] = {}

    def of(match: MatchSchema) -> int:
        key = match_key(match)
        if key not in result:
            feeders = [
                of(index[parse_source(slot.source)[1:]]) for slot in match.teams if slot.source
            ]
            if not feeders and match.bracket == GRAND_FINAL and match.match_id != "1":
                # the reset match follows the first grand final.
                feeders = [of(index[(GRAND_FINAL, match.round_id, "1")])]
            result[key] = 1 + max(feeders, default=0)
        return result[key]

    for match in index.values():
        of(match)
    return result


def playable(stage: Stage) -> list[MatchSchema]:
    """the matches of the rounds in progress whose teams are known, without the matches a bye decided.
    the order is steady: a match always comes after the ones that feed it."""
    depths = depth(stage)
    found = [
        match
        for bracket in stage.values()
        for round_data in bracket.values()
        if round_data.status == Status.IN_PROGRESS
        for match in round_data.matches.values()
        if not match.is_bye and all(slot.name is not None for slot in match.teams)
    ]
    found.sort(
        key=lambda m: (
            depths[match_key(m)],
            BRACKET_RANK.get(m.bracket, 9),
            stage[m.bracket][m.round_id].number,
            int(m.match_id),
        )
    )
    return found


# ---- the end ----


def outcome(stage: Stage, mode: TournamentMode) -> tuple[bool, list[dict]]:
    """whether the bracket is over, and the final ranks ({"rank", "team"}) when it is."""
    if mode == TournamentMode.DOUBLE:
        matches = stage[GRAND_FINAL]["1"].matches
        decider = matches["2"] if "2" in matches else matches["1"]
        if decider.status != Status.COMPLETED:
            return False, []
        return True, _double_placements(stage, decider)

    # single elimination: over when the finals round (the final and the third place match) is.
    finals = sorted_rounds(stage[MAIN_STAGE])[-1][1]
    if finals.status != Status.COMPLETED:
        return False, []
    placements = []
    for match_id, rank in (("1", 1), ("2", 3)):
        match = finals.matches.get(match_id)
        if match:
            placements.append({"rank": rank, "team": match.teams[match.winner_idx].name})
            placements.append({"rank": rank + 1, "team": match.teams[match.loser_idx].name})
    return True, placements


def _double_placements(stage: Stage, decider: MatchSchema) -> list[dict]:
    """final ranks, by the order the teams were knocked out. teams knocked out in the same round
    share a rank (there is no third place match: the team that lost the losers final is third)."""
    result = [
        {"rank": 1, "team": decider.teams[decider.winner_idx].name},
        {"rank": 2, "team": decider.teams[decider.loser_idx].name},
    ]

    # the losers bracket: the later the round a team lost in, the better its rank.
    by_round: dict[int, list[str]] = {}
    for round_data in stage[LOSERS].values():
        for match in round_data.matches.values():
            if not match.is_bye:
                by_round.setdefault(round_data.number, []).append(match.teams[match.loser_idx].name)

    rank = 3
    for number in sorted(by_round, reverse=True):
        for team in by_round[number]:
            result.append({"rank": rank, "team": team})
        rank += len(by_round[number])
    return result
