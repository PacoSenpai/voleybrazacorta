import json
import math
import time
import uuid

from db import transaction


COLORS = [
    "#F2B544",
    "#D66B4F",
    "#90B7A7",
    "#6E9FC1",
    "#D5A0C5",
    "#D3B48C",
    "#7D9A87",
    "#C47C52",
]


class DomainError(Exception):
    """A user-correctable domain error."""


def now_seconds():
    return time.time()


def next_power_of_two(value):
    return 2 if value <= 2 else 2 ** math.ceil(math.log2(value))


def stage_names(capacity):
    names = {
        2: ["Final"],
        4: ["Semifinal", "Final"],
        8: ["Cuartos", "Semifinal", "Final"],
        16: ["Octavos", "Cuartos", "Semifinal", "Final"],
    }
    if capacity in names:
        return names[capacity]

    rounds = int(math.log2(capacity))
    return [f"Ronda {index + 1}" for index in range(rounds - 1)] + ["Final"]


def stage_defaults(name):
    return {
        "name": name,
        "best_of": 3,
        "points_per_set": 21 if name.lower() == "final" else 15,
        "win_by": 2,
    }


def build_stage_config(team_count, submitted=None):
    capacity = next_power_of_two(team_count)
    defaults = [stage_defaults(name) for name in stage_names(capacity)]
    submitted = submitted or []
    for index, item in enumerate(submitted[: len(defaults)]):
        if not isinstance(item, dict):
            continue
        default = defaults[index]
        default["name"] = str(item.get("name") or default["name"]).strip()[:40]
        default["best_of"] = _positive_int(item.get("best_of"), default["best_of"], minimum=1)
        if default["best_of"] % 2 == 0:
            default["best_of"] -= 1
        default["points_per_set"] = _positive_int(
            item.get("points_per_set"), default["points_per_set"], minimum=1
        )
        default["win_by"] = _positive_int(item.get("win_by"), default["win_by"], minimum=1)
    return capacity, defaults


def _positive_int(value, fallback, minimum=1):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, parsed)


def sets_needed(best_of):
    return best_of // 2 + 1


def is_set_won(score_a, score_b, target, win_by=2):
    high = max(score_a, score_b)
    low = min(score_a, score_b)
    return high >= target and high - low >= win_by


def elapsed_seconds(match, current_time=None):
    current_time = now_seconds() if current_time is None else current_time
    elapsed = int(match["timer_elapsed"] or 0)
    if match["status"] == "live" and match["timer_started_at"]:
        elapsed += max(0, int(current_time - match["timer_started_at"]))
    return elapsed


def _ensure_set(connection, match_id, set_number):
    connection.execute(
        "INSERT OR IGNORE INTO sets(match_id, set_number) VALUES (?, ?)",
        (match_id, set_number),
    )


def _match_snapshot(connection, match_id):
    match = connection.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    sets = connection.execute(
        "SELECT set_number, score_a, score_b, winner_team_id, status "
        "FROM sets WHERE match_id = ? ORDER BY set_number",
        (match_id,),
    ).fetchall()
    return json.dumps(
        {
            "match": {
                "status": match["status"],
                "winner_team_id": match["winner_team_id"],
                "current_set": match["current_set"],
                "sets_a": match["sets_a"],
                "sets_b": match["sets_b"],
                "timer_elapsed": match["timer_elapsed"],
                "timer_started_at": match["timer_started_at"],
                "finished_at": match["finished_at"],
            },
            "sets": [dict(item) for item in sets],
        }
    )


