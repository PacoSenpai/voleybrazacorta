(function () {
    "use strict";

    let state = window.APP_STATE || {};
    let clockTimer = null;
    let clockOffset = 0;

    const liveRoot = document.getElementById("live-match");
    const feedRoot = document.getElementById("points-feed");
    const nextRoot = document.getElementById("next-match-card");

    function currentScore(match, side) {
        return App.teamScore(match, side);
    }

    function teamMarkup(team, side, score) {
        if (!team) return `<div class="score-team team-${side} is-empty"><strong>Por decidir</strong><span class="team-score">${score}</span></div>`;
        return `<div class="score-team team-${side}">
            <span class="team-swatch" style="--team-color: ${team.color}"></span>
            <strong>${App.escapeHtml(team.name)}</strong>
            <span class="team-score">${score}</span>
        </div>`;
    }

    function renderLive() {
        const match = state.active_match;
        if (!match) {
            const next = state.next_match;
            liveRoot.innerHTML = `<article class="empty-live-card">
                <span class="empty-orbit" aria-hidden="true"></span>
                <p class="eyebrow">Ahora mismo</p>
                <h2>${next ? "La siguiente pelota<br><em>ya está lista.</em>" : "La pista está<br><em>entre partidos.</em>"}</h2>
                <p>${next ? `${App.escapeHtml(next.team_a.name)} contra ${App.escapeHtml(next.team_b.name)} es el siguiente cruce.` : "En cuanto empiece el siguiente, el marcador aparecerá aquí."}</p>
            </article>`;
            return;
        }
        const scoreA = currentScore(match, "a");
        const scoreB = currentScore(match, "b");
        const sets = (match.sets || []).map((set) => `<span class="set-pill">${set.score_a}—${set.score_b}</span>`).join("");
        liveRoot.innerHTML = `<article class="score-card is-${match.status}">
            <div class="score-card-head">
                <span class="status-label">${App.statusLabel(match.status)}</span>
                <span class="match-meta">${match.points_per_set} · mejor de ${match.best_of}</span>
            </div>
            <div class="score-teams">
                ${teamMarkup(match.team_a, "a", scoreA)}
                <div class="versus"><span>SET ${match.current_set}</span><b>:</b><span class="match-clock">${App.formatElapsed(match.timer_elapsed)}</span></div>
                ${teamMarkup(match.team_b, "b", scoreB)}
            </div>
            <div class="set-strip">${sets}</div>
        </article>`;
        clockOffset = Date.now() / 1000 - (Number(state.server_time) || Date.now() / 1000);
        updateClock();
    }

    function updateClock() {
        const match = state.active_match;
        const clock = liveRoot?.querySelector(".match-clock");
        if (!match || !clock) return;
        let elapsed = Number(match.timer_elapsed) || 0;
        if (match.status === "live" && match.timer_started_at) {
            elapsed += Math.max(0, Date.now() / 1000 - clockOffset - Number(match.timer_started_at));
        }
        clock.textContent = App.formatElapsed(elapsed);
    }

    function renderFeed() {
        const points = state.active_match?.points || [];
        const counter = document.getElementById("point-counter");
        if (counter) counter.textContent = points.length ? `${points.length} PUNTOS` : "EN VIVO";
        if (!points.length) {
            feedRoot.innerHTML = `<div class="empty-list"><span class="tiny-ball"></span><p>Los puntos irán apareciendo aquí.</p></div>`;
            return;
        }
        feedRoot.innerHTML = points.map((point) => `<div class="point-row">
            <span class="point-time">${App.formatElapsed(point.elapsed_seconds)}</span>
            <span class="point-line"></span>
            <span class="point-team"><i style="--team-color: ${point.team?.color || "#f2b544"}"></i>${App.escapeHtml(point.team?.name || "Equipo")}</span>
            <strong>${point.score_a}—${point.score_b}</strong>
        </div>`).join("");
    }

    function renderNext() {
        const match = state.next_match;
        if (!match) {
            nextRoot.innerHTML = `<p class="eyebrow">Lo siguiente</p><h2>El cuadro<br><em>está al día.</em></h2><div class="next-meta"><span>Consulta todos los cruces</span><a href="/cuadro">Ver cuadro →</a></div><span class="card-court-line" aria-hidden="true"></span>`;
            return;
        }
        nextRoot.innerHTML = `<p class="eyebrow">Lo siguiente</p>
            <h2>${App.escapeHtml(match.team_a?.name || "Por decidir")} <span>vs</span> ${App.escapeHtml(match.team_b?.name || "Por decidir")}</h2>
            <div class="next-meta"><span>${match.points_per_set} puntos por set</span><span>${App.statusText(match.status)}</span></div><span class="card-court-line" aria-hidden="true"></span>`;
    }

    function render() {
        renderLive();
        renderFeed();
        renderNext();
    }

    async function refresh() {
        try {
            state = await App.requestJSON("/api/public/state");
            render();
        } catch (error) {
            // The last rendered state remains visible while the tunnel reconnects.
        }
    }

    render();
    window.setInterval(updateClock, 1000);
    App.connectEvents(refresh);
    window.setInterval(refresh, 15000);
    App.installServiceWorker();
})();
