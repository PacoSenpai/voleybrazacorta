import json
import os
import queue
import secrets
import threading
import time
from functools import wraps
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from flask import (
    Flask,
    Response,
    current_app,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    stream_with_context,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from db import connect, init_database, transaction
from domain import (
    DomainError,
    add_point,
    create_tournament,
    finish_match,
    hydrate_match,
    pause_match,
    start_match,
    undo_last_point,
)
from state import tournament_state


class EventHub:
    """Small in-process event bus for the single Flask worker used by the app."""

    def __init__(self):
        self._subscribers = set()
        self._lock = threading.Lock()

    def subscribe(self):
        channel = queue.Queue()
        with self._lock:
            self._subscribers.add(channel)
        return channel

    def unsubscribe(self, channel):
        with self._lock:
            self._subscribers.discard(channel)

    def publish(self, payload=None):
        payload = payload or {"type": "state_changed"}
        with self._lock:
            subscribers = list(self._subscribers)
        for channel in subscribers:
            channel.put(payload)


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
        DATABASE_PATH=os.environ.get(
            "DATABASE_PATH", str(Path(app.instance_path) / "brazacorta.sqlite3")
        ),
    )
    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    init_database(app.config["DATABASE_PATH"])
    app.extensions["event_hub"] = EventHub()
    seed_users(app)

    @app.before_request
    def open_database():
        g.db = connect(app.config["DATABASE_PATH"])
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(24)

    @app.teardown_request
    def close_database(error=None):
        connection = g.pop("db", None)
        if connection:
            connection.close()

    @app.before_request
    def protect_state_changes():
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return None
        protected = request.path == "/acceso" or request.path == "/api/logout" or request.path.startswith(
            "/api/admin/"
        )
        if not protected:
            return None
        supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
        if not supplied or supplied != session.get("csrf_token"):
            if request.path.startswith("/api/"):
                return jsonify(error="La sesión ha caducado. Recarga la página."), 400
            return render_template("login.html", error="La sesión ha caducado. Recarga la página."), 400
        return None

    @app.context_processor
    def template_context():
        return {
            "csrf_token": session.get("csrf_token", ""),
            "logged_user": current_user(g.db),
        }

    @app.get("/")
    def public_home():
        return render_template("index.html", state=tournament_state(g.db))

    @app.get("/cuadro")
    def public_bracket():
        return render_template("bracket.html", state=tournament_state(g.db))

    @app.route("/acceso", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            return render_template("login.html")
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        user = g.db.execute(
            "SELECT * FROM users WHERE username = ? AND active = 1", (username,)
        ).fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            return render_template("login.html", error="Usuario o contraseña incorrectos."), 401
        session.clear()
        session["user_id"] = user["id"]
        session["csrf_token"] = secrets.token_urlsafe(24)
        return redirect(url_for("admin_panel"))

    @app.get("/admin")
    @login_required()
    def admin_panel():
        return render_template("admin.html", state=tournament_state(g.db, include_admin=True))

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get("/api/public/state")
    def public_state():
        return jsonify(tournament_state(g.db))

    @app.get("/api/admin/state")
    @login_required(api=True)
    def admin_state():
        return jsonify(tournament_state(g.db, include_admin=True))

    @app.get("/api/stream")
    def event_stream():
        channel = app.extensions["event_hub"].subscribe()

        @stream_with_context
        def generate():
            try:
                yield "event: ready\ndata: {}\n\n"
                while True:
                    try:
                        payload = channel.get(timeout=20)
                        yield f"event: state\ndata: {json.dumps(payload)}\n\n"
                    except queue.Empty:
                        yield ": keepalive\n\n"
            finally:
                app.extensions["event_hub"].unsubscribe(channel)

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/admin/tournaments")
    @login_required(api=True)
    def create_tournament_api():
        payload = request.get_json(silent=True) or request.form.to_dict(flat=False)
        name = payload.get("name", "")
        if isinstance(name, list):
            name = name[0] if name else ""
        teams = payload.get("teams", payload.get("team_names", []))
        if isinstance(teams, str):
            teams = teams.splitlines()
        stages = payload.get("stages", [])
        if isinstance(stages, str):
            try:
                stages = json.loads(stages)
            except json.JSONDecodeError:
                stages = []
        try:
            tournament_id = create_tournament(g.db, name, teams, stages)
        except DomainError as error:
            return jsonify(error=str(error)), 400
        publish_state()
        return jsonify(
            ok=True,
            tournament_id=tournament_id,
            state=tournament_state(g.db, include_admin=True),
        )

    @app.post("/api/admin/matches/<int:match_id>/start")
    @login_required(api=True)
    def start_match_api(match_id):
        try:
            start_match(g.db, match_id)
        except DomainError as error:
            return jsonify(error=str(error)), 400
        publish_state()
        return jsonify(ok=True, state=tournament_state(g.db, include_admin=True))

    @app.post("/api/admin/matches/<int:match_id>/pause")
    @login_required(api=True)
    def pause_match_api(match_id):
        try:
            pause_match(g.db, match_id)
        except DomainError as error:
            return jsonify(error=str(error)), 400
        publish_state()
        return jsonify(ok=True, state=tournament_state(g.db, include_admin=True))

    @app.post("/api/admin/matches/<int:match_id>/point")
    @login_required(api=True)
    def point_api(match_id):
        payload = request.get_json(silent=True) or {}
        try:
            team_id = int(payload.get("team_id"))
        except (TypeError, ValueError):
            return jsonify(error="Selecciona un equipo."), 400
        try:
            result = add_point(
                g.db,
                match_id,
                team_id,
                session["user_id"],
                event_id=payload.get("event_id"),
                client_elapsed=payload.get("elapsed_seconds"),
            )
        except DomainError as error:
            return jsonify(error=str(error)), 400
        publish_state()
        return jsonify(ok=True, **result, state=tournament_state(g.db, include_admin=True))

    @app.post("/api/admin/matches/<int:match_id>/undo")
    @login_required(api=True)
    def undo_api(match_id):
        try:
            event_id = undo_last_point(g.db, match_id)
        except DomainError as error:
            return jsonify(error=str(error)), 400
        publish_state()
        return jsonify(ok=True, event_id=event_id, state=tournament_state(g.db, include_admin=True))

    @app.post("/api/admin/matches/<int:match_id>/finish")
    @login_required(api=True)
    def finish_api(match_id):
        payload = request.get_json(silent=True) or {}
        try:
            winner_team_id = int(payload.get("winner_team_id"))
        except (TypeError, ValueError):
            return jsonify(error="Selecciona el equipo ganador."), 400
        try:
            finish_match(g.db, match_id, winner_team_id)
        except DomainError as error:
            return jsonify(error=str(error)), 400
        publish_state()
        return jsonify(ok=True, state=tournament_state(g.db, include_admin=True))

    @app.post("/api/admin/matches/<int:match_id>/hydrate")
    @login_required(api=True)
    def hydrate_api(match_id):
        payload = request.get_json(silent=True) or {}
        try:
            hydrate_match(g.db, match_id, payload)
        except (DomainError, ValueError) as error:
            return jsonify(error=str(error)), 400
        publish_state()
        return jsonify(ok=True, state=tournament_state(g.db, include_admin=True))

    @app.get("/manifest.webmanifest")
    def manifest():
        return send_from_directory(app.static_folder, "manifest.webmanifest")

    @app.get("/sw.js")
    def service_worker():
        return send_from_directory(app.static_folder, "sw.js")

    return app


def seed_users(app):
    connection = connect(app.config["DATABASE_PATH"])
    try:
        with transaction(connection):
            for number in range(1, 6):
                username = f"arbitro{number}"
                password = os.environ.get(f"ARBITRO{number}_PASSWORD", f"saque{number}")
                exists = connection.execute(
                    "SELECT id FROM users WHERE username = ?", (username,)
                ).fetchone()
                if not exists:
                    connection.execute(
                        "INSERT INTO users(username, password_hash, created_at) VALUES (?, ?, ?)",
                        (username, generate_password_hash(password), time.time()),
                    )
    finally:
        connection.close()


def current_user(connection):
    user_id = session.get("user_id")
    if not user_id:
        return None
    user = connection.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(user) if user else None


def login_required(api=False):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("user_id"):
                if api:
                    return jsonify(error="Necesitas iniciar sesión."), 401
                return redirect(url_for("login"))
            return view(*args, **kwargs)

        return wrapped

    return decorator


def publish_state():
    # Clients fetch a fresh snapshot after receiving this small notification.
    current_app.extensions["event_hub"].publish({"type": "state_changed"})


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), threaded=True, debug=True)