def _restore_snapshot(connection, match_id, snapshot):
    state = json.loads(snapshot)
    match = state["match"]
    connection.execute(
        """UPDATE matches SET status = ?, winner_team_id = ?, current_set = ?,
           sets_a = ?, sets_b = ?, timer_elapsed = ?, timer_started_at = ?,
           finished_at = ? WHERE id = ?""",
        (
            match["status"],
            match["winner_team_id"],
            match["current_set"],
            match["sets_a"],
            match["sets_b"],
            match["timer_elapsed"],
            match["timer_started_at"],
            match["finished_at"],
            match_id,
        ),
    )
    connection.execute("DELETE FROM sets WHERE match_id = ?", (match_id,))
    for item in state["sets"]:
        connection.execute(
            """INSERT INTO sets(match_id, set_number, score_a, score_b,
               winner_team_id, status) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                match_id,
                item["set_number"],
                item["score_a"],
                item["score_b"],
                item["winner_team_id"],
                item["status"],
            ),
        )


def _terminal(status):
    return status in ("finished", "bye", "void")


def refresh_bracket(connection, tournament_id):
    """Propagate winners and automatic passes through the generated bracket."""
    matches = connection.execute(
        "SELECT * FROM matches WHERE tournament_id = ? ORDER BY stage_id, bracket_index",
        (tournament_id,),
    ).fetchall()

    for match in matches:
        if not match["source_a_match_id"] and not match["source_b_match_id"]:
            if match["team_a_id"] and match["team_b_id"]:
                if match["status"] in ("void", "bye"):
                    connection.execute(
                        "UPDATE matches SET status = 'scheduled', winner_team_id = NULL WHERE id = ?",
                        (match["id"],),
                    )
                _ensure_set(connection, match["id"], 1)
            elif match["team_a_id"] or match["team_b_id"]:
                if match["status"] in ("scheduled", "void", "bye"):
                    winner = match["team_a_id"] or match["team_b_id"]
                    connection.execute(
                        "UPDATE matches SET status = 'bye', winner_team_id = ? WHERE id = ?",
                        (winner, match["id"]),
                    )
            elif match["status"] in ("scheduled", "bye"):
                connection.execute(
                    "UPDATE matches SET status = 'void', winner_team_id = NULL WHERE id = ?",
                    (match["id"],),
                )
            continue

        source_a = connection.execute(
            "SELECT status, winner_team_id FROM matches WHERE id = ?",
            (match["source_a_match_id"],),
        ).fetchone()
        source_b = connection.execute(
            "SELECT status, winner_team_id FROM matches WHERE id = ?",
            (match["source_b_match_id"],),
        ).fetchone()
        if not source_a or not source_b:
            continue

        if not (_terminal(source_a["status"]) and _terminal(source_b["status"])):
            if match["status"] not in ("live", "paused", "finished"):
                connection.execute(
                    """UPDATE matches SET team_a_id = ?, team_b_id = ?, winner_team_id = NULL,
                       status = 'scheduled' WHERE id = ?""",
                    (
                        source_a["winner_team_id"] if _terminal(source_a["status"]) else None,
                        source_b["winner_team_id"] if _terminal(source_b["status"]) else None,
                        match["id"],
                    ),
                )
            continue

        winner_a = source_a["winner_team_id"]
        winner_b = source_b["winner_team_id"]
        if match["status"] in ("live", "paused"):
            continue
        if match["status"] == "finished" and match["winner_team_id"]:
            continue

        if winner_a and winner_b:
            connection.execute(
                """UPDATE matches SET team_a_id = ?, team_b_id = ?, winner_team_id = NULL,
                   status = 'scheduled' WHERE id = ?""",
                (winner_a, winner_b, match["id"]),
            )
            _ensure_set(connection, match["id"], 1)
        elif winner_a or winner_b:
            connection.execute(
                """UPDATE matches SET team_a_id = ?, team_b_id = ?, winner_team_id = ?,
                   status = 'bye' WHERE id = ?""",
                (winner_a, winner_b, winner_a or winner_b, match["id"]),
            )
        else:
            connection.execute(
                """UPDATE matches SET team_a_id = NULL, team_b_id = NULL,
                   winner_team_id = NULL, status = 'void' WHERE id = ?""",
                (match["id"],),
            )


def create_tournament(connection, name, team_names, submitted_stages=None):
    clean_name = str(name or "").strip()[:100]
    clean_teams = []
    seen = set()
    for raw_name in team_names:
        team_name = str(raw_name or "").strip()[:60]
        key = team_name.casefold()
        if team_name and key not in seen:
            clean_teams.append(team_name)
            seen.add(key)
    if len(clean_teams) < 2:
        raise DomainError("Añade al menos dos equipos.")
    if not clean_name:
        raise DomainError("Escribe un nombre para el torneo.")

    capacity, stages_config = build_stage_config(len(clean_teams), submitted_stages)
    timestamp = now_seconds()
    with transaction(connection):
        cursor = connection.execute(
            "INSERT INTO tournaments(name, status, created_at, updated_at) VALUES (?, 'active', ?, ?)",
            (clean_name, timestamp, timestamp),
        )
        tournament_id = cursor.lastrowid

        team_ids = []
        for seed, team_name in enumerate(clean_teams, start=1):
            cursor = connection.execute(
                "INSERT INTO teams(tournament_id, name, seed, color) VALUES (?, ?, ?, ?)",
                (tournament_id, team_name, seed, COLORS[(seed - 1) % len(COLORS)]),
            )
            team_ids.append(cursor.lastrowid)

        stage_ids = []
        for index, config in enumerate(stages_config):
            cursor = connection.execute(
                """INSERT INTO stages(tournament_id, name, stage_index, best_of,
                   points_per_set, win_by) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    tournament_id,
                    config["name"],
                    index,
                    config["best_of"],
                    config["points_per_set"],
                    config["win_by"],
                ),
            )
            stage_ids.append(cursor.lastrowid)

        previous_matches = []
        first_match_count = capacity // 2
        for bracket_index in range(first_match_count):
            team_a = team_ids[bracket_index * 2] if bracket_index * 2 < len(team_ids) else None
            team_b = team_ids[bracket_index * 2 + 1] if bracket_index * 2 + 1 < len(team_ids) else None
            previous_matches.append(
                _insert_match(
                    connection,
                    tournament_id,
                    stage_ids[0],
                    bracket_index,
                    stages_config[0],
                    team_a,
                    team_b,
                    None,
                    None,
                    timestamp,
                )
            )

        for stage_index in range(1, len(stage_ids)):
            current_matches = []
            for bracket_index in range(len(previous_matches) // 2):
                current_matches.append(
                    _insert_match(
                        connection,
                        tournament_id,
                        stage_ids[stage_index],
                        bracket_index,
                        stages_config[stage_index],
                        None,
                        None,
                        previous_matches[bracket_index * 2],
                        previous_matches[bracket_index * 2 + 1],
                        timestamp,
                    )
                )
            previous_matches = current_matches

        refresh_bracket(connection, tournament_id)
    return tournament_id


def _insert_match(
    connection,
    tournament_id,
    stage_id,
    bracket_index,
    config,
    team_a_id,
    team_b_id,
    source_a_match_id,
    source_b_match_id,
    timestamp,
):
    status = "scheduled"
    winner = None
    if team_a_id and not team_b_id or team_b_id and not team_a_id:
        status = "bye"
        winner = team_a_id or team_b_id
    elif not team_a_id and not team_b_id:
        status = "void"
    cursor = connection.execute(
        """INSERT INTO matches(tournament_id, stage_id, bracket_index, team_a_id,
           team_b_id, source_a_match_id, source_b_match_id, winner_team_id,
           status, best_of, points_per_set, win_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            tournament_id,
            stage_id,
            bracket_index,
            team_a_id,
            team_b_id,
            source_a_match_id,
            source_b_match_id,
            winner,
            status,
            config["best_of"],
            config["points_per_set"],
            config["win_by"],
            timestamp,
        ),
    )
    match_id = cursor.lastrowid
    if status == "scheduled":
        _ensure_set(connection, match_id, 1)
    return match_id


def start_match(connection, match_id, current_time=None):
    current_time = now_seconds() if current_time is None else current_time
    with transaction(connection):
        match = connection.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
        if not match or not match["team_a_id"] or not match["team_b_id"]:
            raise DomainError("Este partido todavía no tiene dos equipos.")
        if match["status"] == "finished":
            raise DomainError("El partido ya ha terminado.")
        if match["status"] not in ("scheduled", "paused"):
            raise DomainError("El partido ya está en juego.")
        other_live = connection.execute(
            """SELECT id FROM matches WHERE tournament_id = ? AND status = 'live'
               AND id != ? LIMIT 1""",
            (match["tournament_id"], match_id),
        ).fetchone()
        if other_live:
            raise DomainError("Ya hay otro partido en juego. Páralo antes de comenzar este.")
        _ensure_set(connection, match_id, match["current_set"])
        connection.execute(
            "UPDATE matches SET status = 'live', timer_started_at = ? WHERE id = ?",
            (current_time, match_id),
        )
        connection.execute(
            "UPDATE tournaments SET updated_at = ? WHERE id = ?",
            (current_time, match["tournament_id"]),
        )


def pause_match(connection, match_id, current_time=None):
    current_time = now_seconds() if current_time is None else current_time
    with transaction(connection):
        match = connection.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
        if not match or match["status"] != "live":
            raise DomainError("El partido no está en marcha.")
        elapsed = elapsed_seconds(match, current_time)
        connection.execute(
            """UPDATE matches SET status = 'paused', timer_elapsed = ?,
               timer_started_at = NULL WHERE id = ?""",
            (elapsed, match_id),
        )
        connection.execute(
            "UPDATE tournaments SET updated_at = ? WHERE id = ?",
            (current_time, match["tournament_id"]),
        )


def add_point(
    connection,
    match_id,
    team_id,
    user_id,
    event_id=None,
    current_time=None,
    client_elapsed=None,
):
    current_time = now_seconds() if current_time is None else current_time
    event_id = str(event_id or uuid.uuid4())
    with transaction(connection):
        existing = connection.execute(
            "SELECT event_id FROM point_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if existing:
            return {"event_id": event_id, "duplicate": True}

        match = connection.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
        if not match:
            raise DomainError("No se ha encontrado el partido.")
        if match["status"] != "live":
            raise DomainError("Primero hay que iniciar el partido.")
        if team_id not in (match["team_a_id"], match["team_b_id"]):
            raise DomainError("Ese equipo no juega este partido.")

        current_set = connection.execute(
            "SELECT * FROM sets WHERE match_id = ? AND set_number = ?",
            (match_id, match["current_set"]),
        ).fetchone()
        if not current_set:
            _ensure_set(connection, match_id, match["current_set"])
            current_set = connection.execute(
                "SELECT * FROM sets WHERE match_id = ? AND set_number = ?",
                (match_id, match["current_set"]),
            ).fetchone()
        snapshot = _match_snapshot(connection, match_id)
        score_a = current_set["score_a"] + (1 if team_id == match["team_a_id"] else 0)
        score_b = current_set["score_b"] + (1 if team_id == match["team_b_id"] else 0)
        point_time = elapsed_seconds(match, current_time)
        if client_elapsed is not None:
            try:
                point_time = max(0, int(client_elapsed))
            except (TypeError, ValueError):
                pass
        set_winner = None
        if is_set_won(score_a, score_b, match["points_per_set"], match["win_by"]):
            set_winner = match["team_a_id"] if score_a > score_b else match["team_b_id"]
        connection.execute(
            "UPDATE sets SET score_a = ?, score_b = ?, winner_team_id = ?, status = ? "
            "WHERE match_id = ? AND set_number = ?",
            (
                score_a,
                score_b,
                set_winner,
                "finished" if set_winner else "live",
                match_id,
                match["current_set"],
            ),
        )

        sets_a = match["sets_a"]
        sets_b = match["sets_b"]
        next_set = match["current_set"]
        match_winner = None
        next_status = "live"
        if set_winner:
            if set_winner == match["team_a_id"]:
                sets_a += 1
            else:
                sets_b += 1
            if sets_a >= sets_needed(match["best_of"]):
                match_winner = match["team_a_id"]
            elif sets_b >= sets_needed(match["best_of"]):
                match_winner = match["team_b_id"]
            else:
                next_set += 1
                _ensure_set(connection, match_id, next_set)

        if match_winner:
            next_status = "finished"
            connection.execute(
                """UPDATE matches SET status = 'finished', winner_team_id = ?, sets_a = ?,
                   sets_b = ?, timer_elapsed = ?, timer_started_at = NULL, finished_at = ?
                   WHERE id = ?""",
                (match_winner, sets_a, sets_b, point_time, current_time, match_id),
            )
        else:
            connection.execute(
                """UPDATE matches SET status = ?, current_set = ?, sets_a = ?, sets_b = ?,
                   timer_elapsed = ?, timer_started_at = ? WHERE id = ?""",
                (
                    next_status,
                    next_set,
                    sets_a,
                    sets_b,
                    point_time,
                    current_time if next_status == "live" else None,
                    match_id,
                ),
            )

        connection.execute(
            """INSERT INTO point_events(event_id, match_id, set_number, team_id, score_a,
               score_b, elapsed_seconds, created_at, created_by, state_before)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                match_id,
                match["current_set"],
                team_id,
                score_a,
                score_b,
                point_time,
                current_time,
                user_id,
                snapshot,
            ),
        )
        if match_winner:
            refresh_bracket(connection, match["tournament_id"])
        connection.execute(
            "UPDATE tournaments SET updated_at = ? WHERE id = ?",
            (current_time, match["tournament_id"]),
        )
    return {"event_id": event_id, "duplicate": False}


