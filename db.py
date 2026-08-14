from contextlib import contextmanager
import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tournaments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    seed INTEGER NOT NULL,
    color TEXT NOT NULL,
    UNIQUE(tournament_id, name)
);

CREATE TABLE IF NOT EXISTS stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    stage_index INTEGER NOT NULL,
    best_of INTEGER NOT NULL DEFAULT 3,
    points_per_set INTEGER NOT NULL DEFAULT 15,
    win_by INTEGER NOT NULL DEFAULT 2,
    UNIQUE(tournament_id, stage_index)
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
    stage_id INTEGER NOT NULL REFERENCES stages(id) ON DELETE CASCADE,
    bracket_index INTEGER NOT NULL,
    team_a_id INTEGER REFERENCES teams(id) ON DELETE SET NULL,
    team_b_id INTEGER REFERENCES teams(id) ON DELETE SET NULL,
    source_a_match_id INTEGER REFERENCES matches(id) ON DELETE SET NULL,
    source_b_match_id INTEGER REFERENCES matches(id) ON DELETE SET NULL,
    winner_team_id INTEGER REFERENCES teams(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'scheduled',
    best_of INTEGER NOT NULL DEFAULT 3,
    points_per_set INTEGER NOT NULL DEFAULT 15,
    win_by INTEGER NOT NULL DEFAULT 2,
    current_set INTEGER NOT NULL DEFAULT 1,
    sets_a INTEGER NOT NULL DEFAULT 0,
    sets_b INTEGER NOT NULL DEFAULT 0,
    timer_elapsed INTEGER NOT NULL DEFAULT 0,
    timer_started_at REAL,
    finished_at REAL,
    created_at REAL NOT NULL,
    UNIQUE(stage_id, bracket_index)
);

CREATE TABLE IF NOT EXISTS sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    set_number INTEGER NOT NULL,
    score_a INTEGER NOT NULL DEFAULT 0,
    score_b INTEGER NOT NULL DEFAULT 0,
    winner_team_id INTEGER REFERENCES teams(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'live',
    UNIQUE(match_id, set_number)
);

CREATE TABLE IF NOT EXISTS point_events (
    event_id TEXT PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    set_number INTEGER NOT NULL,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    score_a INTEGER NOT NULL,
    score_b INTEGER NOT NULL,
    elapsed_seconds INTEGER NOT NULL,
    created_at REAL NOT NULL,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    undone INTEGER NOT NULL DEFAULT 0,
    state_before TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_matches_tournament ON matches(tournament_id);
CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status);
CREATE INDEX IF NOT EXISTS idx_sets_match ON sets(match_id);
CREATE INDEX IF NOT EXISTS idx_points_match ON point_events(match_id, created_at);
"""


def connect(database_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(
        database_path,
        timeout=15,
        isolation_level=None,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def init_database(database_path: str) -> None:
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    connection = connect(database_path)
    try:
        connection.executescript(SCHEMA)
    finally:
        connection.close()


@contextmanager
def transaction(connection: sqlite3.Connection):
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
