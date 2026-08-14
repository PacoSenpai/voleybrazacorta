import json
import tempfile
import unittest
from pathlib import Path

from app import create_app
from db import connect, init_database
from domain import add_point, create_tournament, pause_match, start_match, undo_last_point


class TournamentDomainTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temp_dir.name) / "test.sqlite3")
        init_database(self.database_path)
        self.connection = connect(self.database_path)

    def tearDown(self):
        self.connection.close()
        self.temp_dir.cleanup()

    def test_variable_bracket_creates_byes_and_named_rounds(self):
        tournament_id = create_tournament(
            self.connection,
            "Fiesta del pueblo",
            ["A", "B", "C", "D", "E"],
        )
        stages = self.connection.execute(
            "SELECT name FROM stages WHERE tournament_id = ? ORDER BY stage_index",
            (tournament_id,),
        ).fetchall()
        self.assertEqual([row["name"] for row in stages], ["Cuartos", "Semifinal", "Final"])
        first_stage_id = self.connection.execute(
            "SELECT id FROM stages WHERE tournament_id = ? AND stage_index = 0",
            (tournament_id,),
        ).fetchone()["id"]
        first_round = self.connection.execute(
            "SELECT status FROM matches WHERE tournament_id = ? AND stage_id = ? ORDER BY bracket_index",
            (tournament_id, first_stage_id),
        ).fetchall()
        self.assertIn("bye", [row["status"] for row in first_round])
        self.assertIn("void", [row["status"] for row in first_round])

    def test_best_of_three_uses_stage_points_and_winner_advances(self):
        tournament_id = create_tournament(
            self.connection,
            "Prueba",
            ["A", "B"],
            [{"name": "Final", "best_of": 3, "points_per_set": 2, "win_by": 2}],
        )
        match = self.connection.execute(
            "SELECT * FROM matches WHERE tournament_id = ?", (tournament_id,)
        ).fetchone()
        start_match(self.connection, match["id"], current_time=100)
        team_a = match["team_a_id"]
        add_point(self.connection, match["id"], team_a, None, event_id="a1", current_time=101)
        add_point(self.connection, match["id"], team_a, None, event_id="a2", current_time=102)
        add_point(self.connection, match["id"], team_a, None, event_id="a3", current_time=103)
        add_point(self.connection, match["id"], team_a, None, event_id="a4", current_time=104)
        result = self.connection.execute("SELECT * FROM matches WHERE id = ?", (match["id"],)).fetchone()
        self.assertEqual(result["status"], "finished")
        self.assertEqual(result["winner_team_id"], team_a)
        self.assertEqual((result["sets_a"], result["sets_b"]), (2, 0))

    def test_timer_pause_keeps_elapsed_time(self):
        tournament_id = create_tournament(self.connection, "Reloj", ["A", "B"])
        match = self.connection.execute(
            "SELECT * FROM matches WHERE tournament_id = ?", (tournament_id,)
        ).fetchone()
        start_match(self.connection, match["id"], current_time=100)
        add_point(
            self.connection,
            match["id"],
            match["team_a_id"],
            None,
            event_id="timer-point",
            current_time=110,
        )
        pause_match(self.connection, match["id"], current_time=120)
        paused = self.connection.execute("SELECT * FROM matches WHERE id = ?", (match["id"],)).fetchone()
        self.assertEqual(paused["timer_elapsed"], 20)
        self.assertIsNone(paused["timer_started_at"])

    def test_undo_restores_previous_set_snapshot(self):
        tournament_id = create_tournament(
            self.connection,
            "Deshacer",
            ["A", "B"],
            [{"name": "Final", "best_of": 1, "points_per_set": 1, "win_by": 1}],
        )
        match = self.connection.execute(
            "SELECT * FROM matches WHERE tournament_id = ?", (tournament_id,)
        ).fetchone()
        start_match(self.connection, match["id"], current_time=100)
        add_point(self.connection, match["id"], match["team_a_id"], None, event_id="undo-point", current_time=101)
        undo_last_point(self.connection, match["id"], current_time=102)
        restored = self.connection.execute("SELECT * FROM matches WHERE id = ?", (match["id"],)).fetchone()
        current_set = self.connection.execute("SELECT * FROM sets WHERE match_id = ?", (match["id"],)).fetchone()
        self.assertEqual(restored["status"], "live")
        self.assertEqual((current_set["score_a"], current_set["score_b"]), (0, 0))

    def test_undo_removes_a_winner_from_the_next_bracket_match(self):
        tournament_id = create_tournament(
            self.connection,
            "Cuadro",
            ["A", "B", "C", "D"],
            [
                {"name": "Semifinal", "best_of": 1, "points_per_set": 1, "win_by": 1},
                {"name": "Final", "best_of": 1, "points_per_set": 1, "win_by": 1},
            ],
        )
        semifinal = self.connection.execute(
            """SELECT matches.* FROM matches JOIN stages ON stages.id = matches.stage_id
               WHERE matches.tournament_id = ? AND stages.stage_index = 0 AND matches.bracket_index = 0""",
            (tournament_id,),
        ).fetchone()
        start_match(self.connection, semifinal["id"], current_time=100)
        add_point(
            self.connection,
            semifinal["id"],
            semifinal["team_a_id"],
            None,
            event_id="cascade-point",
            current_time=101,
        )
        final = self.connection.execute(
            """SELECT matches.* FROM matches JOIN stages ON stages.id = matches.stage_id
               WHERE matches.tournament_id = ? AND stages.stage_index = 1""",
            (tournament_id,),
        ).fetchone()
        self.assertEqual(final["team_a_id"], semifinal["team_a_id"])
        undo_last_point(self.connection, semifinal["id"], current_time=102)
        final = self.connection.execute("SELECT * FROM matches WHERE id = ?", (final["id"],)).fetchone()
        self.assertIsNone(final["team_a_id"])


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret",
                "DATABASE_PATH": str(Path(self.temp_dir.name) / "app.sqlite3"),
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def csrf(self):
        with self.client.session_transaction() as session:
            return session["csrf_token"]

    def login(self):
        response = self.client.post(
            "/acceso",
            data={
                "username": "arbitro1",
                "password": "saque1",
                "csrf_token": self.csrf(),
            },
        )
        self.assertEqual(response.status_code, 302)

    def test_admin_can_create_tournament_and_public_can_read_it(self):
        self.client.get("/acceso")
        self.login()
        response = self.client.post(
            "/api/admin/tournaments",
            json={"name": "Torneo BZA", "teams": ["Sol", "Sombra"]},
            headers={"X-CSRF-Token": self.csrf()},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["state"]["tournament"]["name"], "Torneo BZA")
        public = self.client.get("/api/public/state")
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.get_json()["stages"][0]["name"], "Final")

    def test_admin_api_rejects_missing_csrf(self):
        self.client.get("/acceso")
        self.login()
        response = self.client.post("/api/admin/tournaments", json={"name": "No", "teams": ["A", "B"]})
        self.assertEqual(response.status_code, 400)

    def test_public_and_admin_pages_render(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/cuadro").status_code, 200)
        self.assertEqual(self.client.get("/acceso").status_code, 200)
        self.login()
        self.assertEqual(self.client.get("/admin").status_code, 200)

    def test_api_can_start_and_register_a_point(self):
        self.client.get("/acceso")
        self.login()
        csrf = self.csrf()
        created = self.client.post(
            "/api/admin/tournaments",
            json={"name": "Puntos", "teams": ["Sol", "Sombra"]},
            headers={"X-CSRF-Token": csrf},
        ).get_json()
        match = created["state"]["matches"][0]
        started = self.client.post(
            f"/api/admin/matches/{match['id']}/start",
            json={},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(started.status_code, 200)
        live = started.get_json()["state"]["active_match"]
        point = self.client.post(
            f"/api/admin/matches/{match['id']}/point",
            json={"team_id": live["team_a"]["id"], "event_id": "api-point"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(point.status_code, 200, point.get_json())
        self.assertEqual(point.get_json()["state"]["active_match"]["points"][0]["score_a"], 1)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/cuadro").status_code, 200)


if __name__ == "__main__":
    unittest.main()
