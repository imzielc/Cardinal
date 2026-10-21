"""schema -> JSON dicts, with redaction for anonymous requests."""


def _enum(value):
    return value.value if hasattr(value, "value") else value


def member(m, auth: dict | None) -> dict:
    data = m.to_dict() if hasattr(m, "to_dict") else dict(m)
    if auth is not None:
        return data
    if data.get("device_uuid"):
        data["device_uuid"] = "***"
    data.pop("account_id", None)
    return data


def team(t, auth: dict | None) -> dict:
    return {
        "id": t.id,
        "captain": t.captain if auth is not None else "",
        "status": _enum(t.status),
        "members": [member(m, auth) for m in t.members],
        "role_id": t.role_id,
        "thread_id": t.thread_id,
        "expires_at": t.expires_at,
        "reminders_sent": t.reminders_sent,
    }


def season(s) -> dict:
    return {
        "series": _enum(s.series),
        "type": _enum(s.type),
        "mode": _enum(s.mode),
        "stage": _enum(s.stage),
        "created_at": s.created_at,
        "participant_role_id": s.participant_role_id,
    }


def match(m) -> dict:
    return {
        "match_id": m.match_id,
        "round_id": m.round_id,
        "group_id": m.group_id,
        "bracket": m.bracket,
        "label": m.label,
        "status": _enum(m.status),
        "winner_idx": m.winner_idx,
        "loser_idx": m.loser_idx,
        "is_bye": m.is_bye,
        "last_scores": list(m.last_scores),
        "teams": [
            {
                "idx": t.idx,
                "name": t.name,
                "score": t.score,
                "series": t.series,
                "bye": t.bye,
            }
            for t in m.teams
        ],
    }


def round_(r) -> dict:
    return {
        "number": r.number,
        "status": _enum(r.status),
        "matches": {mid: match(m) for mid, m in r.matches.items()},
    }


def group(g) -> dict:
    return {
        "standings": list(g.standings),
        "standings_sorted": list(g.standings_sorted),
        "rounds": {rid: round_(rd) for rid, rd in g.rounds.items()},
        "role_id": g.role_id,
    }


def placement(p: dict) -> dict:
    return {"rank": p.get("rank"), "team": p.get("team")}


def waitlist_entry(e) -> dict:
    return {
        "captain_discord_id": e.captain_discord_id,
        "team_name": e.team_name,
        "invited_member_ids": list(e.invited_member_ids),
        "added_at": e.added_at,
    }