import os
import time

import bascenev1

from server.enums import Status
from server.notify import send_event
from tournament.brackets import Brackets
from tournament.schema import (
    BenchActivationSchema,
    MatchSchema,
    PendingMatchSchema,
)

BENCH_ACTIVATION_TIMEOUT = 6 * 3600


class Manager:
    def __init__(self):
        self.pending_matches: dict[str, PendingMatchSchema] = {}
        self.players = {}
        self.ready_players = {}
        self.pause_players = {}

        self.season_id: str = "0"
        self.brackets: Brackets | None = None

        self.active_match: PendingMatchSchema | None = None

        self._transitioning: bool = False

    def initialize(self, season_id: str):
        self.season_id = season_id
        if not int(season_id):
            self.brackets = None
            self.pending_matches.clear()
            self.players.clear()
            return
        self.brackets = Brackets(season_id=season_id)
        self.brackets.refresh()
        self.load_pending_matches()

    def sync(self) -> None:
        from tournament import seasons

        if self.active_match:
            return
        if seasons.active_season != self.season_id:
            self.initialize(seasons.active_season)
        elif self.brackets:
            self.load_pending_matches()

    def cancel(self) -> None:
        was_playing = self.active_match is not None
        self.active_match = None
        self.pending_matches.clear()
        self.players.clear()
        self.ready_players.clear()
        self.pause_players.clear()
        self.brackets = None
        self.season_id = "0"
        if was_playing:
            bascenev1.broadcastmessage("The tournament has been cancelled.")
            self.end_tournament_session()

    def load_pending_matches(self):
        self.pending_matches.clear()
        self.players.clear()

        matches = self.brackets.list_matches()
        for key, match in matches.items():
            if match.status == Status.PENDING:
                self.register_pending_match(key=key, match=match)

    def register_pending_match(self, key: str, match: MatchSchema):
        team1 = match.teams[0].name
        team2 = match.teams[1].name

        team1_schema = self.brackets.get_team(team1)
        team2_schema = self.brackets.get_team(team2)

        team1_accounts = [m.account_id for m in team1_schema.mains if m.account_id]
        team2_accounts = [m.account_id for m in team2_schema.mains if m.account_id]
        team1_uuids = [m.device_uuid for m in team1_schema.mains if m.account_id]
        team2_uuids = [m.device_uuid for m in team2_schema.mains if m.account_id]

        self.pending_matches[key] = PendingMatchSchema(
            key=key,
            match=match,
            uuids=team1_uuids + team2_uuids,
            players=team1_accounts + team2_accounts,
            teams={team1: list(team1_accounts), team2: list(team2_accounts)},
        )

        if key not in self.ready_players:
            self.ready_players[key] = set()
        if key not in self.pause_players:
            self.pause_players[key] = set()
        for team, accounts in ((team1, team1_accounts), (team2, team2_accounts)):
            for account_id in accounts:
                self.players[account_id] = [key, team]

        self._apply_bench_activations(key)

    # ---- bench activation ----

    def _apply_bench_activations(self, match_key: str) -> None:
        pending = self.pending_matches.get(match_key)
        if not pending:
            return
        for team_name, activation in list(pending.match.bench_activations.items()):
            if activation.state in ("PENDING", "ACCEPTED", "FORCE"):
                self._apply_bench_activation(match_key, team_name)

    def _apply_bench_activation(self, match_key: str, team_name: str) -> None:
        pending = self.pending_matches.get(match_key)
        if not pending:
            return
        activation = pending.match.bench_activations.get(team_name)
        if not activation:
            return

        team_accounts = pending.teams.get(team_name, [])
        if activation.out_account_id in team_accounts:
            team_accounts[team_accounts.index(activation.out_account_id)] = activation.in_account_id

        if activation.out_account_id in pending.players:
            idx = pending.players.index(activation.out_account_id)
            pending.players[idx] = activation.in_account_id
            if idx < len(pending.uuids):
                pending.uuids[idx] = activation.in_uuid

        self.players.pop(activation.out_account_id, None)
        self.players[activation.in_account_id] = [match_key, team_name]

    def _revert_bench_activation(self, match_key: str, team_name: str) -> None:
        pending = self.pending_matches.get(match_key)
        if not pending:
            return
        activation = pending.match.bench_activations.get(team_name)
        if not activation:
            return

        team_accounts = pending.teams.get(team_name, [])
        if activation.in_account_id in team_accounts:
            team_accounts[team_accounts.index(activation.in_account_id)] = activation.out_account_id

        if activation.in_account_id in pending.players:
            idx = pending.players.index(activation.in_account_id)
            pending.players[idx] = activation.out_account_id
            team = self.brackets.get_team(team_name)
            orig = next((m for m in team.mains if m.account_id == activation.out_account_id), None)
            if orig and idx < len(pending.uuids):
                pending.uuids[idx] = orig.device_uuid

        self.players.pop(activation.in_account_id, None)
        self.players[activation.out_account_id] = [match_key, team_name]

    def activate_bench(
        self,
        match_key: str,
        out_discord: str,
        in_discord: str,
        requested_by: str,
        reason: str = "",
    ) -> dict:
        pending = self.pending_matches.get(match_key)
        if not pending:
            return {"ok": False, "error": "no such pending match"}

        match = pending.match
        target_team = None
        for match_team in match.teams:
            if not match_team.name:
                continue
            team = self.brackets.get_team(match_team.name)
            if any(m.discord_id == out_discord for m in team.mains) and \
               any(b.discord_id == in_discord for b in team.bench):
                target_team = match_team.name
                break
        if not target_team:
            return {"ok": False, "error": "both players must be on the same team"}

        existing = match.bench_activations.get(target_team)
        if existing and existing.state == "PENDING":
            return {"ok": False, "error": "an activation is already pending"}

        team = self.brackets.get_team(target_team)
        out_member = next(m for m in team.mains if m.discord_id == out_discord)
        in_member = next(b for b in team.bench if b.discord_id == in_discord)
        if not in_member.device_uuid:
            return {"ok": False, "error": "bench member hasn't verified yet"}

        now = time.time()
        activation = BenchActivationSchema(
            team=target_team,
            out_discord_id=out_discord,
            out_account_id=out_member.account_id,
            in_discord_id=in_discord,
            in_account_id=in_member.account_id,
            in_uuid=in_member.device_uuid,
            requested_by=requested_by,
            reason=reason,
            state="PENDING",
            at=now,
            expires_at=now + BENCH_ACTIVATION_TIMEOUT,
        )
        match.bench_activations[target_team] = activation
        self._apply_bench_activation(match_key, target_team)
        self.brackets.save_bench_state(match)

        return {
            "ok": True,
            "team": target_team,
            "team_thread_id": team.thread_id,
            "out_discord_id": out_discord,
            "in_discord_id": in_discord,
            "expires_at": activation.expires_at,
        }

    def resolve_bench_activation(self, match_key: str, team_name: str, action: str) -> dict:
        pending = self.pending_matches.get(match_key)
        if not pending:
            return {"ok": False, "error": "no such pending match"}
        activation = pending.match.bench_activations.get(team_name)
        if not activation:
            return {"ok": False, "error": "no activation found"}
        if activation.state != "PENDING":
            return {"ok": False, "error": f"activation already {activation.state.lower()}"}

        if action == "confirm":
            activation.state = "ACCEPTED"
        elif action == "decline":
            activation.state = "DECLINED"
            self._revert_bench_activation(match_key, team_name)
        else:
            return {"ok": False, "error": "unknown action"}

        self.brackets.save_bench_state(pending.match)
        return {"ok": True, "state": activation.state}

    def force_bench_activation(
        self,
        match_key: str,
        out_discord: str,
        in_discord: str,
        requested_by: str,
        reason: str = "",
    ) -> dict:
        pending = self.pending_matches.get(match_key)
        if not pending:
            return {"ok": False, "error": "no such pending match"}

        match = pending.match
        target_team = None
        for match_team in match.teams:
            if not match_team.name:
                continue
            team = self.brackets.get_team(match_team.name)
            if any(m.discord_id == out_discord for m in team.mains) and \
               any(b.discord_id == in_discord for b in team.bench):
                target_team = match_team.name
                break
        if not target_team:
            return {"ok": False, "error": "both players must be on the same team"}

        team = self.brackets.get_team(target_team)
        out_member = next(m for m in team.mains if m.discord_id == out_discord)
        in_member = next(b for b in team.bench if b.discord_id == in_discord)
        if not in_member.device_uuid:
            return {"ok": False, "error": "bench member hasn't verified yet"}

        existing = match.bench_activations.get(target_team)
        if existing and existing.state in ("PENDING", "ACCEPTED"):
            self._revert_bench_activation(match_key, target_team)

        now = time.time()
        activation = BenchActivationSchema(
            team=target_team,
            out_discord_id=out_discord,
            out_account_id=out_member.account_id,
            in_discord_id=in_discord,
            in_account_id=in_member.account_id,
            in_uuid=in_member.device_uuid,
            requested_by=requested_by,
            reason=reason,
            state="FORCE",
            at=now,
            expires_at=0,
        )
        match.bench_activations[target_team] = activation
        self._apply_bench_activation(match_key, target_team)
        self.brackets.save_bench_state(match)
        return {
            "ok": True,
            "team": target_team,
            "team_thread_id": team.thread_id,
            "out_discord_id": out_discord,
            "in_discord_id": in_discord,
        }

    def expire_bench_activations(self) -> list[dict]:
        expired = []
        now = time.time()
        for match_key, pending in list(self.pending_matches.items()):
            for team_name, activation in list(pending.match.bench_activations.items()):
                if activation.state == "PENDING" and activation.expires_at <= now:
                    activation.state = "ACCEPTED"
                    self.brackets.save_bench_state(pending.match)
                    expired.append({
                        "match_key": match_key,
                        "team": team_name,
                        "out_discord_id": activation.out_discord_id,
                        "in_discord_id": activation.in_discord_id,
                    })
        return expired

    # ---- ready ----

    def _readiness_snapshot(self, match_id: str) -> dict:
        pending = self.pending_matches[match_id]
        ready = self.ready_players.get(match_id, set())

        teams_info: dict[str, dict] = {}
        for match_team in pending.match.teams:
            team_name = match_team.name
            if not team_name:
                continue
            team = self.brackets.get_team(team_name)
            active_accounts = pending.teams.get(team_name, [])
            members = []
            for account_id in active_accounts:
                main = next((m for m in team.mains if m.account_id == account_id), None)
                if main:
                    members.append({
                        "discord_id": main.discord_id,
                        "account_id": account_id,
                        "ready": account_id in ready,
                        "is_bench": False,
                    })
                    continue
                bench = next((b for b in team.bench if b.account_id == account_id), None)
                if bench:
                    members.append({
                        "discord_id": bench.discord_id,
                        "account_id": account_id,
                        "ready": account_id in ready,
                        "is_bench": True,
                    })
            size = len(team.mains)
            teams_info[team_name] = {
                "size": size,
                "min": max(1, size - 1),
                "ready": sum(1 for m in members if m["ready"]),
                "members": members,
            }

        min_total = sum(t["min"] for t in teams_info.values())
        total_ready = sum(t["ready"] for t in teams_info.values())
        total_players = sum(t["size"] for t in teams_info.values())
        return {
            "teams": teams_info,
            "min_total": min_total,
            "total_ready": total_ready,
            "total_players": total_players,
            "min_met": all(t["ready"] >= t["min"] for t in teams_info.values()),
            "floor_met": all(t["ready"] >= 1 for t in teams_info.values()),
        }

    def handle_player_ready(self, account_id: str, uuid: str) -> dict:
        self.sync()
        match_id = self.players.get(account_id, [None, None])[0]
        if not match_id:
            return {
                "status": "error",
                "message": "You are not registered for any matches.",
            }

        if self.active_match:
            return {"status": "error", "message": "A match is already active."}

        if account_id in self.ready_players.get(match_id, set()):
            return {"status": "error", "message": "You are already ready."}

        match = self.pending_matches.get(match_id)
        if match is None:
            return {"status": "error", "message": "This match is no longer pending."}

        if uuid not in match.uuids:
            return {
                "status": "error",
                "message": "Your device uuid seems to have changed, contact the server admins.",
            }

        self.ready_players[match_id].add(account_id)
        snapshot = self._readiness_snapshot(match_id)

        if snapshot["min_met"]:
            self.active_match = match
            self.pending_matches.pop(match_id, None)
            self.ready_players.pop(match_id, None)
            self._mark_bench_started(match_id)
            send_event(
                "ready_panel",
                season_id=self.season_id,
                match_key=match_id,
                state="started",
            )
            with bascenev1.ContextRef.empty():
                bascenev1.apptimer(5.0, self.start_tournament_session)
            return {"status": "success", "message": "Match starting.", "start": True}

        send_event(
            "ready_panel",
            season_id=self.season_id,
            match_key=match_id,
            state="waiting",
            snapshot=snapshot,
        )
        return {
            "status": "success",
            "message": f"Ready: {snapshot['total_ready']}/{snapshot['min_total']}.",
        }

    def _mark_bench_started(self, match_id: str) -> None:
        """any pending activation that never got a response is accepted at start."""
        pending = self.pending_matches.get(match_id) or self.active_match
        if not pending:
            return
        for team_name, activation in pending.match.bench_activations.items():
            if activation.state == "PENDING":
                activation.state = "ACCEPTED"
                self.brackets.save_bench_state(pending.match)

    def handle_player_pause(self, account_id: str) -> dict:
        if self._transitioning:
            return {"status": "error", "message": "The match is ending."}
        if not self.active_match:
            return {"status": "error", "message": "There is no active match."}
        if account_id not in self.active_match.players:
            return {"status": "error", "message": "You are not a member of the active match."}

        captains = set()
        for team in self.active_match.match.teams:
            captain_account = self._captain_account_of(team.name)
            if captain_account:
                captains.add(captain_account)

        if account_id not in captains:
            return {"status": "error", "message": "Only the team captain can request a pause."}

        match_id = self.active_match.key
        self.pause_players.setdefault(match_id, set())
        if account_id in self.pause_players[match_id]:
            return {"status": "error", "message": "You are already marked for match pause."}

        result = {"status": "success", "message": "You have been marked for match pause."}
        self.pause_players[match_id].add(account_id)

        if captains and captains.issubset(self.pause_players[match_id]):
            self.save_score()
            self.end_tournament_session()
            result["pause"] = True
        return result

    def _captain_account_of(self, team_name: str) -> str | None:
        team_schema = self.brackets.get_team(team_name)
        if not team_schema:
            return None
        for member in team_schema.mains:
            if member.discord_id == team_schema.captain and member.account_id:
                return member.account_id
        return None

    def save_score(self) -> None:
        if not self.active_match:
            return
        if self.active_match.match.group_id:
            gs = self.brackets.read_gs()
            gs.groups[self.active_match.match.group_id].rounds[self.active_match.match.round_id].matches[self.active_match.match.match_id] = self.active_match.match
            self.brackets.commit_gs(gs)
        else:
            self.brackets.save_match(self.active_match.match)

    def handle_player_leave(self, account_id: str) -> None:
        match_id = self.players.get(account_id, [None, None])[0]
        if not match_id:
            return
        if self.active_match:
            return
        if account_id in self.ready_players.get(match_id, set()):
            self.ready_players[match_id].discard(account_id)
            if match_id in self.pending_matches:
                snapshot = self._readiness_snapshot(match_id)
                send_event(
                    "ready_panel",
                    season_id=self.season_id,
                    match_key=match_id,
                    state="waiting",
                    snapshot=snapshot,
                )

    def force_start_match(self, match_key: str) -> dict:
        match_key = str(match_key)
        if self.active_match:
            return {"started": False, "error": "A match is already active."}
        match = self.pending_matches.get(match_key)
        if match is None:
            return {"started": False, "error": "No such pending match."}
        if not self._readiness_snapshot(match_key)["floor_met"]:
            return {"started": False, "error": "Need at least one player per team."}

        self.active_match = match
        self.pending_matches.pop(match_key, None)
        self.ready_players.pop(match_key, None)
        self.pause_players.pop(match_key, None)
        self._mark_bench_started(match_key)
        send_event(
            "ready_panel",
            season_id=self.season_id,
            match_key=match_key,
            state="started",
        )
        with bascenev1.ContextRef.empty():
            bascenev1.apptimer(5.0, self.start_tournament_session)
        return {"started": True}

    def conclude_active_match(self) -> None:
        if not self.active_match:
            return

        self.save_score()

        send_event(
            "match_ended",
            season_id=self.season_id,
            key=self.active_match.key,
        )

        if self.active_match.match.group_id:
            self.brackets.update_gs_match(match=self.active_match.match)
        else:
            self.brackets.update_match(match=self.active_match.match)

        self.brackets.send_results(self.active_match.match, self.active_match.key)
        self.brackets.send_players_dashboard()
        self.end_tournament_session()

    def start_tournament_session(self) -> None:
        os.environ["TOURNAMENT_MATCH"] = self.season_id
        self.brackets.announce_match_start(self.active_match.match)
        from .activity import TournamentTransitionActivity

        session = bascenev1.get_foreground_host_session()
        with session.context:
            session.setactivity(bascenev1.newactivity(TournamentTransitionActivity))

    def end_tournament_session(self) -> None:
        if self._transitioning:
            return
        self._transitioning = True
        bascenev1.broadcastmessage("Server will return to the lobby in 30 seconds.")
        with bascenev1.ContextRef.empty():
            bascenev1.apptimer(30.0, self._return_to_lobby)

    def _return_to_lobby(self) -> None:
        from tournament.session import TournamentSession

        self._transitioning = False
        os.environ.pop("TOURNAMENT_MATCH", None)

        if self.active_match:
            match_id = self.active_match.key

            for account_id in [
                aid for aid, (mid, _) in self.players.items() if mid == match_id
            ]:
                del self.players[account_id]

            self.pending_matches.pop(match_id, None)
            self.ready_players.pop(match_id, None)
            self.pause_players.pop(match_id, None)

        self.active_match = None

        session = bascenev1.get_foreground_host_session()
        if not isinstance(session, TournamentSession):
            return

        from .activity import TeamSessionTransitionActivity
        with session.context:
            session.setactivity(bascenev1.newactivity(TeamSessionTransitionActivity))


manager = Manager()