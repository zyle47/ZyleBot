/* ZyleBot Live Evolution viewer — loaded ONLY by templates/game_evolution.html.
 * Vanilla JS, no dependencies, no build step.
 *
 * Six grid tiles are same-origin iframes of the board-only
 * /game?embed=1&ai=1&spectator=1&evo=1&slot=N. Each plays a DIFFERENT evolved
 * genome (the current generation's top 6, served by /ws/game-agent-evo). The page
 * itself never touches iframe-private state — it only polls /api/evo/status for
 * the run's generation, per-slot fitness, and the best/mean history it charts.
 * Boards stay full-length games; the chart races ahead at evolution speed.
 */
(() => {
    "use strict";

    const shell = document.querySelector(".evo-shell");
    const grid = document.getElementById("evo-grid");
    const restartBtn = document.getElementById("evo-restart");
    const stateEl = document.getElementById("evo-state");
    const hintEl = document.getElementById("evo-hint");
    const canvas = document.getElementById("evo-chart");
    const ctx = canvas.getContext("2d");
    const historyCanvas = document.getElementById("evo-history-chart");
    const historyCtx = historyCanvas.getContext("2d");
    const historyDataEl = document.getElementById("evo-history-data");
    const historyCountEl = document.getElementById("evo-history-count");
    const historyMilestonesEl = document.querySelector(".evo-milestones");
    const historyLedgerCountEl = document.getElementById("evo-history-ledger-count");
    const historyLedgerEl = document.getElementById("evo-history-ledger-list");
    let historicalPoints = [];
    let initialHistorySnapshot = {};
    let historicalSignature = "";
    try {
        initialHistorySnapshot = JSON.parse(historyDataEl.textContent);
    } catch {
        initialHistorySnapshot = {};
    }
    const hud = {
        gen: document.getElementById("evo-gen"),
        best: document.getElementById("evo-best"),
        baseline: document.getElementById("evo-baseline"),
        trainbest: document.getElementById("evo-trainbest"),
        mean: document.getElementById("evo-mean"),
        sigma: document.getElementById("evo-sigma"),
    };

    const EMBED_SRC = shell.dataset.embedSrc || "/game?embed=1&ai=1&spectator=1&evo=1";
    const MAX_SLOTS = Math.max(1, Math.min(6, Number(shell.dataset.slots) || 6));
    const STATUS_POLL_MS = 1500;
    const COLORS = { best: "#4de1ff", mean: "#8b93ff", line: "#ffc15e", grid: "rgba(120,150,200,0.14)" };
    const HISTORY_COLORS = {
        training: "#8b93ff",
        validation: "#4de1ff",
        champion: "#67ffb7",
        DQN: "#8b93ff",
        EVOLUTION: "#4de1ff",
        "CURRICULUM EVO": "#67ffb7",
        DEPLOYED: "#67ffb7",
    };

    const tiles = new Map();   // slot(1..N) -> {iframe, statusEl, fitnessEl, popped:false}
    let slotCount = 0;         // built once a run appears
    let started = false;
    let lastHistory = [];
    let lastBaseline = null;

    function boardUrl(slot) {
        return `${EMBED_SRC}&slot=${slot}`;
    }

    // --- Tiles ---------------------------------------------------------------
    function createTile(slot) {
        const el = document.createElement("div");
        el.className = "arena-tile evo-tile";
        el.dataset.slot = String(slot);

        const bar = document.createElement("div");
        bar.className = "tile-bar";
        const label = document.createElement("span");
        label.className = "tile-slot";
        label.textContent = slot === 1 ? "BRAIN 1 · TOP" : `BRAIN ${slot}`;
        const fitnessEl = document.createElement("span");
        fitnessEl.className = "evo-fitness";
        fitnessEl.textContent = "—";
        const statusEl = document.createElement("span");
        statusEl.className = "tile-status";
        statusEl.dataset.state = "offline";
        statusEl.textContent = "OFFLINE";
        bar.append(label, fitnessEl, statusEl);

        const frame = document.createElement("div");
        frame.className = "tile-frame";
        const iframe = document.createElement("iframe");
        iframe.className = "tile-iframe";
        iframe.title = `Evolving brain ${slot}`;
        iframe.setAttribute("scrolling", "no");
        frame.append(iframe);

        el.append(bar, frame);
        grid.appendChild(el);
        tiles.set(slot, { iframe, statusEl, fitnessEl });
    }

    function buildGrid(count) {
        grid.replaceChildren();
        tiles.clear();
        slotCount = Math.max(1, Math.min(MAX_SLOTS, count));
        grid.dataset.count = String(slotCount);
        for (let slot = 1; slot <= slotCount; slot++) createTile(slot);
    }

    // Blank first, then (next tick) point at the URL so a same-URL restart
    // actually re-navigates and re-launches the board.
    function loadSlot(slot) {
        const tile = tiles.get(slot);
        if (!tile) return;
        setStatus(slot, "STARTING…", "starting");
        tile.iframe.src = "about:blank";
        setTimeout(() => {
            const current = tiles.get(slot);
            if (current === tile) current.iframe.src = boardUrl(slot);
        }, 0);
    }

    function loadAll() {
        for (let slot = 1; slot <= slotCount; slot++) loadSlot(slot);
    }

    function setStatus(slot, label, stateKey) {
        const tile = tiles.get(slot);
        if (!tile) return;
        tile.statusEl.textContent = label;
        tile.statusEl.dataset.state = stateKey;
    }

    // --- Spectator postMessage contract (same as the arena) ------------------
    function onMessage(event) {
        if (event.origin !== location.origin) return;
        const data = event.data;
        if (!data || typeof data !== "object" || typeof data.type !== "string") return;
        const slot = data.slot;
        if (!Number.isInteger(slot) || slot < 1 || slot > slotCount) return;
        if (data.type === "breakout-spectator-ready") setStatus(slot, "RUNNING", "running");
        else if (data.type === "breakout-spectator-offline") setStatus(slot, "OFFLINE", "offline");
    }

    // --- Status polling ------------------------------------------------------
    const fmt = (v) => (v == null || Number.isNaN(Number(v)) ? "—" : Math.round(Number(v)).toLocaleString());

    function setState(label, key) {
        stateEl.textContent = label;
        stateEl.className = `policy-state policy-state--${key}`;
    }

    function applyStatus(data) {
        if (!data || !data.available) {
            setState("WAITING FOR RUN", "warn");
            hintEl.hidden = false;
            return;
        }
        hintEl.hidden = true;
        setState(`EVOLVING · GEN ${Number(data.generation).toLocaleString()}`, "ok");

        const slots = Array.isArray(data.slots) ? data.slots : [];
        if (!started) {
            buildGrid(slots.length || MAX_SLOTS);
            loadAll();
            started = true;
        }
        for (let slot = 1; slot <= slotCount; slot++) {
            const tile = tiles.get(slot);
            const info = slots[slot - 1];
            if (tile) tile.fitnessEl.textContent = info ? `★ ${fmt(info.fitness)}` : "—";
        }

        hud.gen.textContent = Number(data.generation).toLocaleString();
        hud.best.textContent = fmt(data.all_time_best);
        hud.baseline.textContent = fmt(data.baseline);
        hud.trainbest.textContent = fmt(data.train_best);
        hud.mean.textContent = fmt(data.train_mean);
        hud.sigma.textContent = data.sigma != null ? Number(data.sigma).toFixed(4) : "—";

        lastHistory = Array.isArray(data.history) ? data.history : [];
        lastBaseline = typeof data.baseline === "number" ? data.baseline : null;
        drawChart();
    }

    async function pollLive() {
        try {
            const response = await fetch("/api/evo/status", { cache: "no-store" });
            if (!response.ok) throw new Error(`status ${response.status}`);
            applyStatus(await response.json());
        } catch {
            setState("SERVER OFFLINE", "error");
        }
    }

    async function pollHistoricalHistory() {
        try {
            const response = await fetch("/api/evo/history", { cache: "no-store" });
            if (!response.ok) throw new Error(`history ${response.status}`);
            applyHistoricalHistory(await response.json());
        } catch {
            // Keep the last good server-rendered snapshot; live training status
            // remains independently available above.
        }
    }

    function poll() {
        pollLive();
        pollHistoricalHistory();
    }

    // --- Chart ---------------------------------------------------------------
    function drawChart() {
        const W = canvas.width;
        const H = canvas.height;
        const padL = 56, padR = 16, padT = 18, padB = 28;
        ctx.clearRect(0, 0, W, H);

        const hist = lastHistory;
        if (hist.length === 0) {
            ctx.fillStyle = "rgba(160,180,210,0.5)";
            ctx.font = "16px 'Courier New', monospace";
            ctx.textAlign = "center";
            ctx.fillText("waiting for the first generation…", W / 2, H / 2);
            return;
        }

        const gens = hist.map((h) => h.gen);
        const gMin = gens[0];
        const gMax = Math.max(gens[gens.length - 1], gMin + 1);
        let yMax = 10;
        for (const h of hist) yMax = Math.max(yMax, h.best || 0, h.mean || 0);
        if (lastBaseline != null) yMax = Math.max(yMax, lastBaseline);
        yMax *= 1.12;

        const x = (g) => padL + ((g - gMin) / (gMax - gMin)) * (W - padL - padR);
        const y = (v) => H - padB - (v / yMax) * (H - padT - padB);

        // gridlines + y labels
        ctx.strokeStyle = COLORS.grid;
        ctx.fillStyle = "rgba(160,180,210,0.6)";
        ctx.font = "12px 'Courier New', monospace";
        ctx.lineWidth = 1;
        ctx.textAlign = "right";
        for (let i = 0; i <= 4; i++) {
            const v = (yMax / 4) * i;
            const py = y(v);
            ctx.beginPath();
            ctx.moveTo(padL, py);
            ctx.lineTo(W - padR, py);
            ctx.stroke();
            ctx.fillText(Math.round(v).toLocaleString(), padL - 8, py + 4);
        }
        // x labels (first / last gen)
        ctx.textAlign = "center";
        ctx.fillText(`gen ${gMin}`, padL, H - 8);
        ctx.fillText(`gen ${gens[gens.length - 1]}`, W - padR, H - 8);

        // champion "line to beat"
        if (lastBaseline != null) {
            ctx.strokeStyle = COLORS.line;
            ctx.setLineDash([6, 6]);
            ctx.lineWidth = 1.5;
            ctx.beginPath();
            ctx.moveTo(padL, y(lastBaseline));
            ctx.lineTo(W - padR, y(lastBaseline));
            ctx.stroke();
            ctx.setLineDash([]);
        }

        const line = (key, color, width) => {
            ctx.strokeStyle = color;
            ctx.lineWidth = width;
            ctx.beginPath();
            hist.forEach((h, i) => {
                const px = x(h.gen);
                const py = y(h[key] || 0);
                if (i === 0) ctx.moveTo(px, py);
                else ctx.lineTo(px, py);
            });
            ctx.stroke();
        };
        line("mean", COLORS.mean, 1.5);
        line("best", COLORS.best, 2.5);

        // glow dot on the latest best
        const last = hist[hist.length - 1];
        ctx.fillStyle = COLORS.best;
        ctx.shadowColor = COLORS.best;
        ctx.shadowBlur = 12;
        ctx.beginPath();
        ctx.arc(x(last.gen), y(last.best || 0), 3.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;
    }

    // --- Audited champion history -------------------------------------------
    // The server assembles this feed from rl/runs*, so it grows while training
    // without coupling the browser to local filesystem details.
    const historicalScore = (value) => Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });

    function validHistoricalItems(items) {
        if (!Array.isArray(items)) return [];
        return items.filter((item) => Number.isFinite(Number(item.score))).map((item) => ({
            ...item,
            score: Number(item.score),
        }));
    }

    function textElement(tag, className, value) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        element.textContent = value == null ? "—" : String(value);
        return element;
    }

    function renderHistoricalChampions(champions) {
        historyMilestonesEl.replaceChildren();
        champions.forEach((champion, index) => {
            const item = document.createElement("li");
            item.className = "evo-milestone";
            item.dataset.score = String(champion.score);
            item.dataset.label = champion.label || "CHAMPION";

            const topline = document.createElement("div");
            topline.className = "evo-milestone-topline";
            topline.append(
                textElement("span", "evo-milestone-index", String(index + 1).padStart(2, "0")),
                textElement("span", "evo-milestone-date", champion.date || "LOCAL RUN"),
            );
            const gain = textElement("span", "evo-milestone-gain", "");
            gain.setAttribute("aria-label", "Improvement from previous champion");
            item.append(
                topline,
                textElement("strong", "", champion.label || "CHAMPION"),
                textElement("span", "evo-milestone-score", historicalScore(champion.score)),
                gain,
                textElement("span", "evo-milestone-detail", champion.detail || champion.measure),
                textElement("code", "", `run ${champion.run || champion.source || "local"}`),
            );
            historyMilestonesEl.appendChild(item);
        });
    }

    function renderHistoricalLedger(points) {
        historyLedgerEl.replaceChildren();
        points.forEach((point, index) => {
            const item = document.createElement("li");
            item.dataset.kind = point.kind || "validation";
            item.append(
                textElement("span", "", index + 1),
                textElement("strong", "", point.label || "MILESTONE"),
                textElement("b", "", historicalScore(point.score)),
                textElement("small", "", point.measure || "Recorded score"),
                textElement("code", "", point.source || point.run || "local run"),
            );
            historyLedgerEl.appendChild(item);
        });
    }

    function applyHistoricalHistory(data) {
        if (!data || typeof data !== "object") return;
        const points = validHistoricalItems(data.points);
        const champions = validHistoricalItems(data.champions);
        const signature = JSON.stringify([points, champions]);
        if (signature === historicalSignature) return;
        historicalSignature = signature;
        historicalPoints = points;
        const count = Number.isInteger(data.count) ? data.count : points.length;
        historyCountEl.textContent = `${count.toLocaleString()} RECORDED STOPS`;
        historyLedgerCountEl.textContent = count.toLocaleString();
        renderHistoricalChampions(champions);
        renderHistoricalLedger(points);
        labelHistoricalGains();
        drawHistoricalChart();
    }

    function drawHistoricalChart() {
        const W = historyCanvas.width;
        const H = historyCanvas.height;
        const padL = 70, padR = 70, padT = 58, padB = 54;
        historyCtx.clearRect(0, 0, W, H);

        if (historicalPoints.length === 0) return;

        const maxScore = Math.max(...historicalPoints.map((point) => point.score));
        const yMax = Math.ceil((maxScore * 1.18) / 250) * 250;
        const plotW = W - padL - padR;
        const plotH = H - padT - padB;
        const x = (index) => historicalPoints.length === 1
            ? W / 2
            : padL + (index / (historicalPoints.length - 1)) * plotW;
        const y = (value) => padT + (1 - value / yMax) * plotH;

        // Lightly band the three chronological phases before drawing the grid.
        const phaseGroups = [];
        historicalPoints.forEach((point, index) => {
            const current = phaseGroups[phaseGroups.length - 1];
            if (!current || current.phase !== point.phase) {
                phaseGroups.push({ phase: point.phase, start: index, end: index });
            } else {
                current.end = index;
            }
        });
        phaseGroups.forEach((group, index) => {
            const left = group.start === 0 ? padL : (x(group.start - 1) + x(group.start)) / 2;
            const right = group.end === historicalPoints.length - 1
                ? W - padR
                : (x(group.end) + x(group.end + 1)) / 2;
            historyCtx.fillStyle = index % 2 === 0 ? "rgba(139,147,255,0.025)" : "rgba(77,225,255,0.025)";
            historyCtx.fillRect(left, padT, right - left, plotH);
            historyCtx.fillStyle = HISTORY_COLORS[group.phase] || "rgba(160,180,210,0.8)";
            historyCtx.font = "bold 12px 'Courier New', monospace";
            historyCtx.textAlign = "center";
            historyCtx.fillText(group.phase, (left + right) / 2, 20);
        });

        historyCtx.font = "12px 'Courier New', monospace";
        historyCtx.lineWidth = 1;
        historyCtx.textAlign = "right";
        for (let i = 0; i <= 4; i++) {
            const value = (yMax / 4) * i;
            const py = y(value);
            historyCtx.strokeStyle = COLORS.grid;
            historyCtx.beginPath();
            historyCtx.moveTo(padL, py);
            historyCtx.lineTo(W - padR, py);
            historyCtx.stroke();
            historyCtx.fillStyle = "rgba(160,180,210,0.65)";
            historyCtx.fillText(Math.round(value).toLocaleString(), padL - 10, py + 4);
        }

        // Join consecutive measurements, tinting each segment by its phase.
        for (let index = 1; index < historicalPoints.length; index++) {
            const previous = historicalPoints[index - 1];
            const point = historicalPoints[index];
            historyCtx.beginPath();
            historyCtx.moveTo(x(index - 1), y(previous.score));
            historyCtx.lineTo(x(index), y(point.score));
            historyCtx.strokeStyle = HISTORY_COLORS[point.phase] || COLORS.best;
            historyCtx.globalAlpha = 0.62;
            historyCtx.lineWidth = point.kind === "champion" ? 3.5 : 2.5;
            historyCtx.stroke();
        }
        historyCtx.globalAlpha = 1;

        historicalPoints.forEach((point, index) => {
            const px = x(index);
            const py = y(point.score);
            historyCtx.beginPath();
            if (point.kind === "champion") {
                historyCtx.moveTo(px, py - 9);
                historyCtx.lineTo(px + 9, py);
                historyCtx.lineTo(px, py + 9);
                historyCtx.lineTo(px - 9, py);
                historyCtx.closePath();
            } else {
                historyCtx.arc(px, py, point.kind === "training" ? 5 : 4, 0, Math.PI * 2);
            }
            historyCtx.fillStyle = HISTORY_COLORS[point.kind] || COLORS.best;
            historyCtx.shadowColor = historyCtx.fillStyle;
            historyCtx.shadowBlur = point.kind === "champion" ? 14 : 7;
            historyCtx.fill();
            historyCtx.shadowBlur = 0;
            historyCtx.lineWidth = point.kind === "champion" ? 3 : 1.5;
            historyCtx.strokeStyle = "rgba(7,13,22,0.9)";
            historyCtx.stroke();

            if (point.highlight) {
                const labelY = point.kind === "champion" ? py + 29 : py - 18;
                historyCtx.textAlign = "center";
                historyCtx.fillStyle = "rgba(231,247,255,0.96)";
                historyCtx.font = "bold 17px 'Courier New', monospace";
                historyCtx.fillText(
                    point.score.toLocaleString(undefined, { maximumFractionDigits: 2 }),
                    px,
                    labelY,
                );
            }
        });

        historyCtx.fillStyle = "rgba(160,180,210,0.7)";
        historyCtx.font = "12px 'Courier New', monospace";
        historyCtx.textAlign = "left";
        historyCtx.fillText("START", padL, H - 17);
        historyCtx.textAlign = "right";
        historyCtx.fillText("LATEST RECORD", W - padR, H - 17);
    }

    function labelHistoricalGains() {
        const championMilestones = Array.from(document.querySelectorAll(".evo-milestone")).map((el) => ({
            el,
            score: Number(el.dataset.score),
        })).filter((point) => Number.isFinite(point.score));
        championMilestones.forEach((point, index) => {
            const target = point.el.querySelector(".evo-milestone-gain");
            if (!target) return;
            if (index === 0) {
                target.textContent = "ORIGIN";
                return;
            }
            const previous = championMilestones[index - 1].score;
            const gain = ((point.score - previous) / previous) * 100;
            target.textContent = `+${gain.toFixed(1)}%`;
        });
    }

    // --- Startup -------------------------------------------------------------
    function init() {
        restartBtn.addEventListener("click", () => { if (started) loadAll(); });
        window.addEventListener("message", onMessage);
        drawChart();
        applyHistoricalHistory(initialHistorySnapshot);
        poll();
        setInterval(poll, STATUS_POLL_MS);
    }

    init();
})();