def finish_match(connection, match_id, winner_team_id, current_time=None):
    current_time = now_seconds() if current_time is None else current_time
    with transaction(connection):
        match = connection.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
        if not match or not match["team_a_id"] or not match["team_b_id"]:
            raise DomainError("Este partido no está listo para finalizar.")
        if winner_team_id not in (match["team_a_id"], match["team_b_id"]):
            raise DomainError("El ganador debe ser uno de los dos equipos.")
        if match["status"] == "finished":
            raise DomainError("El partido ya ha terminado.")
        elapsed = elapsed_seconds(match, current_time)
        connection.execute(
            """UPDATE matches SET status = 'finished', winner_team_id = ?,
               timer_elapsed = ?, timer_started_at = NULL, finished_at = ? WHERE id = ?""",
            (winner_team_id, elapsed, current_time, match_id),
        )
        refresh_bracket(connection, match["tournament_id"])
        connection.execute(
            "UPDATE tournaments SET updated_at = ? WHERE id = ?",
            (current_time, match["tournament_id"]),
        )


def undo_last_point(connection, match_id, current_time=None):
    current_time = now_seconds() if current_time is None else current_time
    with transaction(connection):
        event = connection.execute(
            """SELECT * FROM point_events WHERE match_id = ? AND undone = 0
               ORDER BY created_at DESC, rowid DESC LIMIT 1""",
            (match_id,),
        ).fetchone()
        if not event:
            raise DomainError("No hay ningún punto que deshacer.")

        match = connection.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
        if match["status"] == "finished":
            children = connection.execute(
                """SELECT * FROM matches WHERE source_a_match_id = ?
                   OR source_b_match_id = ?""",
                (match_id, match_id),
            ).fetchall()
            if any(child["status"] in ("live", "paused", "finished") for child in children):
                raise DomainError("No se puede deshacer: el siguiente partido ya ha comenzado.")

        _restore_snapshot(connection, match_id, event["state_before"])
        connection.execute(
            "UPDATE point_events SET undone = 1 WHERE event_id = ?",
            (event["event_id"],),
        )
        refresh_bracket(connection, match["tournament_id"])
        connection.execute(
            "UPDATE tournaments SET updated_at = ? WHERE id = ?",
            (current_time, match["tournament_id"]),
        )
    return event["event_id"]


