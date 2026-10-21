"""storage for brackets"""

import json
import random

from server.enums import Status, TournamentMode, TournamentStage
from server.notify import send_event
from server.storage import Storage
from tournament import stages
from tournament.storage import SEASONS_DIR
from tournament.registration import Registration
from tournament.graphics import runner
from tournament.schema import BracketsMetaSchema, MatchSchema, MatchTeamSchema, GroupStageSchema, GroupSchema, RoundSchema, TeamSchema


def round_label(match: MatchSchema) -> str:
    """human label for a knockout match. uses the match's own label when set."""
    if match.label:
        return match.label
    if match.bracket == "winners":
        return f"Winners Round {match.round_id}"
    if match.bracket == "losers":
        return f"Losers Round {match.round_id}"
    if match.bracket == "grand-final":
        return "Grand Final"
    if match.bracket == "main-stage":
        return match.round_id.replace("-", " ").title()
    return ""

class Brackets(Storage):
    """generates brackets and handles the rounds."""

    def __init__(self, season_id: str):
        super().__init__("brackets.json", SEASONS_DIR / season_id)
        self.season_id = season_id
        self.rounds_dir = self.directory / "rounds"
        self.group_stage_path = self.rounds_dir / "group-stage.json"
        self.rounds_dir.mkdir(parents=True, exist_ok=True)
        self.bootstrap()

    def bootstrap(self):
        """creates the file and setups up method"""
        self.registration = Registration(self.season_id)
        if not self.path.exists():
            self.commit_meta(BracketsMetaSchema())

    # ---- round transitions ----

    def _build_round_payload(self, new_rounds: list[str], include_pending: bool = False) -> list[dict]:
        """builds the payload for a round-start event.

        new_rounds limits the payload to matches from those rounds. an empty
        list means every currently playable match.
        """
        try:
            matches = self.list_matches(include_pending=include_pending)
        except Exception:
            return []

        brackets = self.read_meta()
        in_group = "group-stage" in brackets.active_rounds

        gs = None
        if in_group:
            try:
                gs = self.read_gs()
            except Exception:
                gs = None

        payload = []
        for key, m in matches.items():
            if m.is_bye:
                continue

            round_key = (
                f"group-stage/{m.round_id}" if in_group
                else f"{m.bracket}/{m.round_id}"
            )
            if new_rounds and round_key not in new_rounds:
                continue

            entry = {
                "key": key,
                "team1": m.teams[0].name or "TBD",
                "team2": m.teams[1].name or "TBD",
            }

            if in_group and gs and m.group_id:
                group = gs.groups.get(m.group_id)
                if group:
                    stats = {s["id"]: s for s in group.standings_sorted}
                    entry["stage"] = "group"
                    entry["group_id"] = m.group_id
                    entry["standings"] = {}
                    for name in (m.teams[0].name, m.teams[1].name):
                        if name and name in stats:
                            s = stats[name]
                            entry["standings"][name] = {
                                "wins": s.get("wins", 0),
                                "loses": s.get("loses", 0),
                                "points": s.get("points", 0),
                                "diff": s.get("diff", 0),
                            }
            else:
                entry["stage"] = "knockout"
                entry["round_label"] = round_label(m)

            payload.append(entry)

        return payload

    def _fire_round_event(self) -> None:
        """fires round_started immediately, no queueing. used for the first round."""
        payload = self._build_round_payload([])
        if payload:
            send_event("round_started", season_id=self.season_id, matches=payload)

    def _queue_round_start(self, matches: list[dict]) -> None:
        """stores a round transition for release during the night window."""
        if not matches:
            return
        brackets = self.read_meta()
        brackets.pending_round_start = matches
        self.commit_meta(brackets)

    def release_pending_rounds(self) -> bool:
        """fires the round_started event for a queued transition. returns whether anything fired.

        the pending state is cleared only after the event is actually sent.
        if the bot isn't listening, the pending round stays on disk so the
        next release attempt retries, no round is silently lost.
        """
        brackets = self.read_meta()
        pending = brackets.pending_round_start
        if not pending:
            return False
        if not send_event("round_started", season_id=self.season_id, matches=pending):
            return False
        brackets.pending_round_start = []
        self.commit_meta(brackets)
        return True

    def _try_queue_transition(self, old_rounds: list[str], completed: bool = False) -> None:
        """queues matches from newly-playable rounds. nothing to do if none or completed."""
        if completed:
            return
        brackets = self.read_meta()
        if brackets.pending_round_start:
            # a transition is already queued, it will be released in order.
            return
        new_only = [r for r in brackets.active_rounds if r not in old_rounds]
        if not new_only:
            return
        matches = self._build_round_payload(new_only)
        self._queue_round_start(matches)

    # ---- group stage ----

    def generate_group_stage(self, teams: list) -> None | bool:
        """generates the group stage brackets."""
        teams_count = len(teams)
        groups_count = (teams_count & -teams_count)
        if teams_count < groups_count * 5:
            groups_count = groups_count // 2

        assert groups_count >= 4
        if groups_count == teams_count:
            self.generate_main_stage(teams=teams)
            return

        groups = {}
        main_stage_capacity = 1 << (teams_count.bit_length() - 1)
        teams_per_group = teams_count // groups_count

        winning_teams_per_group = main_stage_capacity // groups_count
        if winning_teams_per_group * 2 - 1 > teams_per_group:
            winning_teams_per_group = winning_teams_per_group // 2

        for index in range(groups_count):
            group_id = str(index + 1)
            groups[group_id] = GroupSchema(
                rounds=self.generate_round_robin(
                    teams[index * teams_per_group : (index + 1) * teams_per_group],
                    count=teams_per_group,
                    group_id=group_id,
                ),
                standings=[],
                standings_sorted=[]
            )
            self.recalculate_group_standings(groups[group_id])

        database = GroupStageSchema(
            groups=groups,
            winning_teams_per_group=winning_teams_per_group,
            status=Status.IN_PROGRESS
        )

        self.commit_gs(data=database)

        brackets = self.read_meta()
        brackets.mode = TournamentMode.SINGLE
        brackets.started = True
        brackets.status = Status.IN_PROGRESS
        brackets.active_rounds = ["group-stage"]
        brackets.total_rounds += 1
        self.commit_meta(brackets)

        self._fire_round_event()
        return True

    def generate_round_robin(self, teams: list, count: int, group_id: str) -> dict[str, RoundSchema]:
        """generates rounds robin for teams."""
        rounds = {}

        if count % 2 != 0:
            teams = teams + [None]
            count += 1

        for round in range(1, count):
            round_matches = {}
            for i in range(count // 2):
                first = teams[i]
                last = teams[count - 1 - i]

                match = self.create_match_format(
                    team1=first,
                    team2=last,
                    match_id=str(i + 1),
                    group_id=group_id,
                    round_id=str(round),
                )

                if first is None or last is None:
                    match.status = Status.COMPLETED
                    match.winner_idx = 0 if last is None else 1

                round_matches[match.match_id] = match

            rounds[str(round)] = RoundSchema(
                matches=round_matches,
                status=Status.IN_PROGRESS if round == 1 else Status.PENDING,
            )

            teams = [teams[0]] + [teams[-1]] + teams[1:-1]
        return rounds

    def update_gs_match(self, match: MatchSchema):
        """updates the match of the group round."""
        gs = self.read_gs()
        group = gs.groups[match.group_id]
        round_id = match.round_id
        round = group.rounds[round_id]
        round.matches[match.match_id] = match

        match.status = Status.COMPLETED

        all_groups_round_completed = all(
            m.status == Status.COMPLETED
            for g in gs.groups.values()
            for m in g.rounds[round_id].matches.values()
        )

        if all_groups_round_completed:
            next_round_id = str(int(round_id) + 1)

            for g in gs.groups.values():
                if round_id in g.rounds:
                    g.rounds[round_id].status = Status.COMPLETED

                if next_round_id in g.rounds:
                    g.rounds[next_round_id].status = Status.IN_PROGRESS

        self.recalculate_group_standings(group=group)

        if all(
            round.status == Status.COMPLETED
            for g in gs.groups.values()
            for round in g.rounds.values()
        ):
            gs.status = Status.COMPLETED

            self.commit_gs(gs)
            self.send_groupstage_brackets()
            self.send_group_stage_standings()
            self.generate_main_stage()
            return

        self.commit_gs(gs)
        self.send_groupstage_brackets()
        self.send_group_stage_standings()

        if all_groups_round_completed:
            next_round_id = str(int(round_id) + 1)
            if any(next_round_id in g.rounds for g in gs.groups.values()):
                matches = self._build_round_payload([f"group-stage/{next_round_id}"])
                self._queue_round_start(matches)

    # ---- knockout stage ----

    def read_stage(self) -> stages.Stage:
        """reads all the rounds of the knockout stage."""
        stage: stages.Stage = {}
        for folder in sorted(self.rounds_dir.iterdir()):
            if not folder.is_dir():
                continue
            rounds = {
                file.stem: RoundSchema.from_dict(self.read(file))
                for file in folder.glob("*.json")
            }
            if rounds:
                stage[folder.name] = rounds
        return stage

    @staticmethod
    def snapshot(stage: stages.Stage) -> dict[str, str]:
        """the text of every round, to find out which rounds changed."""
        return {
            f"{bracket}/{round_id}": json.dumps(round_data.to_dict(), sort_keys=True)
            for bracket, rounds in stage.items()
            for round_id, round_data in rounds.items()
        }

    def commit_stage(self, stage: stages.Stage, before: dict[str, str] | None = None) -> None:
        """writes the rounds, only the ones that changed since the snapshot when there is one."""
        after = self.snapshot(stage)
        for key, text in after.items():
            if before is not None and before.get(key) == text:
                continue
            path = self.rounds_dir / f"{key}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            self.commit(json.loads(text), path)

    def start_stage(self, stage: stages.Stage, mode: TournamentMode) -> None:
        """saves a freshly built stage, makes it the active one and sends its brackets."""
        stages.settle(stage, mode)
        self.commit_stage(stage)

        brackets = self.read_meta()
        old_rounds = list(brackets.active_rounds)
        brackets.mode = mode
        brackets.started = True
        brackets.status = Status.IN_PROGRESS
        brackets.active_rounds = stages.active_rounds(stage)
        brackets.total_rounds += sum(len(rounds) for rounds in stage.values())
        self.commit_meta(brackets)
        self.send_stage_brackets()

        if old_rounds != brackets.active_rounds:
            if not old_rounds:
                self._fire_round_event()
            else:
                self._try_queue_transition(old_rounds)

    def refresh(self) -> None:
        """settles the stage again, this repairs a stage a crash only saved partly."""
        brackets = self.read_meta()
        if not brackets.started or "group-stage" in brackets.active_rounds:
            return
        stage = self.read_stage()
        if not stage:
            return
        before = self.snapshot(stage)
        stages.settle(stage, brackets.mode)
        self.commit_stage(stage, before)
        old_rounds = list(brackets.active_rounds)
        brackets.active_rounds = stages.active_rounds(stage)
        self.commit_meta(brackets)

        if old_rounds != brackets.active_rounds:
            self._try_queue_transition(old_rounds)

    def generate_double_elimination(self, teams: list) -> bool:
        """generates the double elimination bracket for the teams (any number, at least 4)."""
        self.start_stage(stages.build_double(teams), TournamentMode.DOUBLE)
        return True

    def update_match(self, match: MatchSchema) -> None:
        """updates a completed match of the knockout stage, and moves the teams along."""
        brackets = self.read_meta()
        stage = self.read_stage()
        before = self.snapshot(stage)

        stored = stage[match.bracket][match.round_id].matches[match.match_id]
        for slot, played in zip(stored.teams, match.teams):
            slot.score, slot.series = played.score, played.series
        stored.winner_idx, stored.loser_idx = match.winner_idx, match.loser_idx
        stored.last_scores = match.last_scores
        stored.status = Status.COMPLETED
        match.status = Status.COMPLETED

        stages.settle(stage, brackets.mode)
        self.commit_stage(stage, before)

        completed, placements = stages.outcome(stage, brackets.mode)
        old_rounds = list(brackets.active_rounds)
        brackets.active_rounds = stages.active_rounds(stage)
        if completed:
            brackets.status = Status.COMPLETED
            brackets.placements = placements
        self.commit_meta(brackets)
        self.send_stage_brackets()

        if completed:
            self.announce_tournament_completion()
        elif old_rounds != brackets.active_rounds:
            self._try_queue_transition(old_rounds)

    def save_match(self, match: MatchSchema) -> None:
        """saves the progress (scores) of a match that is still being played."""
        stage = self.read_stage()
        before = self.snapshot(stage)
        stored = stage[match.bracket][match.round_id].matches[match.match_id]
        for slot, played in zip(stored.teams, match.teams):
            slot.score, slot.series = played.score, played.series
        stored.last_scores = match.last_scores
        self.commit_stage(stage, before)

    def recalculate_group_standings(self, group: GroupSchema) -> None:
        """recalculates the group standings based on points, diff, and rounds won."""
        stats = {}
        for round in group.rounds.values():
            for match in round.matches.values():
                for team in match.teams:
                    team_name = team.name
                    if team_name and team_name not in stats:
                        stats[team_name] = {
                            "id": team_name,
                            "wins": 0,
                            "loses": 0,
                            "points": 0,
                            "diff": 0,
                            "rounds_won": 0,
                            "rounds_lost": 0,
                        }

        for round in group.rounds.values():
            for match in round.matches.values():
                if match.status != Status.COMPLETED:
                    continue

                if None in [team.name for team in match.teams]:
                    continue

                team1 = match.teams[0]
                team2 = match.teams[1]
                t1, t2 = team1.name, team2.name
                s1, s2 = team1.score, team2.score

                stats[t1]["rounds_won"] += s1
                stats[t1]["rounds_lost"] += s2
                stats[t2]["rounds_won"] += s2
                stats[t2]["rounds_lost"] += s1

                if match.winner_idx == team1.idx:
                    stats[t1]["wins"] += 1
                    stats[t2]["loses"] += 1
                else:
                    stats[t1]["loses"] += 1
                    stats[t2]["wins"] += 1

        for team in stats.values():
            team["diff"] = team["rounds_won"] - team["rounds_lost"]
            team["points"] = team["wins"] * 3 - team["loses"]

        sorted_teams = sorted(
            stats.values(),
            key=lambda x: (x["points"], x["diff"], x["rounds_won"]),
            reverse=True,
        )

        group.standings = [team["id"] for team in sorted_teams]
        group.standings_sorted = sorted_teams

    def send_group_stage_standings(self) -> None:
        """sends the group stage standings to discord webhook."""
        runner.run(data={"type": "group-standings", "season_id": self.season_id})

    def make_pairings(
        self, teams: dict[str, GroupSchema] | list, winning_teams_per_group: int = 0
    ) -> list[tuple[str, str]]:
        "pairs the teams for the first round of the main-stage."
        pairings = []
        if isinstance(teams, list):
            shuffled = teams.copy()
            random.shuffle(shuffled)
            for i in range(0, len(teams), 2):
                pairings.append((shuffled[i], shuffled[i + 1]))
        else:
            group_keys = list(teams.keys())
            groups_count = len(group_keys)
            offset = groups_count // 2

            for i in range(groups_count):
                front = group_keys[i]
                back = group_keys[(i + offset) % groups_count]

                if winning_teams_per_group == 1:
                    if i < offset:
                        pairings.append(
                            (teams[front].standings[0], teams[back].standings[0])
                        )
                    continue

                for index in range(winning_teams_per_group // 2):
                    team1 = teams[front].standings[index]
                    team2 = teams[back].standings[winning_teams_per_group - 1 - index]
                    pairings.append((team1, team2))
        return pairings

    def generate_main_stage(self, teams: list = []):
        """generates the main stage (single elimination)"""
        if teams:
            pairings = self.make_pairings(teams)
        else:
            gs = self.read_gs()
            pairings = self.make_pairings(gs.groups, gs.winning_teams_per_group)

        self.start_stage(stages.build_single(pairings), TournamentMode.SINGLE)

    def announce_tournament_completion(self) -> None:
        """announces the tournament completion."""
        from tournament import tournament

        db = tournament.read()
        season = db.seasons.get(self.season_id)
        if season:
            season.stage = TournamentStage.COMPLETED
        db.active_season = "0"
        tournament.commit(db)

        send_event("tournament_completed", season_id=self.season_id)

    def announce_match_start(self, match: MatchSchema) -> None:
        """announces the match start in discord."""
        from tournament import tournament
        season = tournament.get_season(self.season_id)
        send_event(
            "match_announcement",
            season_id=self.season_id,
            team1=match.teams[0].name,
            team2=match.teams[1].name,
            round_label=round_label(match),
            participant_role_id=season.participant_role_id if season else 0,
        )

    def send_stage_brackets(self) -> None:
        """sends the brackets of the knockout stage, the image depends on the mode."""
        if self.read_meta().mode == TournamentMode.DOUBLE:
            self.send_double_elimination_brackets()
        else:
            self.send_mainstage_brackets()

    def send_mainstage_brackets(self) -> None:
        """sends the mainstage brackets."""
        runner.run(data={"type": "main-stage", "season_id": self.season_id})

    def send_double_elimination_brackets(self) -> None:
        """sends the double elimination bracket."""
        runner.run(data={"type": "double-elimination", "season_id": self.season_id})

    def send_groupstage_brackets(self) -> None:
        """sends groupstage brackets with webhooks."""
        runner.run(data={"type": "group-stage", "season_id": self.season_id})

    def send_results(self, match: MatchSchema, key: str) -> None:
        team1 = match.teams[0]
        team2 = match.teams[1]
        details = {
            "team1": team1.name,
            "team2": team2.name,
            "winner": match.teams[match.winner_idx].name,
            "score1": team1.score,
            "score2": team2.score,
            "series1": team1.series,
            "series2": team2.series,
            "key": key,
            "season_id": self.season_id,
        }
        runner.run(data={"type": "results", "details": details, "season_id": self.season_id})

    def send_players_dashboard(self) -> None:
        """sends the players dashboard."""
        runner.run(data={"type": "player-standings", "season_id": self.season_id})

    def read_meta(self) -> BracketsMetaSchema:
        return BracketsMetaSchema.from_dict(self.read())

    def commit_meta(self, data: BracketsMetaSchema) -> None:
        self.commit(data.to_dict())

    def read_gs(self) -> GroupStageSchema:
        return GroupStageSchema.from_dict(self.read(self.group_stage_path))

    def commit_gs(self, data: GroupStageSchema) -> None:
        self.commit(data.to_dict(), self.group_stage_path)

    def list_matches(self, include_pending: bool = False) -> dict[str, MatchSchema]:
        """returns matches playable right now. pending rounds are excluded by default."""
        brackets = self.read_meta()
        if not brackets.active_rounds:
            return {}

        matches = {}

        if "group-stage" in brackets.active_rounds:
            round_data = self.read_gs()
            for group in round_data.groups.values():
                for round in group.rounds.values():
                    if round.status == Status.IN_PROGRESS:
                        for match in round.matches.values():
                            composite_id = f"{match.group_id}-{match.round_id}-{match.match_id}"
                            matches[composite_id] = match
        else:
            for match in stages.playable(self.read_stage()):
                matches[stages.match_key(match)] = match

        if not include_pending and brackets.pending_round_start:
            pending_keys = {m.get("key") for m in brackets.pending_round_start}
            matches = {k: v for k, v in matches.items() if k not in pending_keys}

        return matches

    def give_win_by_key(
        self, match_key: str, team_index: int, force: bool = False
    ) -> tuple[bool, str, MatchSchema | None]:
        """gives the win to a team, addressed by the match's key.

        returns (ok, message, match), where match is the updated schema on
        success or the existing match when it was already completed.
        """
        if team_index not in (1, 2):
            return False, "Team index must be either 1 or 2.", None

        matches = self.list_matches()
        if not matches:
            return False, "No active round.", None

        if match_key not in matches:
            return False, "No such match.", None

        match = matches[match_key]
        if match.status == Status.COMPLETED and not force:
            return False, "Match is already completed", match

        team_index -= 1
        from tournament import tournament

        series_length = tournament.series_length
        match.winner_idx = team_index
        match.loser_idx = 1 - team_index

        team = match.teams[team_index]
        team.score = series_length * 4
        team.series = series_length

        if match.group_id:
            self.update_gs_match(match=match)
        else:
            self.update_match(match=match)

        self.send_results(match, match_key)
        self.send_players_dashboard()
        return True, f"Given {team.name} win.", match

    def give_win_to_team(
        self, match_index: int, team_index: int, force: bool = False
    ) -> str:
        """gives the win to the team, addressed by its position in the current matches.

        thin wrapper for the /tournament win command; the API uses
        give_win_by_key so a stale index can't award the wrong match.
        """
        matches = self.list_matches()
        if not matches:
            return "No active round."

        match_ids = list(matches.keys())
        if match_index < 1 or match_index > len(match_ids):
            return f"Invalid match index. Must be between 1 and {len(match_ids)}."

        _, message, _ = self.give_win_by_key(
            match_ids[match_index - 1], team_index, force=force
        )
        return message

    def get_team(self, team_id: str) -> TeamSchema:
        """returns the full team information dict."""
        return self.registration.read().teams[team_id]

    def create_match_format(
        self,
        team1: str,
        team2: str,
        match_id: str,
        group_id: str | None = None,
        round_id: str | None = None,
    ) -> MatchSchema:
        """match format."""
        return MatchSchema(
            match_id=match_id,
            teams=[
                MatchTeamSchema(idx=0, name=team1),
                MatchTeamSchema(idx=1, name=team2),
            ],
            group_id=group_id,
            round_id=round_id,
        )