/* ZyleBot AI Spectator Arena — loaded ONLY by templates/game_arena.html.
 * Vanilla JS. No dependencies, no build step.
 *
 * Runs several independent copies of the exported Breakout DQN at once. Each
 * grid tile is a same-origin iframe of the board-only /game?embed=1&ai=1&
 * spectator=1&slot=N; POP OUT re-hosts a slot in a real browser window. The
 * arena never reaches into iframe-private state — spectator games publish
 * ready / run-end / offline via postMessage, and the arena only reads
 * /api/game-agent/status for the active policy. All stats here are three-life
 * browser (viewer) runs, NOT the trainer's single-life eval_score_mean.
 */
(() => {
    "use strict";

    const shell = document.querySelector(".arena-shell");
    const grid = document.getElementById("arena-grid");
    const countGroup = document.getElementById("arena-count");
    const startAllBtn = document.getElementById("start-all");
    const stopAllBtn = document.getElementById("stop-all");
    const popOutBtn = document.getElementById("pop-out");
    const policyState = document.getElementById("policy-state");
    const policySteps = document.getElementById("policy-steps");
    const policyEval = document.getElementById("policy-eval");
    const statEls = {
        runs: document.getElementById("stat-runs"),
        clears: document.getElementById("stat-clears"),
        clearpct: document.getElementById("stat-clearpct"),
        mean: document.getElementById("stat-mean"),
        high: document.getElementById("stat-high"),
    };

    const EMBED_SRC = shell.dataset.embedSrc || "/game?embed=1&ai=1&spectator=1";
    const MAX_GAMES = Math.max(1, Number(shell.dataset.maxGames) || 6);
    const DEFAULT_GAMES = clampCount(Number(shell.dataset.defaultGames) || 4);
    const STATUS_POLL_MS = 2000;

    function clampCount(value) {
        const n = Math.floor(Number(value));
        return Number.isFinite(n) ? Math.max(1, Math.min(n, MAX_GAMES)) : 1;
    }

    function spectatorUrl(slot) {
        return `${EMBED_SRC}&slot=${slot}`;
    }

    function windowName(slot) {
        return `zylebot-arena-slot-${slot}`;
    }

    // --- Slot / grid model ---------------------------------------------------
    const state = { count: DEFAULT_GAMES };
    const tiles = new Map();     // slot -> {el, iframe, statusEl, noteEl, popped}
    const popouts = new Map();   // slot -> Window (persists across grid rebuilds)
    const stats = { runs: 0, clears: 0, scoreSum: 0, scoreMax: 0 };

    function createTile(slot) {
        const el = document.createElement("div");
        el.className = "arena-tile";
        el.dataset.slot = String(slot);

        const bar = document.createElement("div");
        bar.className = "tile-bar";
        const slotLabel = document.createElement("span");
        slotLabel.className = "tile-slot";
        slotLabel.textContent = `SLOT ${String(slot).padStart(2, "0")}`;
        const statusEl = document.createElement("span");
        statusEl.className = "tile-status";
        statusEl.dataset.state = "offline";
        statusEl.textContent = "OFFLINE";
        bar.append(slotLabel, statusEl);

        const frame = document.createElement("div");
        frame.className = "tile-frame";
        const iframe = document.createElement("iframe");
        iframe.className = "tile-iframe";
        iframe.title = `Spectator game ${slot}`;
        iframe.setAttribute("scrolling", "no");
        const noteEl = document.createElement("div");
        noteEl.className = "tile-note";
        noteEl.hidden = true;
        frame.append(iframe, noteEl);

        el.append(bar, frame);
        return { el, iframe, statusEl, noteEl, popped: false };
    }

    function setStatus(slot, label, stateKey) {
        const tile = tiles.get(slot);
        if (!tile) return;
        tile.statusEl.textContent = label;
        tile.statusEl.dataset.state = stateKey;
    }

    function showFrame(slot) {
        const tile = tiles.get(slot);
        if (!tile) return;
        tile.noteEl.hidden = true;
        tile.iframe.hidden = false;
    }

    function showNote(slot, text) {
        const tile = tiles.get(slot);
        if (!tile) return;
        tile.noteEl.textContent = text;
        tile.noteEl.hidden = false;
        tile.iframe.hidden = true;
    }

    function blankSlot(slot) {
        const tile = tiles.get(slot);
        if (tile) tile.iframe.src = "about:blank";
    }

    // Reload deterministically: blank first, then (next tick) point at the URL,
    // so a same-URL restart actually re-navigates and re-launches.
    function loadSlot(slot) {
        const tile = tiles.get(slot);
        if (!tile) return;
        tile.popped = false;
        showFrame(slot);
        setStatus(slot, "STARTING…", "starting");
        tile.iframe.src = "about:blank";
        setTimeout(() => {
            const current = tiles.get(slot);
            if (current === tile) current.iframe.src = spectatorUrl(slot);
        }, 0);
    }

    function buildGrid(count) {
        // Drop pop-outs for slots that no longer exist.
        for (const slot of [...popouts.keys()]) {
            if (slot > count) {
                const win = popouts.get(slot);
                if (win && !win.closed) win.close();
                popouts.delete(slot);
            }
        }
        grid.replaceChildren();
        tiles.clear();
        grid.dataset.count = String(count);
        for (let slot = 1; slot <= count; slot++) {
            const tile = createTile(slot);
            tiles.set(slot, tile);
            grid.appendChild(tile.el);
            const win = popouts.get(slot);
            if (win && !win.closed) {
                tile.popped = true;
                blankSlot(slot);
                showNote(slot, "POPPED OUT ↗");
                setStatus(slot, "POPPED OUT", "popped");
            } else {
                popouts.delete(slot);
                loadSlot(slot);
            }
        }
    }

    function setCount(value) {
        const count = clampCount(value);
        state.count = count;
        for (const button of countGroup.querySelectorAll("button")) {
            button.setAttribute("aria-pressed", String(Number(button.dataset.count) === count));
        }
        buildGrid(count);
    }

    // --- Controls ------------------------------------------------------------
    function startAll() {
        for (let slot = 1; slot <= state.count; slot++) {
            const win = popouts.get(slot);
            if (win && !win.closed) {   // leave an open pop-out running
                win.focus();
                continue;
            }
            if (win) popouts.delete(slot);   // closed pop-out — recreate in grid
            loadSlot(slot);
        }
    }

    function stopAll() {
        for (let slot = 1; slot <= state.count; slot++) {
            const win = popouts.get(slot);
            if (win && !win.closed) win.close();
            popouts.delete(slot);
            const tile = tiles.get(slot);
            if (tile) tile.popped = false;
            blankSlot(slot);
            showNote(slot, "STOPPED");
            setStatus(slot, "OFFLINE", "offline");
        }
    }

    // Must run directly from the user's click — no delayed window.open calls.
    function popOut() {
        for (let slot = 1; slot <= state.count; slot++) {
            const existing = popouts.get(slot);
            if (existing && !existing.closed) {
                existing.focus();
                continue;
            }
            const win = window.open(spectatorUrl(slot), windowName(slot), "width=520,height=470");
            const tile = tiles.get(slot);
            if (win) {
                popouts.set(slot, win);
                if (tile) tile.popped = true;
                blankSlot(slot);   // stop the iframe instance — no double inference
                showNote(slot, "POPPED OUT ↗");
                setStatus(slot, "POPPED OUT", "popped");
            } else {
                // Blocked: the grid tile keeps running; surface it clearly.
                setStatus(slot, "POPUP BLOCKED", "blocked");
            }
        }
    }

    // --- Spectator event contract (postMessage) ------------------------------
    function isValidSlot(value) {
        return Number.isInteger(value) && value >= 1 && value <= 6;
    }

    function recordRun(score, cleared) {
        stats.runs += 1;
        if (cleared) stats.clears += 1;
        const numericScore = Number(score);
        const safeScore = Number.isFinite(numericScore) ? numericScore : 0;
        stats.scoreSum += safeScore;
        stats.scoreMax = Math.max(stats.scoreMax, safeScore);
        renderStats();
    }

    function renderStats() {
        statEls.runs.textContent = String(stats.runs);
        statEls.clears.textContent = String(stats.clears);
        statEls.clearpct.textContent = stats.runs
            ? `${Math.round((stats.clears / stats.runs) * 100)}%`
            : "—";
        statEls.mean.textContent = stats.runs
            ? String(Math.round(stats.scoreSum / stats.runs))
            : "—";
        statEls.high.textContent = stats.runs ? String(stats.scoreMax) : "—";
    }

    function onMessage(event) {
        if (event.origin !== location.origin) return;   // same-origin only
        const data = event.data;
        if (!data || typeof data !== "object" || typeof data.type !== "string") return;
        const slot = data.slot;
        if (!isValidSlot(slot)) return;

        if (data.type === "breakout-spectator-run-end") {
            recordRun(data.score, data.cleared === true);
        } else if (data.type === "breakout-spectator-ready") {
            const tile = tiles.get(slot);
            if (tile && !tile.popped) setStatus(slot, "RUNNING", "running");
        } else if (data.type === "breakout-spectator-offline") {
            const tile = tiles.get(slot);
            if (tile && !tile.popped) setStatus(slot, "OFFLINE", "offline");
        }
        // Every other message shape is ignored.
    }

    // --- Active-policy status (header) ---------------------------------------
    function setPolicyState(label, key) {
        policyState.textContent = label;
        policyState.className = `policy-state policy-state--${key}`;
    }

    async function pollStatus() {
        try {
            const response = await fetch("/api/game-agent/status", { cache: "no-store" });
            if (!response.ok) throw new Error(`status ${response.status}`);
            const data = await response.json();
            if (data.available) {
                setPolicyState("POLICY LOADED", "ok");
                policySteps.textContent = data.training_steps != null
                    ? `STEP ${Number(data.training_steps).toLocaleString()}`
                    : "STEP —";
                policyEval.textContent = data.eval_score != null
                    ? `EVAL ${Math.round(Number(data.eval_score))}`
                    : "EVAL —";
            } else {
                setPolicyState("NO POLICY", "warn");
                policySteps.textContent = "STEP —";
                policyEval.textContent = "EVAL —";
            }
        } catch {
            setPolicyState("SERVER OFFLINE", "error");
            policySteps.textContent = "STEP —";
            policyEval.textContent = "EVAL —";
        }
    }

    // --- Startup -------------------------------------------------------------
    function init() {
        countGroup.addEventListener("click", (event) => {
            const button = event.target.closest("button[data-count]");
            if (button) setCount(button.dataset.count);
        });
        startAllBtn.addEventListener("click", startAll);
        stopAllBtn.addEventListener("click", stopAll);
        popOutBtn.addEventListener("click", popOut);
        window.addEventListener("message", onMessage);

        renderStats();
        setCount(DEFAULT_GAMES);   // builds the grid and starts the games
        pollStatus();
        setInterval(pollStatus, STATUS_POLL_MS);
    }

    init();
})();