def hydrate_match(connection, match_id, payload, current_time=None):
    current_time = now_seconds() if current_time is None else current_time
    try:
        current_set = max(1, int(payload.get("current_set", 1)))
        score_a = max(0, int(payload.get("score_a", 0)))
        score_b = max(0, int(payload.get("score_b", 0)))
        sets_a = max(0, int(payload.get("sets_a", 0)))
        sets_b = max(0, int(payload.get("sets_b", 0)))
        elapsed = max(0, int(payload.get("elapsed_seconds", 0)))
    except (TypeError, ValueError):
        raise DomainError("Los datos del marcador no son válidos.")
    status = payload.get("status", "paused")
    if status not in ("scheduled", "live", "paused"):
        raise DomainError("El estado inicial no es válido.")

    with transaction(connection):
        match = connection.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
        if not match or not match["team_a_id"] or not match["team_b_id"]:
            raise DomainError("El partido no tiene dos equipos.")
        connection.execute("DELETE FROM sets WHERE match_id = ?", (match_id,))
        set_scores = payload.get("set_scores") or []
        for number in range(1, current_set + 1):
            item = set_scores[number - 1] if number - 1 < len(set_scores) else {}
            item_a = max(0, int(item.get("a", 0))) if isinstance(item, dict) else 0
            item_b = max(0, int(item.get("b", 0))) if isinstance(item, dict) else 0
            if number == current_set:
                item_a, item_b = score_a, score_b
            winner = None
            if is_set_won(item_a, item_b, match["points_per_set"], match["win_by"]):
                winner = match["team_a_id"] if item_a > item_b else match["team_b_id"]
            set_status = "finished" if winner else "live"
            connection.execute(
                """INSERT INTO sets(match_id, set_number, score_a, score_b, winner_team_id,
                   status) VALUES (?, ?, ?, ?, ?, ?)""",
                (match_id, number, item_a, item_b, winner, set_status),
            )
        connection.execute(
            """UPDATE matches SET status = ?, current_set = ?, sets_a = ?, sets_b = ?,
               timer_elapsed = ?, timer_started_at = ?, winner_team_id = NULL,
               finished_at = NULL WHERE id = ?""",
            (
                status,
                current_set,
                sets_a,
                sets_b,
                elapsed,
                current_time if status == "live" else None,
                match_id,
            ),
        )
        connection.execute(
            "UPDATE point_events SET undone = 1 WHERE match_id = ? AND undone = 0",
            (match_id,),
        )
        refresh_bracket(connection, match["tournament_id"])
        connection.execute(
            "UPDATE tournaments SET updated_at = ? WHERE id = ?",
            (current_time, match["tournament_id"]),
        )
