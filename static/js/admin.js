(function () {
    "use strict";

    let state = window.APP_STATE || {};
    let selectedMatchId = null;
    let pendingPoints = [];
    let clockOffset = 0;

    const setupForm = document.getElementById("setup-form");
    const teamInput = document.getElementById("team-input");
    const stageFields = document.getElementById("stage-fields");
    const matchSelector = document.getElementById("match-selector");
    const adminMatch = document.getElementById("admin-match");
    const controlActions = document.getElementById("control-actions");
    const hydrateForm = document.getElementById("hydrate-form");

    function makeId() {
        if (window.crypto?.randomUUID) return window.crypto.randomUUID();
        return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    }

    function openQueue() {
        return new Promise((resolve) => {
            if (!window.indexedDB) return resolve(null);
            const request = indexedDB.open("punto-brazacorta", 1);
            request.onupgradeneeded = () => request.result.createObjectStore("points", { keyPath: "event_id" });
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => resolve(null);
        });
    }

    async function loadQueue() {
        const database = await openQueue();
        if (!database) return [];
        return new Promise((resolve) => {
            const request = database.transaction("points", "readonly").objectStore("points").getAll();
            request.onsuccess = () => resolve(request.result || []);
            request.onerror = () => resolve([]);
        });
    }

    async function saveQueuedPoint(point) {
        pendingPoints.push(point);
        const database = await openQueue();
        if (!database) return;
        database.transaction("points", "readwrite").objectStore("points").put(point);
    }

    async function removeQueuedPoint(eventId) {
        pendingPoints = pendingPoints.filter((point) => point.event_id !== eventId);
        const database = await openQueue();
        if (!database) return;
        database.transaction("points", "readwrite").objectStore("points").delete(eventId);
    }

    function stageNames(teamCount) {
        const capacity = teamCount <= 2 ? 2 : 2 ** Math.ceil(Math.log2(teamCount));
        return {
            2: ["Final"],
            4: ["Semifinal", "Final"],
            8: ["Cuartos", "Semifinal", "Final"],
            16: ["Octavos", "Cuartos", "Semifinal", "Final"],
        }[capacity] || Array.from({ length: Math.log2(capacity) }, (_, index) => `Ronda ${index + 1}`);
    }

    function renderStageFields() {
        const teams = teamInput.value.split(/\n|,/).map((item) => item.trim()).filter(Boolean);
        const names = stageNames(Math.max(2, teams.length));
        stageFields.innerHTML = names.map((name, index) => {
            const points = name.toLowerCase() === "final" ? 21 : 15;
            return `<div class="stage-row">
                <div class="stage-row-name"><span>${String(index + 1).padStart(2, "0")}</span><input data-stage-name value="${name}" aria-label="Nombre de ronda ${index + 1}"></div>
                <label>Sets<select data-stage-best><option value="3" selected>Al mejor de 3</option><option value="1">Al mejor de 1</option><option value="5">Al mejor de 5</option></select></label>
                <label>Puntos<input data-stage-points type="number" min="1" value="${points}"></label>
                <span class="win-by-note">+2</span>
            </div>`;
        }).join("");
    }

    function getStagePayload() {
        return [...stageFields.querySelectorAll(".stage-row")].map((row) => ({
            name: row.querySelector("[data-stage-name]").value,
            best_of: Number(row.querySelector("[data-stage-best]").value),
            points_per_set: Number(row.querySelector("[data-stage-points]").value),
            win_by: 2,
        }));
    }

    function allMatches() {
        return state.matches || [];
    }

    function selectedMatch() {
        const flat = allMatches().find((match) => match.id === Number(selectedMatchId));
        if (!flat) return null;
        if (state.active_match?.id === flat.id) return structuredCloneSafe(state.active_match);
        return structuredCloneSafe(flat);
    }

    function structuredCloneSafe(value) {
        return value ? JSON.parse(JSON.stringify(value)) : null;
    }

    function pendingFor(matchId) {
        return pendingPoints.filter((point) => point.match_id === matchId);
    }

    function applyLocalPoint(match, point) {
        if (!match || match.status !== "live") return;
        let current = (match.sets || []).find((item) => item.set_number === match.current_set);
        if (!current) {
            current = { set_number: match.current_set, score_a: 0, score_b: 0, status: "live" };
            match.sets = [...(match.sets || []), current];
        }
        if (point.team_id === match.team_a.id) current.score_a += 1;
        if (point.team_id === match.team_b.id) current.score_b += 1;
        const high = Math.max(current.score_a, current.score_b);
        const low = Math.min(current.score_a, current.score_b);
        if (high >= match.points_per_set && high - low >= match.win_by) {
            current.status = "finished";
            current.winner_team_id = current.score_a > current.score_b ? match.team_a.id : match.team_b.id;
            if (current.winner_team_id === match.team_a.id) match.sets_a += 1;
            else match.sets_b += 1;
            if (match.sets_a >= Math.floor(match.best_of / 2) + 1 || match.sets_b >= Math.floor(match.best_of / 2) + 1) {
                match.status = "finished";
                match.winner_team_id = current.winner_team_id;
            } else {
                match.current_set += 1;
                match.sets.push({ set_number: match.current_set, score_a: 0, score_b: 0, status: "live" });
            }
        }
    }

    function displayMatch() {
        const match = selectedMatch();
        if (!match) return null;
        pendingFor(match.id).forEach((point) => applyLocalPoint(match, point));
        return match;
    }

    function matchLabel(match) {
        const stage = state.stages?.find((item) => item.id === match.stage_id);
        const teams = `${match.team_a?.name || "Por decidir"} vs ${match.team_b?.name || "Por decidir"}`;
        return `${stage?.name || "Partido"} · ${teams}`;
    }

    function renderSelector() {
        const available = allMatches().filter((match) => match.team_a && match.team_b && !["void", "bye"].includes(match.status));
        if (!selectedMatchId || !available.some((match) => match.id === Number(selectedMatchId))) {
            selectedMatchId = state.active_match?.id || available.find((match) => match.status === "scheduled")?.id || available[0]?.id || null;
        }
        matchSelector.innerHTML = available.length
            ? available.map((match) => `<option value="${match.id}" ${match.id === Number(selectedMatchId) ? "selected" : ""}>${App.escapeHtml(matchLabel(match))} · ${App.statusText(match.status)}</option>`).join("")
            : `<option value="">Crea un torneo para empezar</option>`;
    }

    function renderAdminMatch() {
        const match = displayMatch();
        const badge = document.getElementById("admin-status-badge");
        if (!match) {
            adminMatch.className = "admin-match-empty";
            adminMatch.innerHTML = `<span class="empty-orbit"></span><p>Selecciona o crea un partido con dos equipos.</p>`;
            controlActions.hidden = true;
            badge.textContent = "Sin partido";
            return;
        }
        const scoreA = App.teamScore(match, "a");
        const scoreB = App.teamScore(match, "b");
        const isLive = match.status === "live";
        const hasTeams = match.team_a && match.team_b;
        const pending = pendingFor(match.id).length;
        badge.textContent = App.statusLabel(match.status);
        adminMatch.className = `admin-match is-${match.status}`;
        adminMatch.innerHTML = `<div class="admin-match-head"><span class="status-label">${App.statusLabel(match.status)}</span><span>${match.points_per_set} puntos · +${match.win_by}</span></div>
            <div class="admin-score-grid">
                <button class="point-button team-a" data-action="point" data-team-id="${match.team_a?.id || ""}" ${!isLive || !match.team_a ? "disabled" : ""}>
                    <span class="point-team-name"><i style="--team-color: ${match.team_a?.color || "#90b7a7"}"></i>${App.escapeHtml(match.team_a?.name || "Por decidir")}</span><strong>${scoreA}</strong><small>+ punto</small>
                </button>
                <div class="admin-versus"><span>SET ${match.current_set}</span><b>:</b><span id="admin-clock">${App.formatElapsed(match.timer_elapsed)}</span></div>
                <button class="point-button team-b" data-action="point" data-team-id="${match.team_b?.id || ""}" ${!isLive || !match.team_b ? "disabled" : ""}>
                    <span class="point-team-name"><i style="--team-color: ${match.team_b?.color || "#f2b544"}"></i>${App.escapeHtml(match.team_b?.name || "Por decidir")}</span><strong>${scoreB}</strong><small>+ punto</small>
                </button>
            </div>
            <div class="admin-set-strip">${(match.sets || []).map((set) => `<span class="set-pill ${set.status === "finished" ? "is-finished" : ""}">Set ${set.set_number} <b>${set.score_a}—${set.score_b}</b></span>`).join("")}</div>
            ${pending ? `<div class="pending-note"><span class="sync-spinner"></span>${pending} punto${pending === 1 ? "" : "s"} pendiente${pending === 1 ? "" : "s"} de sincronizar</div>` : ""}`;

        controlActions.hidden = false;
        controlActions.innerHTML = `<div class="timer-actions">
                <button class="button button-primary" data-action="${isLive ? "pause" : "start"}" ${!hasTeams || match.status === "finished" ? "disabled" : ""}>${isLive ? "Pausar reloj" : match.status === "paused" ? "Reanudar reloj" : "Empezar partido"}</button>
                <button class="button button-ghost" data-action="undo" ${!match.points?.length && !pending ? "disabled" : ""}>Deshacer punto</button>
            </div>
            <div class="finish-actions"><span>Finalizar con victoria de:</span><button class="finish-button" data-action="finish" data-team-id="${match.team_a?.id || ""}" ${!hasTeams || match.status === "finished" ? "disabled" : ""}>${App.escapeHtml(match.team_a?.name || "Equipo A")}</button><button class="finish-button" data-action="finish" data-team-id="${match.team_b?.id || ""}" ${!hasTeams || match.status === "finished" ? "disabled" : ""}>${App.escapeHtml(match.team_b?.name || "Equipo B")}</button></div>`;
        clockOffset = Date.now() / 1000 - (Number(state.server_time) || Date.now() / 1000);
        updateClock();
    }

    function updateClock() {
        const match = displayMatch();
        const clock = document.getElementById("admin-clock");
        if (!match || !clock) return;
        let elapsed = Number(match.timer_elapsed) || 0;
        if (match.status === "live" && match.timer_started_at) {
            elapsed += Math.max(0, Date.now() / 1000 - clockOffset - Number(match.timer_started_at));
        }
        clock.textContent = App.formatElapsed(elapsed);
    }

    function render() {
        clockOffset = Date.now() / 1000 - (Number(state.server_time) || Date.now() / 1000);
        renderSelector();
        renderAdminMatch();
    }

    function setState(nextState) {
        state = nextState;
        render();
    }

    async function refresh() {
        try {
            const response = await App.requestJSON("/api/admin/state");
            setState(response);
            await flushQueue();
        } catch (error) {
            if (error.status === 401) window.location.href = "/acceso";
        }
    }

    function currentElapsed(match) {
        let elapsed = Number(match.timer_elapsed) || 0;
        if (match.status === "live" && match.timer_started_at) {
            elapsed += Math.max(0, Date.now() / 1000 - clockOffset - Number(match.timer_started_at));
        }
        return Math.floor(elapsed);
    }

    async function sendPoint(point) {
        try {
            const response = await App.post(`/api/admin/matches/${point.match_id}/point`, point);
            await removeQueuedPoint(point.event_id);
            setState(response.state);
            return true;
        } catch (error) {
            if (error.network || [502, 503, 504].includes(error.status)) return false;
            await removeQueuedPoint(point.event_id);
            throw error;
        }
    }

    async function handlePoint(teamId) {
        const match = displayMatch();
        if (!match || match.status !== "live") {
            App.showToast("Primero hay que iniciar el partido.", "is-error");
            return;
        }
        const point = {
            event_id: makeId(),
            match_id: match.id,
            team_id: Number(teamId),
            elapsed_seconds: currentElapsed(match),
        };
        if (navigator.onLine) {
            try {
                if (await sendPoint(point)) {
                    App.showToast("Punto registrado");
                    return;
                }
            } catch (error) {
                App.showToast(error.message, "is-error");
                return;
            }
        }
        await saveQueuedPoint(point);
        render();
        App.showToast("Sin conexión: el punto queda guardado en este móvil.", "is-offline");
    }

    async function flushQueue() {
        if (!navigator.onLine || !pendingPoints.length) return;
        for (const point of [...pendingPoints]) {
            try {
                const sent = await sendPoint(point);
                if (!sent) break;
            } catch (error) {
                App.showToast(error.message, "is-error");
            }
        }
        render();
    }

    async function controlAction(action, button) {
        const match = selectedMatch();
        if (!match) return;
        try {
            if (action === "point") return await handlePoint(button.dataset.teamId);
            if (action === "start" || action === "pause") {
                const response = await App.post(`/api/admin/matches/${match.id}/${action}`);
                setState(response.state);
                App.showToast(action === "pause" ? "Reloj pausado" : "Partido en marcha");
            }
            if (action === "undo") {
                if (!window.confirm("¿Deshacer el último punto?")) return;
                const pending = pendingFor(match.id);
                if (pending.length) {
                    await removeQueuedPoint(pending[pending.length - 1].event_id);
                    render();
                    App.showToast("Punto local eliminado");
                    return;
                }
                const response = await App.post(`/api/admin/matches/${match.id}/undo`);
                setState(response.state);
                App.showToast("Último punto deshecho");
            }
            if (action === "finish") {
                if (!window.confirm("¿Finalizar el partido con este ganador?")) return;
                const response = await App.post(`/api/admin/matches/${match.id}/finish`, { winner_team_id: Number(button.dataset.teamId) });
                setState(response.state);
                App.showToast("Partido finalizado");
            }
        } catch (error) {
            App.showToast(error.message, "is-error");
        }
    }

    setupForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const teams = teamInput.value.split(/\n|,/).map((item) => item.trim()).filter(Boolean);
        if (state.tournament && !window.confirm("Esto creará un nuevo torneo. ¿Continuar?")) return;
        const message = document.getElementById("setup-message");
        message.textContent = "Creando el cuadro…";
        try {
            const response = await App.post("/api/admin/tournaments", {
                name: document.getElementById("tournament-name").value,
                teams,
                stages: getStagePayload(),
            });
            setState(response.state);
            message.textContent = "Cuadro creado. Ya puedes elegir el siguiente partido.";
            App.showToast("Torneo preparado");
        } catch (error) {
            message.textContent = error.message;
            App.showToast(error.message, "is-error");
        }
    });

    teamInput.addEventListener("input", renderStageFields);
    matchSelector.addEventListener("change", () => {
        selectedMatchId = Number(matchSelector.value) || null;
        renderAdminMatch();
    });
    controlActions.addEventListener("click", (event) => {
        const button = event.target.closest("button[data-action]");
        if (button) controlAction(button.dataset.action, button);
    });

    hydrateForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        const match = selectedMatch();
        if (!match) {
            App.showToast("Selecciona un partido primero.", "is-error");
            return;
        }
        const message = document.getElementById("hydrate-message");
        try {
            const response = await App.post(`/api/admin/matches/${match.id}/hydrate`, {
                current_set: Number(document.getElementById("hydrate-set").value),
                sets_a: Number(document.getElementById("hydrate-sets-a").value),
                sets_b: Number(document.getElementById("hydrate-sets-b").value),
                score_a: Number(document.getElementById("hydrate-score-a").value),
                score_b: Number(document.getElementById("hydrate-score-b").value),
                elapsed_seconds: Number(document.getElementById("hydrate-time").value),
                status: document.getElementById("hydrate-status").value,
            });
            setState(response.state);
            message.textContent = "Estado guardado. El partido puede continuar desde aquí.";
            App.showToast("Marcador cargado");
        } catch (error) {
            message.textContent = error.message;
            App.showToast(error.message, "is-error");
        }
    });

    document.getElementById("logout-button").addEventListener("click", async () => {
        try { await App.post("/api/logout"); } finally { window.location.href = "/"; }
    });

    window.addEventListener("online", () => {
        App.showToast("Conexión recuperada");
        flushQueue();
        refresh();
    });
    window.addEventListener("offline", () => App.showToast("Sin conexión: los puntos se guardarán aquí.", "is-offline"));

    renderStageFields();
    loadQueue().then((points) => {
        pendingPoints = points;
        render();
        flushQueue();
    });
    render();
    window.setInterval(updateClock, 1000);
    App.connectEvents(refresh);
    window.setInterval(refresh, 12000);
    App.installServiceWorker();
})();
