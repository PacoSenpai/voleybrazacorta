(function () {
    "use strict";

    let state = window.APP_STATE || {};
    const root = document.getElementById("bracket-root");
    const summary = document.getElementById("bracket-summary");

    function teamName(team) {
        return team ? App.escapeHtml(team.name) : "Por decidir";
    }

    function renderSummary() {
        const tournament = state.tournament;
        if (!tournament) {
            summary.innerHTML = "";
            return;
        }
        const total = state.matches.length;
        const finished = state.matches.filter((match) => match.status === "finished").length;
        const active = state.active_match;
        summary.innerHTML = `<div><span class="eyebrow">${App.escapeHtml(tournament.name)}</span><strong>${finished} <small>/ ${total} cruces</small></strong></div>
            <div class="summary-live">${active ? `<span class="live-pip"></span> ${App.escapeHtml(active.team_a.name)} — ${App.escapeHtml(active.team_b.name)}` : "Sin partido en juego"}</div>`;
    }

    function matchMarkup(match, stage, index) {
        const live = match.status === "live" || match.status === "paused";
        const winner = match.winner_team_id;
        return `<article class="bracket-match is-${match.status}">
            <span class="match-number">${App.escapeHtml(stage.name.slice(0, 3).toUpperCase())} · ${String(index + 1).padStart(2, "0")}</span>
            <div class="bracket-team ${winner === match.team_a?.id ? "is-winner" : ""}"><span>${teamName(match.team_a)}</span><b>${match.sets_a}</b></div>
            <div class="bracket-team ${winner === match.team_b?.id ? "is-winner" : ""}"><span>${teamName(match.team_b)}</span><b>${match.sets_b}</b></div>
            <span class="bracket-status">${live ? `<span class="live-pip"></span>${App.statusLabel(match.status)}` : App.statusLabel(match.status)}</span>
        </article>`;
    }

    function render() {
        renderSummary();
        if (!state.stages?.length) {
            root.innerHTML = `<div class="bracket-empty"><span class="empty-orbit"></span><h2>El cuadro aún no está creado.</h2><p>El administrador podrá cargar los equipos cuando empiece el torneo.</p></div>`;
            return;
        }
        root.innerHTML = `<div class="bracket-board">${state.stages.map((stage) => `<section class="bracket-column">
            <header class="column-header"><span>${String(stage.stage_index + 1).padStart(2, "0")}</span><h2>${App.escapeHtml(stage.name)}</h2><small>${stage.points_per_set} pts</small></header>
            <div class="column-matches">${stage.matches.map((match, index) => matchMarkup(match, stage, index)).join("")}</div>
        </section>`).join("")}</div>`;
    }

    async function refresh() {
        try {
            state = await App.requestJSON("/api/public/state");
            render();
        } catch (error) {}
    }

    render();
    App.connectEvents(refresh);
    window.setInterval(refresh, 15000);
    App.installServiceWorker();
})();
