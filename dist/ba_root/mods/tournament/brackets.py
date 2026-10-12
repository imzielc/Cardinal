"""storage for brackets"""

import json
import random
from pathlib import Path

from server.enums import Status, TournamentMode, TournamentStage
from server.storage import Storage
from tournament import stages
from tournament.storage import SEASONS_DIR
from tournament.registration import Registration
from tournament.graphics import runner
from tournament.schema import BracketsMetaSchema, MatchSchema, MatchTeamSchema, GroupStageSchema, GroupSchema, RoundSchema, TeamSchema

class Brackets(Storage):
    """generates brackets and handles the rounds."""

    def __init__(self, season_id: str):
        super().__init__("brackets.json", SEASONS_DIR / season_id)
        self.season_id = season_id
        # the rounds of a stage are files in folders: rounds/<bracket>/<round>.json, only the group
        # stage is one file, rounds/group-stage.json
        self.rounds_dir = self.directory / "rounds"
        self.group_stage_path = self.rounds_dir / "group-stage.json"
        self.rounds_dir.mkdir(parents=True, exist_ok=True)
        self.bootstrap()

    def bootstrap(self):
        """creates the file and setups up method"""
        self.registration = Registration(self.season_id)
        if not self.path.exists():
            self.commit_meta(BracketsMetaSchema())

    def generate_group_stage(self, teams: list) -> None | bool:
        """generates the group stage brackets."""
        # TODO: make it more dynamically syncing with real logic.
        # we assume that the total number of teams is even.
        teams_count = len(teams)
        groups_count = (teams_count & -teams_count)
        if teams_count < groups_count * 5:
            groups_count = groups_count // 2

        # ah we dont want the groups to be only two, round-robin will make it longer otherwise or the teams count is less than 4
        assert groups_count >= 4
        if groups_count == teams_count:
            # the number is power of 2 value
            # we go straight to the main stage
            self.generate_main_stage(teams=teams)
            return

        # mhm.. groups are needed now.
        groups = {}
        main_stage_capacity = 1 << (teams_count.bit_length() - 1)
        teams_per_group = teams_count // groups_count

        # calculates the number of winning teams needed out of each group
        winning_teams_per_group = main_stage_capacity // groups_count
        if winning_teams_per_group * 2 - 1 > teams_per_group:
            winning_teams_per_group = winning_teams_per_group // 2

        for index in range(groups_count):
            group_id = str(index + 1)
            groups[group_id] = GroupSchema(
                rounds = self.generate_round_robin(
                    teams[index * teams_per_group : (index + 1) * teams_per_group],
                    count=teams_per_group,
                    group_id=group_id,
                ),
                standings = [],
                standings_sorted=[]
            )
            # fill up the standings.
            self.recalculate_group_standings(groups[group_id])

        database = GroupStageSchema(
            groups = groups,
            winning_teams_per_group=winning_teams_per_group,
            status=Status.IN_PROGRESS
        )

        self.commit_gs(data=database)

        # also update it in brackets.json that groupstage is active;
        brackets = self.read_meta()
        brackets.mode = TournamentMode.SINGLE
        brackets.started = True
        brackets.status = Status.IN_PROGRESS
        brackets.active_rounds = ["group-stage"]
        brackets.total_rounds += 1
        self.commit_meta(brackets)

        return True

    def generate_round_robin(self, teams: list, count: int, group_id: str) -> dict[str, RoundSchema]:
        """generates rounds robin for teams."""
        rounds = {}

        # if the teams count is odd.. we can add an empty team and then remove it while making rounds.
        if count % 2 != 0:
            teams = teams + [None]
            count += 1

        for round in range(1, count):
            round_matches = {}
            # for each round.
            for i in range(count // 2):
                first = teams[i]  # first in sense of next front.
                last = teams[count - 1 - i]  # last in sense of previous back.

                match = self.create_match_format(
                    team1=first,
                    team2=last,
                    match_id=str(i + 1),
                    group_id=group_id,
                    round_id=str(round),
                )

                # if one of them is None, we give them BYEs.
                if first is None or last is None:
                    match.status = Status.COMPLETED
                    match.winner_idx = 0 if last is None else 1

                round_matches[match.match_id] = match

            rounds[str(round)] = RoundSchema(matches = round_matches, status = Status.IN_PROGRESS if round == 1 else Status.PENDING)

            # shuffle it so the teams dont get matched up with the same team twice.
            teams = [teams[0]] + [teams[-1]] + teams[1:-1]
        return rounds

    def update_gs_match(
        self, match: MatchSchema
    ):
        """updates the match of the group round."""
        gs = self.read_gs()
        group = gs.groups[match.group_id]
        round_id = match.round_id
        round = group.rounds[round_id]
        round.matches[match.match_id] = match

        match.status = Status.COMPLETED

        # all the matches of same round across all the groups
        all_groups_round_completed = all(
            m.status == Status.COMPLETED
            for g in gs.groups.values()
            for m in g.rounds[round_id].matches.values()
        )

        # lets check if the round of all groups is completed.
        if all_groups_round_completed:
            # all matches are completed.
            next_round_id = str(int(round_id) + 1)

            for g in gs.groups.values():
                if round_id in g.rounds:
                    g.rounds[round_id].status = Status.COMPLETED

                if next_round_id in g.rounds:
                    # if a next round exists, turn it on.
                    g.rounds[next_round_id].status = Status.IN_PROGRESS

        # recalculate the standings
        self.recalculate_group_standings(group=group)

        # check if the whole groupstage is completed.
        if all(
            round.status == Status.COMPLETED
            for g in gs.groups.values()
            for round in g.rounds.values()
        ):
            # groupstage is completed.
            gs.status = Status.COMPLETED

            # commit now because main stage will check the status of the groupstage.
            self.commit_gs(gs)
            self.send_groupstage_brackets()
            self.send_group_stage_standings()
            # load the main stage.
            self.generate_main_stage()
            return

        self.commit_gs(gs)
        self.send_groupstage_brackets()
        self.send_group_stage_standings()

    # ---- the knockout stage (single and double elimination) ----

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
        brackets.mode = mode
        brackets.started = True
        brackets.status = Status.IN_PROGRESS
        brackets.active_rounds = stages.active_rounds(stage)
        brackets.total_rounds += sum(len(rounds) for rounds in stage.values())
        self.commit_meta(brackets)
        self.send_stage_brackets()

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
        brackets.active_rounds = stages.active_rounds(stage)
        self.commit_meta(brackets)

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
        # the played values (scores, winner) are the ones to keep, the slots stay as they are.
        for slot, played in zip(stored.teams, match.teams):
            slot.score, slot.series = played.score, played.series
        stored.winner_idx, stored.loser_idx = match.winner_idx, match.loser_idx
        stored.last_scores = match.last_scores
        stored.status = Status.COMPLETED
        match.status = Status.COMPLETED

        stages.settle(stage, brackets.mode)
        self.commit_stage(stage, before)

        completed, placements = stages.outcome(stage, brackets.mode)
        brackets.active_rounds = stages.active_rounds(stage)
        if completed:
            brackets.status = Status.COMPLETED
            brackets.placements = placements
        self.commit_meta(brackets)
        self.send_stage_brackets()

        if completed:
            self.announce_tournament_completion()

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
        """recalculates the group standings based on:
        1. points
        2. diff
        3. rounds won."""
        stats = {}
        for round in group.rounds.values():
            for match in round.matches.values():
                # add to stats
                for team in match.teams:
                    team_name = team.name
                    if team_name and team_name not in stats:
                        stats[team_name] = {
                            "id": team_name, # the team name is the id itself for uniqueness.
                            "wins": 0,
                            "loses": 0,
                            "points": 0,
                            "diff": 0,
                            "rounds_won": 0,
                            "rounds_lost": 0,
                        }

        # we put and calculate stats of the completed matches only.
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

        # diff is tiebreaker.
        for team in stats.values():
            team["diff"] = team["rounds_won"] - team["rounds_lost"]
            team["points"] = team["wins"] * 3 - team["loses"]

        sorted_teams = sorted(
            stats.values(),
            key=lambda x: (x["points"], x["diff"], x["rounds_won"]),
            reverse=True,
        )

        group.standings = [team["id"] for team in sorted_teams]
        # this will come in help for showing the stats on leaderboard.
        group.standings_sorted = sorted_teams

    def send_group_stage_standings(self) -> None:
        """sends the group stage standings to discord webhook."""
        data = {
            "type": "group-standings",
            "season_id": self.season_id,
        }

        runner.run(data=data)

    def make_pairings(
        self, teams: dict[str, GroupSchema] | list, winning_teams_per_group: int = 0
    ) -> list[tuple[str, str]]:
        "pairs the teams for the first round of the main-stage."
        pairings = []
        if isinstance(teams, list):
            # there was no group stage before main-stage.
            shuffled_teams = teams.copy()
            random.shuffle(shuffled_teams)

            # make the pairings.
            for i in range(0, len(teams), 2):
                pairings.append((shuffled_teams[i], shuffled_teams[i + 1]))

        else:
            # there was a group stage.. so the teams dict is actually the dict of groups.
            # groups = teams
            groups_keys = list(teams.keys())
            groups_count = len(groups_keys)
            offset = groups_count // 2

            for i in range(groups_count):
                front_group = groups_keys[i]
                back_group = groups_keys[(i + offset) % groups_count]

                if winning_teams_per_group == 1:
                    if i < offset:
                        pairings.append(
                            (
                                teams[front_group].standings[0],
                                teams[back_group].standings[0],
                            )
                        )
                    continue

                for index in range(winning_teams_per_group // 2):
                    team1 = teams[front_group].standings[index]
                    team2 = teams[back_group].standings[
                        winning_teams_per_group - 1 - index
                    ]
                    pairings.append((team1, team2))
        return pairings

    def generate_main_stage(self, teams: list = []):
        """generates the main stage (single elimination)"""
        if teams:
            # there was no group stage before us.
            pairings = self.make_pairings(teams)
        else:
            # there was a group stage before us.
            # to make the match to be fair, we will shuffle them first to last; like we did for group-stage matches
            # but only once per team.
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

        # TODO: announce completion with winners.

    def announce_match_start(self, team1: str, team2: str) -> None:
        """announces the match start in discord."""
        from tournament import tournament
        data = {
            "type": "match-announcement",
            "season_id": self.season_id,
            "participant_role_id": tournament.get_season(self.season_id).participant_role_id,
            "team1": team1,
            "team2": team2,
        }
        runner.run(data=data)

    def send_stage_brackets(self) -> None:
        """sends the brackets of the knockout stage, the image depends on the mode."""
        if self.read_meta().mode == TournamentMode.DOUBLE:
            self.send_double_elimination_brackets()
        else:
            self.send_mainstage_brackets()

    def send_mainstage_brackets(self) -> None:
        """sends the mainstage brackets."""
        data = {
            "type": "main-stage",
            "season_id": self.season_id,
        }
        runner.run(data=data)

    def send_double_elimination_brackets(self) -> None:
        """sends the double elimination bracket."""
        data = {
            "type": "double-elimination",
            "season_id": self.season_id,
        }
        runner.run(data=data)

    def send_groupstage_brackets(self) -> None:
        """ sends groupstage brackets with webhooks."""
        data = {
            "type": "group-stage",
            "season_id": self.season_id,
        }
        runner.run(data=data)

    def send_results(
        self,
        match: MatchSchema,
        key: str,
    ) -> None:
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
        data = {
            "type": "results",
            "details": details,
            "season_id": self.season_id,
        }
        runner.run(data=data)

    def send_players_dashboard(self) -> None:
        """sends the players dashboard."""
        data = {
            "type": "player-standings",
            "season_id": self.season_id,
        }
        runner.run(data=data)

    def read_meta(self) -> BracketsMetaSchema:
        return BracketsMetaSchema.from_dict(self.read())

    def commit_meta(self, data: BracketsMetaSchema) -> None:
        self.commit(data.to_dict())

    def read_gs(self) -> GroupStageSchema:
        return GroupStageSchema.from_dict(self.read(self.group_stage_path))

    def commit_gs(self, data: GroupStageSchema) -> None:
        self.commit(data.to_dict(), self.group_stage_path)

    def list_matches(self) -> dict[str, MatchSchema]:
        """ returns the list of all the matches in the active rounds."""
        brackets = self.read_meta()
        if not brackets.active_rounds:
            return {} # no active round.

        matches = {}

        # if the round is groupstage;
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

        return matches

    def give_win_to_team(self, match_index: int, team_index: int, force: bool = False) -> str:
        """gives the win to the team."""
        if team_index not in (1, 2):
            return "Team index must be either 1 or 2."
        team_index -= 1

        matches = self.list_matches()
        if not matches:
            return "No active round."

        match_ids = list(matches.keys())
        if match_index < 1 or match_index > len(match_ids):
            return f"Invalid match index. Must be between 1 and {len(match_ids)}."

        match_id = match_ids[match_index - 1]

        if match_id not in matches:
            return "No such match."

        match = matches[match_id]
        if match.status != Status.COMPLETED or force:
            from tournament import tournament
            series_length = tournament.series_length
            match.winner_idx = team_index
            match.loser_idx = 1 - team_index

            team = match.teams[team_index]
            team.score = series_length * 4
            team.series = series_length
            
            if match.group_id:
                # its a group stage match.
                self.update_gs_match(
                    match = match,
                )
            else:
                # its a knockout stage match.
                self.update_match(match=match)

            # and now we can send the results to discord.
            self.send_results(match, match_id)
            self.send_players_dashboard()
            return f"Given {team.name} win."
        return "Match is already completed"

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
            match_id = match_id,
            teams = [
                MatchTeamSchema(idx=0, name=team1),
                MatchTeamSchema(idx=1, name=team2)
            ],
            group_id = group_id,
            round_id=round_id
        )