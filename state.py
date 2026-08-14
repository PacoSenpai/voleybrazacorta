from datetime import datetime, timezone
import time

from domain import elapsed_seconds


def _iso(timestamp):
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _team(connection, team_id):
    if not team_id:
        return None
    row = connection.execute("SELECT id, name, color, seed FROM teams WHERE id = ?", (team_id,)).fetchone()
    if not row:
        return None
    return dict(row)


def _sets(connection, match_id):
    rows = connection.execute(
        """SELECT set_number, score_a, score_b, winner_team_id, status
           FROM sets WHERE match_id = ? ORDER BY set_number""",
        (match_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _points(connection, match_id, limit=None):
    query = """SELECT point_events.event_id, point_events.set_number, point_events.team_id,
                      point_events.score_a, point_events.score_b, point_events.elapsed_seconds,
                      point_events.created_at
               FROM point_events
               WHERE point_events.match_id = ? AND point_events.undone = 0
               ORDER BY point_events.created_at DESC, point_events.rowid DESC"""
    params = [match_id]
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    rows = connection.execute(query, params).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["team"] = _team(connection, item.pop("team_id"))
        item["created_at"] = _iso(item["created_at"])
        result.append(item)
    return result


def match_payload(connection, match, include_points=False):
    item = {
        "id": match["id"],
        "stage_id": match["stage_id"],
        "stage_name": match["stage_name"] if "stage_name" in match.keys() else None,
        "bracket_index": match["bracket_index"],
        "team_a": _team(connection, match["team_a_id"]),
        "team_b": _team(connection, match["team_b_id"]),
        "winner_team_id": match["winner_team_id"],
        "status": match["status"],
        "best_of": match["best_of"],
        "points_per_set": match["points_per_set"],
        "win_by": match["win_by"],
        "current_set": match["current_set"],
        "sets_a": match["sets_a"],
        "sets_b": match["sets_b"],
        "timer_elapsed": elapsed_seconds(match),
        "timer_started_at": match["timer_started_at"],
        "finished_at": _iso(match["finished_at"]),
        "sets": _sets(connection, match["id"]),
    }
    if include_points:
        item["points"] = _points(connection, match["id"])
    return item


def tournament_state(connection, include_admin=False):
    tournament = connection.execute(
        "SELECT * FROM tournaments WHERE status != 'archived' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not tournament:
        return {
            "tournament": None,
            "stages": [],
            "matches": [],
            "active_match": None,
            "next_match": None,
            "last_finished": None,
            "server_time": time.time(),
        }

    rows = connection.execute(
        """SELECT matches.*, stages.name AS stage_name, stages.stage_index
           FROM matches JOIN stages ON stages.id = matches.stage_id
           WHERE matches.tournament_id = ?
           ORDER BY stages.stage_index, matches.bracket_index""",
        (tournament["id"],),
    ).fetchall()
    flat = [match_payload(connection, row, include_points=False) for row in rows]
    stages = []
    stage_rows = connection.execute(
        """SELECT * FROM stages WHERE tournament_id = ? ORDER BY stage_index""",
        (tournament["id"],),
    ).fetchall()
    for stage in stage_rows:
        stages.append(
            {
                "id": stage["id"],
                "name": stage["name"],
                "stage_index": stage["stage_index"],
                "best_of": stage["best_of"],
                "points_per_set": stage["points_per_set"],
                "win_by": stage["win_by"],
                "matches": [item for item in flat if item["stage_id"] == stage["id"]],
            }
        )

    active_row = next((row for row in rows if row["status"] in ("live", "paused")), None)
    next_row = next(
        (
            row
            for row in rows
            if row["status"] == "scheduled" and row["team_a_id"] and row["team_b_id"]
        ),
        None,
    )
    finished_rows = [row for row in rows if row["status"] == "finished"]
    last_row = max(finished_rows, key=lambda row: row["finished_at"] or 0, default=None)
    state = {
        "tournament": {
            "id": tournament["id"],
            "name": tournament["name"],
            "status": tournament["status"],
            "updated_at": _iso(tournament["updated_at"]),
        },
        "stages": stages,
        "matches": flat,
        "active_match": match_payload(connection, active_row, include_points=True)
        if active_row
        else None,
        "next_match": match_payload(connection, next_row, include_points=False) if next_row else None,
        "last_finished": match_payload(connection, last_row, include_points=False) if last_row else None,
        "server_time": time.time(),
    }
    if include_admin:
        state["active_match"] = (
            match_payload(connection, active_row, include_points=True)
            if active_row
            else state["next_match"]
        )
    return state
