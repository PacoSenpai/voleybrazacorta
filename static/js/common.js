(function () {
    "use strict";

    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

    function escapeHtml(value) {
        return String(value ?? "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");
    }

    function formatElapsed(seconds) {
        const safe = Math.max(0, Number(seconds) || 0);
        const minutes = Math.floor(safe / 60).toString().padStart(2, "0");
        const remainder = Math.floor(safe % 60).toString().padStart(2, "0");
        return `${minutes}:${remainder}`;
    }

    async function requestJSON(url, options = {}) {
        const headers = new Headers(options.headers || {});
        headers.set("Accept", "application/json");
        if (options.body && typeof options.body !== "string") {
            headers.set("Content-Type", "application/json");
            options.body = JSON.stringify(options.body);
        }
        if (["POST", "PUT", "PATCH", "DELETE"].includes((options.method || "GET").toUpperCase())) {
            headers.set("X-CSRF-Token", csrfToken);
        }
        let response;
        try {
            response = await fetch(url, { ...options, headers });
        } catch (error) {
            error.network = true;
            throw error;
        }
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            const error = new Error(data.error || "No se ha podido completar la acción.");
            error.status = response.status;
            throw error;
        }
        return data;
    }

    function post(url, body = {}) {
        return requestJSON(url, { method: "POST", body });
    }

    function showToast(message, tone = "") {
        const toast = document.getElementById("toast");
        if (!toast) return;
        toast.textContent = message;
        toast.className = `toast is-visible ${tone}`;
        window.clearTimeout(showToast.timeout);
        showToast.timeout = window.setTimeout(() => {
            toast.className = "toast";
        }, 3600);
    }

    function connectEvents(onChange) {
        if (!window.EventSource) return null;
        const source = new EventSource("/api/stream");
        source.addEventListener("state", () => onChange());
        source.onerror = () => {
            // EventSource reconnects itself; the next successful state event refreshes the UI.
        };
        return source;
    }

    function teamScore(match, side) {
        const current = (match.sets || []).find((item) => item.set_number === match.current_set);
        return current ? current[`score_${side}`] : 0;
    }

    function statusLabel(status) {
        return {
            live: "EN JUEGO",
            paused: "PAUSADO",
            scheduled: "PENDIENTE",
            finished: "FINALIZADO",
            bye: "PASA DIRECTO",
            void: "SIN CRUCE",
        }[status] || status;
    }

    function statusText(status) {
        return {
            live: "En juego",
            paused: "Pausado",
            scheduled: "Pendiente",
            finished: "Finalizado",
            bye: "Pasa directo",
            void: "Sin cruce",
        }[status] || status;
    }

    function installServiceWorker() {
        if ("serviceWorker" in navigator) {
            window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
        }
    }

    window.App = {
        escapeHtml,
        formatElapsed,
        requestJSON,
        post,
        showToast,
        connectEvents,
        teamScore,
        statusLabel,
        statusText,
        installServiceWorker,
    };
})();
