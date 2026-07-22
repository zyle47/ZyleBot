/* ZyleBot Breakout — loaded ONLY by templates/game.html (never the chat shell).
 * Vanilla JS + canvas 2D. No dependencies, no CDNs, no build step.
 *
 * Level 2 introduces durable bricks, a Piercer, and a randomly placed Splitter
 * (multiball); level 3 draws ZYLE on a 20x12 grid with a Piercer in Y's gap and
 * a Splitter in E's; level 4 is a compact DAJA-CHAN tribute in neon pixel art.
 *
 * OUT OF SCOPE — do NOT add: power-ups beyond the Piercer and Splitter,
 * particles / screen shake / trails, touch controls, gamepad, extra lives at
 * score thresholds, paddle shrink, volume slider (mute toggle only), pause menu.
 */
(() => {
    "use strict";

    // --- Constants (decided at planning — don't tweak without asking) -------
    const W = 800, H = 600;           // logical playfield; ALL game logic uses these coords
    const PHYSICS_STEP = 1 / 120;     // fixed-timestep physics (s). At <= 640 px/s the ball
                                      // moves <= 5.4 px/step (< ball radius): no tunneling,
                                      // simple overlap tests suffice — no swept collision.
    const MAX_FRAME_DT = 0.25;        // clamp rAF catch-up after a hidden tab (s)
    const BRICK_ROW_COLORS = ["pink", "pink", "amber", "amber", "green", "green"];
    const LEVEL_ONE_ROW_POINTS = [70, 70, 50, 50, 30, 30];
    const BRICK_TOP = 60, BRICK_SIDE = 20, BRICK_GAP = 6, BRICK_H = 22; // wall layout, logical px
    const PADDLE_W = 110, PADDLE_H = 14, PADDLE_Y = H - 40;
    const BALL_R = 7;
    const PADDLE_SPEED = 520;         // px/s (keyboard; mouse sets x directly, last input wins)
    const BALL_BASE_SPEED = 340;      // px/s at level 1
    const SPEED_PER_BRICK = 5, SPEED_PER_LEVEL = 40, MAX_SPEED = 640;
    const MAX_DEFLECT = Math.PI / 3;  // 60° max deflection from vertical off the paddle edge
    const MIN_VY_FRAC = 0.25;         // enforce |vy| >= 0.25 * speed everywhere (kills flat rallies)
    const START_LIVES = 3;
    const PIERCER_HITS = 5, PIERCER_DURATION = 10; // active gameplay seconds
    const SPLITTER_HITS = 4, SPLIT_SPREAD = 0.55; // multiball fork: ±rad around the ball's heading
    const MUTED_KEY = "zylebot.breakout.muted"; // localStorage

    // 0 = empty, 1 = regular, 2 = hard, 3 = Ultima, 4 = Splitter (multiball),
    // 5 = Piercer. Level 2's Splitter is not in the mask — buildBricks converts
    // one random non-Piercer brick each run. The mosaic stays sparse enough to
    // be a fair introduction before the denser ZYLE challenge.
    const MOSAIC_PATTERN = [
        "1001002002001001",
        "0100201001020010",
        "0010020220200100",
        "0001202332021000",
        "0022033523302200",
        "0001202332021000",
        "0010020220200100",
        "0100201001020010",
        "1001002002001001",
    ];
    const ZYLE_GLYPHS = [
        ["1111", "0001", "0010", "0010", "0100", "0100", "1000", "1111"],
        ["1001", "1001", "1001", "0110", "0110", "0110", "0110", "0110"],
        ["1000", "1000", "1000", "1000", "1000", "1000", "1000", "1111"],
        ["1111", "1000", "1000", "1110", "1000", "1000", "1000", "1111"],
    ];
    const ZYLE_STRENGTHS = [3, 2, 2, 3];
    const ZYLE_PATTERN = [
        "0".repeat(20),
        "0".repeat(20),
        ...Array.from({ length: 8 }, (_, row) => (
            ZYLE_GLYPHS.map((glyph, letter) => (
                glyph[row].replaceAll("1", String(ZYLE_STRENGTHS[letter]))
            )).join("0") + "0"
        )),
        "0".repeat(20),
        "0".repeat(20),
    ].map((line, row) => {
        const cells = [...line];
        if (row === 3) cells[6] = "5";  // open center of Y
        if (row === 7) cells[17] = "4"; // open center of E: Splitter (multiball)
        return cells.join("");
    });
    const TRIBUTE_COLS = 48, TRIBUTE_ROWS = 24;
    const DAJA_CHAN_PATTERN = (() => {
        const grid = Array.from({ length: TRIBUTE_ROWS }, () => Array(TRIBUTE_COLS).fill("Y"));
        const font = {
            D: ["1110", "1001", "1001", "1001", "1110"],
            A: ["0110", "1001", "1111", "1001", "1001"],
            J: ["0011", "0001", "0001", "1001", "0110"],
            "-": ["0000", "0000", "1111", "0000", "0000"],
            C: ["0111", "1000", "1000", "1000", "0111"],
            H: ["1001", "1001", "1111", "1001", "1001"],
            N: ["1001", "1101", "1101", "1011", "1001"],
        };
        let titleX = 2;
        for (const letter of "DAJA-CHAN") {
            const glyph = font[letter];
            glyph.forEach((line, row) => {
                [...line].forEach((pixel, col) => {
                    if (pixel === "1") grid[row][titleX + col] = "R";
                });
            });
            titleX += glyph[0].length + 1;
        }

        const heart = [
            "011000110",
            "111101111",
            "111111111",
            "011111110",
            "001111100",
            "000111000",
            "000010000",
        ];
        heart.forEach((line, row) => {
            [...line].forEach((pixel, col) => {
                if (pixel === "1") grid[8 + row][1 + col] = "R";
            });
        });

        // Daja's portrait, redrawn square from her photo (cell pitch ~15.9x15,
        // so art proportions render true). 24x19 block anchored at row 5,
        // col 12; "." keeps the yellow background.
        // K black coat · B brown brindle · E green eyes · C cream/white
        const catArt = [
            ".......KK............KK.......",  // pointed ear tips, yellow gap between
            "......BKKKK........KKKKB......",  // ears widen, brown outer fringe
            "......BKKKKK......KKKKKB......",
            "......KKKKKKK....KKKKKKK......",  // ears meet the head
            "......KKKBKKKKKKKKKKBKKK......",  // dome closes, brindle ticks
            "......KKKKKKKKBBKKKKKKKK......",  // forehead, tan blaze begins
            "......KBKKKKKKBBKKKKKKBK......",  // temple brindle
            "......KKKEEEEKBBKEEEEKKK......",  // round eyes, blaze between
            "......KKKEKKEKKKKEKKEKKK......",  // 2-wide round pupils
            "......KBKEEEEKKKKEEEEKBK......",  // eye bottoms, cheek ticks
            "......KBBKKKKKBBKKKKKBBK......",  // cheeks brindle, brown nose bridge
            "......KBBKKKCCKKCCKKKBBK......",  // dark nose framed by cream muzzle
            ".CCCCCKKKKKKKCKKCKKKKKKKCCCCC.",  // mouth starts under nose; long upper whiskers
            "......KBKKKKCCKKCCKKKKBK......",  // mouth line splits the white chin
            "CCCCC..KKKKCCKKKKCCKKKK..CCCCC",  // dark chin shadow; lower whiskers, further out
            "........KKKCCCKKCCCKKK........",  // mouth tail fades into the bib
            ".........KKBCCCCCCBKK.........",  // orange streaks frame the bib
            "..........KBCCCCCCBK..........",
            "...........BCCCCCCB...........",  // chest bib bottom
        ];
        const catX = 9, catY = 5;
        catArt.forEach((line, row) => {
            [...line].forEach((cell, col) => {
                if (cell !== ".") grid[catY + row][catX + col] = cell;
            });
        });
        return grid.map((row) => row.join(""));
    })();
    const LEVEL_LAYOUTS = [
        { cols: 10, rows: 6, pattern: null },
        { cols: 16, rows: MOSAIC_PATTERN.length, pattern: MOSAIC_PATTERN, splitter: true },
        { cols: 20, rows: ZYLE_PATTERN.length, pattern: ZYLE_PATTERN },
        {
            cols: TRIBUTE_COLS,
            rows: DAJA_CHAN_PATTERN.length,
            pattern: DAJA_CHAN_PATTERN,
            gap: 3,
            brickH: 12,
            top: 36,
        },
    ];

    // --- DOM contract (ids live in game.html — keep the two in sync) --------
    const stage = document.querySelector(".game-stage");
    const canvas = document.getElementById("game-canvas");
    const ctx = canvas.getContext("2d");
    const hud = {
        score: document.getElementById("hud-score"),
        level: document.getElementById("hud-level"),
        lives: document.getElementById("hud-lives"),
        mute: document.getElementById("mute-btn"),
        ai: document.getElementById("ai-btn"),
        aiBadge: document.getElementById("ai-badge"),
    };
    const panels = {
        attract: document.getElementById("panel-attract"),
        paused: document.getElementById("panel-paused"),
        levelclear: document.getElementById("panel-levelclear"),
        gameover: document.getElementById("panel-gameover"),
    };
    const scoreRows = document.getElementById("score-rows");
    const scoreForm = document.getElementById("score-form");
    const initialsInput = document.getElementById("initials-input");
    const skipBtn = document.getElementById("skip-btn");
    const finalScoreEl = document.getElementById("final-score");
    const defaultInitials = document.getElementById("game-root").dataset.initials || "ZYL";

    // --- Theme (read the app's CSS vars once; canvas can't use var()) --------
    const cssVars = getComputedStyle(document.documentElement);
    const themeVar = (name, fallback) => cssVars.getPropertyValue(name).trim() || fallback;
    const THEME = {
        bg: themeVar("--bg-deep", "#020403"),
        fg: themeVar("--fg", "#e9fff7"),
        muted: themeVar("--muted", "#829991"),
        green: themeVar("--green", "#67ffb7"),
        greenBright: themeVar("--green-bright", "#a5ffd2"),
        pink: themeVar("--pink", "#ff3cac"),
        amber: themeVar("--amber", "#ffd166"),
        danger: themeVar("--danger", "#ff5d7d"),
        tributeRed: "#ff2747",
        tributeYellow: "#ffd900",
        tributeBlack: "#101312",
        tributeBrown: "#a85d35",
        tributeEye: "#62ff72",
        tributeCream: "#fff0b8",
    };

    // --- Canvas sizing, DPR-correct (FINAL — reference pattern) --------------
    // Logic stays in logical 800x600 coords; only the backing store scales.
    // Re-read devicePixelRatio on every call: `resize` also fires when the
    // window crosses monitors with different DPI on Windows 11.
    let scale = 1;
    function resizeCanvas() {
        const dpr = window.devicePixelRatio || 1;
        const cssW = stage.clientWidth;
        canvas.width = Math.round(cssW * dpr);
        canvas.height = Math.round(cssW * (H / W) * dpr);
        scale = canvas.width / W;
        render(); // repaint immediately so a resize never shows a blank frame
    }
    // Mouse -> logical x mapping (use inside the mousemove handler):
    //   const rect = stage.getBoundingClientRect();
    //   const logicalX = (e.clientX - rect.left) / rect.width * W;

    // --- Game state ----------------------------------------------------------
    const State = {
        ATTRACT: "attract",       // idle; top-10 visible; Enter/click -> READY
        READY: "ready",           // ball glued to paddle; Space/click launches
        PLAYING: "playing",
        PAUSED: "paused",         // Space/Esc toggles back
        LIFE_LOST: "life_lost",   // brief beat, then -> READY (or GAME_OVER at 0 lives)
        LEVEL_CLEAR: "levelclear",
        GAME_OVER: "gameover",    // submit/skip score, then -> ATTRACT
    };
    let state = State.ATTRACT;

    // Mutable game data. buildBricks() fills `bricks`; resetBall() places the ball.
    const game = {
        score: 0,
        level: 1,
        lives: START_LIVES,
        paddle: { x: W / 2, w: PADDLE_W },      // x = center
        balls: [{ x: 0, y: 0, vx: 0, vy: 0 }],  // live balls (Splitter forks to 3); velocity px/s
        bricks: [],                              // AABBs with hits/maxHits durability
        bricksAlive: 0,
        speed: BALL_BASE_SPEED,                  // current ball speed magnitude
        pierceRemaining: 0,                      // gameplay seconds; bricks don't reflect ball
        input: { left: false, right: false },
    };
    const ai = {
        enabled: false,
        socket: null,
        awaitingReply: false,
        frame: 0,
    };

    // --- Spectator mode (arena tiles / pop-outs) -----------------------------
    // Query contract: ?embed=1&ai=1&spectator=1&slot=N. `ai=1` connects the AI
    // and auto-starts level 1; `spectator=1` (honored only with ai=1) adds the
    // unattended viewer rules: silent, no score POST, loop level 1 forever, and
    // publish ready/run-end/offline events to the arena via postMessage. Plain
    // /game (no params) is untouched: spectator.active stays false everywhere.
    const params = new URLSearchParams(location.search);
    const clampSlot = (value) => {
        const n = Math.floor(Number(value));
        return Number.isFinite(n) ? Math.max(1, Math.min(n, 6)) : 1;
    };
    const aiRequested = params.get("ai") === "1";
    // Evolution mode reuses every spectator rule; only the action source differs:
    // the board plays an evolved genome slot (0-based) instead of the champion.
    const evoMode = aiRequested && params.get("spectator") === "1" && params.get("evo") === "1";
    const spectator = {
        active: aiRequested && params.get("spectator") === "1",
        slot: clampSlot(params.get("slot")),
        // Pop-outs report to window.opener; iframes to window.parent.
        target: window.opener || (window.parent !== window ? window.parent : null),
        runReported: false,
        restartTimer: null,
    };

    function postToArena(message) {
        if (!spectator.active || !spectator.target) return;
        try {
            spectator.target.postMessage(message, location.origin);
        } catch {
            /* target window gone — nothing to report to */
        }
    }

    function reportRun(cleared) {
        // Count a run once even if a state transition and a timer both fire.
        if (!spectator.active || spectator.runReported) return;
        spectator.runReported = true;
        postToArena({
            type: "breakout-spectator-run-end",
            slot: spectator.slot,
            score: game.score,
            cleared,
            reason: cleared ? "level-cleared" : "game-over",
        });
    }

    function scheduleSpectatorRestart() {
        if (!spectator.active || spectator.restartTimer !== null) return;
        // Hold on the GAME OVER / LEVEL CLEAR panel so the result is visible,
        // then loop level 1 — only while the AI is still online (offline stops
        // the loop; we never auto-reconnect).
        spectator.restartTimer = setTimeout(() => {
            spectator.restartTimer = null;
            if (spectator.active && ai.enabled) startGame(1);
        }, 5000);
    }

    function cancelSpectatorRestart() {
        if (spectator.restartTimer !== null) {
            clearTimeout(spectator.restartTimer);
            spectator.restartTimer = null;
        }
    }

    function setState(next) {
        state = next;
        for (const [name, panel] of Object.entries(panels)) {
            panel.hidden = name !== next;
        }
        if (next === State.ATTRACT) {
            // Tidy dim backdrop behind the panel: fresh wall, centered paddle,
            // ball glued (instead of an empty field / wreckage of the last run).
            game.paddle.x = W / 2;
            game.pierceRemaining = 0;
            buildBricks();
            resetBall();
            fetchScores();
        } else if (next === State.GAME_OVER) {
            finalScoreEl.textContent = `SCORE ${String(game.score).padStart(6, "0")}`;
            if (spectator.active) {
                // Report the run, show the panel briefly, then loop level 1.
                // No initials focus, no score POST.
                reportRun(false);
                scheduleSpectatorRestart();
            } else {
                initialsInput.value = defaultInitials;
                initialsInput.focus();
            }
        }
        if (next === State.READY && ai.enabled) {
            setTimeout(() => {
                if (state === State.READY && ai.enabled) launch();
            }, 0);
        }
    }

    function buildBricks() {
        const layout = LEVEL_LAYOUTS[Math.min(game.level, LEVEL_LAYOUTS.length) - 1];
        const gap = layout.gap ?? BRICK_GAP;
        const brickH = layout.brickH ?? BRICK_H;
        const top = layout.top ?? BRICK_TOP;
        const brickW = (W - 2 * BRICK_SIDE - (layout.cols - 1) * gap) / layout.cols;
        game.bricks = [];
        for (let row = 0; row < layout.rows; row++) {
            for (let col = 0; col < layout.cols; col++) {
                const cell = layout.pattern ? layout.pattern[row][col] : "1";
                if (cell === "0") continue;
                const numericHits = Number(cell);
                const maxHits = Number.isInteger(numericHits) ? numericHits : 1;
                const rowPoints = game.level === 1
                    ? LEVEL_ONE_ROW_POINTS[row]
                    : 30 + (layout.rows - row) * 5 + (maxHits - 1) * 20;
                game.bricks.push({
                    x: BRICK_SIDE + col * (brickW + gap),
                    y: top + row * (brickH + gap),
                    w: brickW,
                    h: brickH,
                    row,
                    col,
                    alive: true,
                    hits: maxHits,
                    maxHits,
                    piercer: maxHits === PIERCER_HITS,
                    splitter: maxHits === SPLITTER_HITS,
                    art: Number.isInteger(numericHits) ? null : cell,
                    points: rowPoints,
                });
            }
        }
        game.bricksAlive = game.bricks.length;
        if (layout.splitter) {
            // One random non-Piercer brick becomes the Splitter (multiball).
            const candidates = game.bricks.filter((brick) => !brick.piercer);
            const chosen = candidates[Math.floor(Math.random() * candidates.length)];
            chosen.hits = SPLITTER_HITS;
            chosen.maxHits = SPLITTER_HITS;
            chosen.splitter = true;
            chosen.points = 30 + (layout.rows - chosen.row) * 5 + (SPLITTER_HITS - 1) * 20;
        }
    }

    function resetBall() {
        game.balls = [{ x: game.paddle.x, y: PADDLE_Y - BALL_R, vx: 0, vy: 0 }];
    }

    function movePaddle(dt) {
        const direction = Number(game.input.right) - Number(game.input.left);
        game.paddle.x += direction * PADDLE_SPEED * dt;
        game.paddle.x = Math.max(game.paddle.w / 2, Math.min(game.paddle.x, W - game.paddle.w / 2));
    }

    function updateHud() {
        hud.score.textContent = `SCORE ${String(game.score).padStart(6, "0")}`;
        hud.level.textContent = `LVL ${game.level}`;
        hud.lives.textContent = `LIVES ${"●".repeat(game.lives)}`;
    }

    function startGame(startLevel = 1) {
        if (spectator.active) spectator.runReported = false;  // fresh run
        game.score = 0;
        game.level = startLevel;
        game.lives = START_LIVES;
        // Same speed as arriving at this level naturally.
        game.speed = Math.min(BALL_BASE_SPEED + (startLevel - 1) * SPEED_PER_LEVEL, MAX_SPEED);
        game.pierceRemaining = 0;
        game.paddle.x = W / 2;
        game.input.left = false;
        game.input.right = false;
        buildBricks();
        resetBall();
        updateHud();
        setState(State.READY);
    }

    function launch() {
        const angle = (Math.random() - 0.5) * (Math.PI / 6);
        game.balls = [{
            x: game.paddle.x,
            y: PADDLE_Y - BALL_R,
            vx: Math.sin(angle) * game.speed,
            vy: -Math.cos(angle) * game.speed,
        }];
        setState(State.PLAYING);
    }

    // --- Input ---------------------------------------------------------------
    function bindInput() {
        const spaceAction = () => {
            if (state === State.ATTRACT || state === State.GAME_OVER) {
                startGame();
            } else if (state === State.READY || state === State.LEVEL_CLEAR) {
                launch();
            } else if (state === State.PLAYING) {
                setState(State.PAUSED);
            } else if (state === State.PAUSED) {
                setState(State.PLAYING);
            }
        };

        window.addEventListener("keydown", (e) => {
            ensureAudio();
            if (e.target.tagName === "INPUT") return;
            if (spectator.active) return;  // spectator tiles are AI-only

            if (["ArrowLeft", "ArrowRight", "Space", "Escape"].includes(e.code)) {
                e.preventDefault();
            }
            if (e.code === "ArrowLeft") game.input.left = true;
            if (e.code === "ArrowRight") game.input.right = true;
            if (e.repeat) return;

            if (e.code === "Space") {
                spaceAction();
            } else if (e.code === "KeyA" && state === State.ATTRACT) {
                toggleAi();
            } else if (e.code === "Escape") {
                if (state === State.PLAYING) setState(State.PAUSED);
                else if (state === State.PAUSED) setState(State.PLAYING);
            } else if (e.code === "Enter") {
                if (state === State.ATTRACT || state === State.GAME_OVER) startGame();
            } else if (state === State.ATTRACT && /^(Digit|Numpad)[1-9]$/.test(e.code)) {
                // Dev shortcut: jump straight to a level from the attract screen.
                const lvl = Number(e.code.slice(-1));
                if (lvl <= LEVEL_LAYOUTS.length) startGame(lvl);
            }
        });
        window.addEventListener("keyup", (e) => {
            if (e.target.tagName === "INPUT") return;
            if (spectator.active) return;  // spectator tiles are AI-only
            if (["ArrowLeft", "ArrowRight", "Space", "Escape"].includes(e.code)) {
                e.preventDefault();
            }
            if (e.code === "ArrowLeft") game.input.left = false;
            if (e.code === "ArrowRight") game.input.right = false;
        });
        document.addEventListener("pointerdown", ensureAudio);
        stage.addEventListener("mousemove", (e) => {
            if (spectator.active) return;  // hovering a tile must not steer the AI
            const rect = stage.getBoundingClientRect();
            const logicalX = (e.clientX - rect.left) / rect.width * W;
            game.paddle.x = Math.max(game.paddle.w / 2, Math.min(logicalX, W - game.paddle.w / 2));
            if (state === State.READY || state === State.LEVEL_CLEAR) resetBall();
        });
        stage.addEventListener("click", (e) => {
            if (spectator.active) return;  // clicking a tile must not start/relaunch
            if (e.target.closest("#score-form")) return;
            // GAME_OVER deliberately absent: a stray click must not restart and
            // lose an unsubmitted score — leave via SUBMIT, SKIP, or Space/Enter.
            if (state === State.ATTRACT) startGame();
            else if (state === State.READY || state === State.LEVEL_CLEAR) launch();
        });
        document.addEventListener("visibilitychange", () => {
            if (document.hidden && state === State.PLAYING) setState(State.PAUSED);
        });
    }

    // --- AI policy bridge (30 Hz browser state -> numpy policy action) -------
    function updateAiControl() {
        hud.ai.textContent = ai.enabled ? "AI: ON" : "AI: OFF";
        hud.ai.setAttribute("aria-pressed", String(ai.enabled));
    }

    function disableAi(offline = false) {
        ai.enabled = false;
        ai.awaitingReply = false;
        game.input.left = false;
        game.input.right = false;
        const socket = ai.socket;
        ai.socket = null;
        if (socket && socket.readyState < WebSocket.CLOSING) socket.close(1000);
        hud.aiBadge.hidden = !offline;
        if (spectator.active && offline) {
            // Stop the loop and tell the arena; do NOT auto-reconnect (the arena
            // or the user recovers via START ALL — no reconnect storm).
            cancelSpectatorRestart();
            postToArena({ type: "breakout-spectator-offline", slot: spectator.slot });
        }
        updateAiControl();
    }

    function enableAi() {
        ai.enabled = true;
        ai.awaitingReply = false;
        ai.frame = 0;
        hud.aiBadge.hidden = true;
        updateAiControl();
        const scheme = location.protocol === "https:" ? "wss" : "ws";
        const wsPath = evoMode
            ? `/ws/game-agent-evo?slot=${spectator.slot - 1}`
            : "/ws/game-agent";
        const socket = new WebSocket(`${scheme}://${location.host}${wsPath}`);
        ai.socket = socket;
        socket.addEventListener("open", () => {
            if (spectator.active) {
                postToArena({ type: "breakout-spectator-ready", slot: spectator.slot });
            }
            if (ai.enabled && state === State.ATTRACT) startGame(1);
        });
        socket.addEventListener("message", (event) => {
            if (!ai.enabled || socket !== ai.socket) return;
            ai.awaitingReply = false;
            let message;
            try {
                message = JSON.parse(event.data);
            } catch {
                disableAi(true);
                return;
            }
            if (message.error) {
                disableAi(true);
                return;
            }
            const action = Number(message.action);
            game.input.left = action === 1;
            game.input.right = action === 2;
        });
        socket.addEventListener("error", () => disableAi(true));
        socket.addEventListener("close", () => {
            if (ai.enabled && socket === ai.socket) disableAi(true);
        });
    }

    function toggleAi() {
        if (ai.enabled) disableAi(false);
        else enableAi();
        hud.ai.blur();
    }

    function streamAiState() {
        if (!ai.enabled || state !== State.PLAYING || ai.awaitingReply) return;
        if (!ai.socket || ai.socket.readyState !== WebSocket.OPEN) return;
        ai.frame++;
        if (ai.frame % 2 !== 0) return;
        ai.awaitingReply = true;
        ai.socket.send(JSON.stringify({
            paddle_x: game.paddle.x,
            balls: game.balls
                .filter((ball) => !ball.dead)
                .map((ball) => [ball.x, ball.y, ball.vx, ball.vy]),
            bricks: game.bricks.map((brick) => brick.alive ? brick.hits : 0).join(""),
            speed: game.speed,
            pierce: game.pierceRemaining,
        }));
    }

    function bindAi() {
        updateAiControl();
        hud.ai.addEventListener("click", toggleAi);
    }

    // --- Physics (runs at PHYSICS_STEP; called from the rAF loop) ------------
    function stepPhysics(dt) {
        const oldScore = game.score;
        const oldLevel = game.level;
        const oldLives = game.lives;
        const updateHud = () => {
            if (game.score !== oldScore) {
                hud.score.textContent = `SCORE ${String(game.score).padStart(6, "0")}`;
            }
            if (game.level !== oldLevel) hud.level.textContent = `LVL ${game.level}`;
            if (game.lives !== oldLives) hud.lives.textContent = `LIVES ${"●".repeat(game.lives)}`;
        };
        const enforceBounce = (ball) => {
            const magnitude = Math.hypot(ball.vx, ball.vy) || game.speed;
            const vxSign = ball.vx < 0 ? -1 : 1;
            const vySign = ball.vy < 0 ? -1 : 1;
            let absVy = Math.abs(ball.vy) / magnitude * game.speed;
            absVy = Math.max(absVy, MIN_VY_FRAC * game.speed);
            absVy = Math.min(absVy, game.speed);
            ball.vy = vySign * absVy;
            ball.vx = vxSign * Math.sqrt(Math.max(0, game.speed ** 2 - absVy ** 2));
        };

        movePaddle(dt);
        game.pierceRemaining = Math.max(0, game.pierceRemaining - dt);

        const spawned = [];
        for (const ball of game.balls) {
            ball.x += ball.vx * dt;
            ball.y += ball.vy * dt;

            if (ball.x - BALL_R < 0) {
                ball.x = BALL_R;
                ball.vx = Math.abs(ball.vx);
                enforceBounce(ball);
                playSfx("wall");
            } else if (ball.x + BALL_R > W) {
                ball.x = W - BALL_R;
                ball.vx = -Math.abs(ball.vx);
                enforceBounce(ball);
                playSfx("wall");
            }
            if (ball.y - BALL_R < 0) {
                ball.y = BALL_R;
                ball.vy = Math.abs(ball.vy);
                enforceBounce(ball);
                playSfx("wall");
            }

            if (ball.y - BALL_R > H) {
                // Drained. With multiball the life resolves only once ALL are gone.
                ball.dead = true;
                continue;
            }

            const paddleLeft = game.paddle.x - game.paddle.w / 2;
            const paddleRight = game.paddle.x + game.paddle.w / 2;
            if (
                ball.vy > 0 &&
                ball.x + BALL_R >= paddleLeft &&
                ball.x - BALL_R <= paddleRight &&
                ball.y + BALL_R >= PADDLE_Y &&
                ball.y - BALL_R <= PADDLE_Y + PADDLE_H
            ) {
                ball.y = PADDLE_Y - BALL_R;
                const offset = Math.max(-1, Math.min(
                    (ball.x - game.paddle.x) / (game.paddle.w / 2),
                    1,
                ));
                const angle = offset * MAX_DEFLECT;
                ball.vx = Math.sin(angle) * game.speed;
                ball.vy = -Math.cos(angle) * game.speed;
                enforceBounce(ball);
                playSfx("paddle");
            }

            for (const brick of game.bricks) {
                if (!brick.alive) continue;
                const closestX = Math.max(brick.x, Math.min(ball.x, brick.x + brick.w));
                const closestY = Math.max(brick.y, Math.min(ball.y, brick.y + brick.h));
                const dx = ball.x - closestX;
                const dy = ball.y - closestY;
                if (dx * dx + dy * dy > BALL_R * BALL_R) continue;

                const piercing = game.pierceRemaining > 0;
                if (!piercing) {
                    const overlapX = Math.min(ball.x + BALL_R, brick.x + brick.w)
                        - Math.max(ball.x - BALL_R, brick.x);
                    const overlapY = Math.min(ball.y + BALL_R, brick.y + brick.h)
                        - Math.max(ball.y - BALL_R, brick.y);
                    if (overlapX < overlapY) {
                        if (ball.x < brick.x + brick.w / 2) {
                            ball.x = brick.x - BALL_R;
                            ball.vx = -Math.abs(ball.vx);
                        } else {
                            ball.x = brick.x + brick.w + BALL_R;
                            ball.vx = Math.abs(ball.vx);
                        }
                    } else if (ball.y < brick.y + brick.h / 2) {
                        ball.y = brick.y - BALL_R;
                        ball.vy = -Math.abs(ball.vy);
                    } else {
                        ball.y = brick.y + brick.h + BALL_R;
                        ball.vy = Math.abs(ball.vy);
                    }
                }

                const hitsTaken = piercing ? brick.hits : 1;
                brick.hits -= hitsTaken;
                game.score += brick.points * hitsTaken;
                if (brick.hits === 0) {
                    brick.alive = false;
                    game.bricksAlive--;
                    game.speed = Math.min(game.speed + SPEED_PER_BRICK, MAX_SPEED);
                }
                if (brick.piercer && !brick.alive) {
                    game.pierceRemaining = PIERCER_DURATION;
                    playSfx("power");
                } else if (brick.splitter && !brick.alive) {
                    // Fork this ball into three, fanned around its current heading.
                    for (const spread of [-SPLIT_SPREAD, SPLIT_SPREAD]) {
                        const heading = Math.atan2(ball.vx, -ball.vy) + spread;
                        spawned.push({
                            x: ball.x,
                            y: ball.y,
                            vx: Math.sin(heading) * game.speed,
                            vy: -Math.cos(heading) * game.speed,
                        });
                    }
                    playSfx("split");
                } else {
                    const sfxRow = Math.min(brick.row + (brick.maxHits - brick.hits) * 2, 11);
                    playSfx("brick", sfxRow);
                }
                if (piercing) {
                    const magnitude = Math.hypot(ball.vx, ball.vy) || game.speed;
                    ball.vx = ball.vx / magnitude * game.speed;
                    ball.vy = ball.vy / magnitude * game.speed;
                } else {
                    enforceBounce(ball);
                }

                if (!brick.alive && game.bricksAlive === 0) {
                    if (spectator.active) {
                        // v1 policy is level-1-only: count a win and loop level 1
                        // instead of building level 2.
                        playSfx("levelclear");
                        reportRun(true);
                        setState(State.LEVEL_CLEAR);
                        resetBall();
                        scheduleSpectatorRestart();
                        return;
                    }
                    setState(State.LEVEL_CLEAR);
                    game.level++;
                    game.speed = Math.min(
                        BALL_BASE_SPEED + (game.level - 1) * SPEED_PER_LEVEL,
                        MAX_SPEED,
                    );
                    buildBricks();
                    resetBall();
                    playSfx("levelclear");
                    updateHud();
                    return; // resetBall replaced game.balls — skip the survivor filter
                }
                break;
            }
        }

        game.balls = game.balls.filter((ball) => !ball.dead).concat(spawned);

        if (game.balls.length === 0) {
            game.lives--;
            updateHud();
            if (game.lives === 0) {
                playSfx("gameover");
                setState(State.GAME_OVER);
            } else {
                playSfx("life");
                setState(State.LIFE_LOST);
                setTimeout(() => {
                    if (state === State.LIFE_LOST) {
                        resetBall();
                        setState(State.READY);
                    }
                }, 650);
            }
            return;
        }
        updateHud();
    }

    // --- Main loop (fixed-timestep accumulator over rAF) ----------------------
    function startLoop() {
        let previous = performance.now();
        let accumulator = 0;
        const frame = (now) => {
            const elapsed = Math.min((now - previous) / 1000, MAX_FRAME_DT);
            previous = now;
            if (state === State.PLAYING) {
                accumulator += elapsed;
                while (accumulator >= PHYSICS_STEP && state === State.PLAYING) {
                    stepPhysics(PHYSICS_STEP);
                    accumulator -= PHYSICS_STEP;
                }
            } else {
                if (state === State.READY || state === State.LEVEL_CLEAR) {
                    movePaddle(elapsed); // arrows work while the ball is glued
                    resetBall();
                }
                accumulator = 0;
            }
            streamAiState();
            render();
            requestAnimationFrame(frame);
        };
        requestAnimationFrame(frame);
    }

    // --- Rendering (neon; HUD + panels are DOM, canvas draws playfield only) --
    function render() {
        ctx.setTransform(scale, 0, 0, scale, 0, 0);
        ctx.fillStyle = THEME.bg;
        ctx.fillRect(0, 0, W, H);

        const fieldAlpha = state === State.ATTRACT ? 0.25 : 1;
        const drawBrick = (brick, color, pipColor = THEME.greenBright) => {
            ctx.globalAlpha = fieldAlpha * (0.5 + 0.5 * brick.hits / brick.maxHits);
            ctx.fillStyle = color;
            ctx.fillRect(brick.x, brick.y, brick.w, brick.h);
            if (brick.maxHits > 1) {
                const pipW = 4;
                const pipGap = 3;
                const totalW = brick.hits * pipW + (brick.hits - 1) * pipGap;
                const pipX = brick.x + (brick.w - totalW) / 2;
                ctx.globalAlpha = fieldAlpha;
                ctx.fillStyle = pipColor;
                for (let pip = 0; pip < brick.hits; pip++) {
                    ctx.fillRect(pipX + pip * (pipW + pipGap), brick.y + brick.h - 5, pipW, 2);
                }
            }
        };

        const maxRow = game.bricks.reduce((highest, brick) => Math.max(highest, brick.row), 0);
        for (let row = 0; row <= maxRow; row++) {
            const color = THEME[BRICK_ROW_COLORS[row % BRICK_ROW_COLORS.length]];
            ctx.shadowColor = color;
            ctx.shadowBlur = 14;
            for (const brick of game.bricks) {
                if (brick.alive && brick.maxHits === 1 && !brick.art && brick.row === row) {
                    drawBrick(brick, color);
                }
            }
        }
        ctx.shadowColor = THEME.amber;
        ctx.shadowBlur = 14;
        for (const brick of game.bricks) {
            if (brick.alive && brick.maxHits === 2) drawBrick(brick, THEME.amber);
        }
        ctx.shadowColor = THEME.danger;
        ctx.shadowBlur = 18;
        for (const brick of game.bricks) {
            if (brick.alive && brick.maxHits === 3) drawBrick(brick, THEME.danger);
        }
        ctx.shadowColor = THEME.pink;
        ctx.shadowBlur = 22;
        for (const brick of game.bricks) {
            if (!brick.alive || !brick.splitter) continue;
            drawBrick(brick, THEME.pink, "#ffffff");
            // Three dots telegraph the 3-ball split.
            ctx.globalAlpha = fieldAlpha;
            ctx.fillStyle = "#ffffff";
            for (const off of [0.25, 0.5, 0.75]) {
                ctx.beginPath();
                ctx.arc(brick.x + brick.w * off, brick.y + 4, 1.8, 0, Math.PI * 2);
                ctx.fill();
            }
        }
        const artColors = {
            R: THEME.tributeRed,
            Y: THEME.tributeYellow,
            K: THEME.tributeBlack,
            B: THEME.tributeBrown,
            E: THEME.tributeEye,
            C: THEME.tributeCream,
        };
        const artGlows = { K: "#5c455d", B: "#d6844f" };
        for (const [art, color] of Object.entries(artColors)) {
            ctx.shadowColor = artGlows[art] || color;
            ctx.shadowBlur = art === "Y" ? 8 : 18;
            for (const brick of game.bricks) {
                if (brick.alive && brick.art === art) drawBrick(brick, color);
            }
        }
        for (const brick of game.bricks) {
            if (!brick.alive || !brick.piercer) continue;
            const silver = ctx.createLinearGradient(brick.x, brick.y, brick.x + brick.w, brick.y);
            silver.addColorStop(0, THEME.muted);
            silver.addColorStop(0.28, THEME.fg);
            silver.addColorStop(0.48, "#ffffff");
            silver.addColorStop(0.68, THEME.fg);
            silver.addColorStop(1, THEME.muted);
            ctx.shadowColor = THEME.fg;
            ctx.shadowBlur = 24;
            drawBrick(brick, silver, THEME.fg);
            ctx.globalAlpha = fieldAlpha;
            ctx.strokeStyle = "#ffffff";
            ctx.lineWidth = 1.5;
            ctx.strokeRect(brick.x + 0.75, brick.y + 0.75, brick.w - 1.5, brick.h - 1.5);
            ctx.fillStyle = "rgba(255, 255, 255, 0.85)";
            ctx.fillRect(brick.x + brick.w * 0.16, brick.y + 3, brick.w * 0.42, 2);
        }
        ctx.shadowBlur = 0;
        ctx.globalAlpha = fieldAlpha;

        ctx.fillStyle = THEME.pink;
        ctx.shadowColor = THEME.pink;
        ctx.shadowBlur = 14;
        ctx.fillRect(game.paddle.x - game.paddle.w / 2, PADDLE_Y, game.paddle.w, PADDLE_H);

        const piercing = game.pierceRemaining > 0;
        for (const ball of game.balls) {
            const ballX = state === State.READY ? game.paddle.x : ball.x;
            const ballY = state === State.READY ? PADDLE_Y - BALL_R : ball.y;
            ctx.fillStyle = piercing ? THEME.fg : THEME.green;
            ctx.shadowColor = piercing ? THEME.fg : THEME.green;
            ctx.shadowBlur = piercing ? 24 : 14;
            ctx.beginPath();
            ctx.arc(ballX, ballY, BALL_R, 0, Math.PI * 2);
            ctx.fill();
            ctx.shadowBlur = 0;
            ctx.fillStyle = piercing ? "#ffffff" : THEME.greenBright;
            ctx.beginPath();
            ctx.arc(ballX, ballY, BALL_R * 0.4, 0, Math.PI * 2);
            ctx.fill();
            if (piercing) {
                ctx.strokeStyle = THEME.fg;
                ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.arc(ballX, ballY, BALL_R + 4, 0, Math.PI * 2);
                ctx.stroke();
            }
        }
        ctx.globalAlpha = 1; // ATTRACT dims the whole playfield, not just bricks
    }

    // --- Audio (WebAudio, synthesized — no audio files) -----------------------
    let audioCtx = null;
    let masterGain = null;

    function ensureAudio() {
        if (!audioCtx) {
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            if (!AudioContext) return;
            audioCtx = new AudioContext();
            masterGain = audioCtx.createGain();
            masterGain.gain.value = hud.mute.getAttribute("aria-pressed") === "true" ? 0 : 1;
            masterGain.connect(audioCtx.destination);
        }
        if (audioCtx.state === "suspended") audioCtx.resume();
    }

    function beep(opts) {
        if (!audioCtx || !masterGain) return;
        const {
            freq,
            dur = 0.08,
            type = "square",
            vol = 0.18,
            slideTo = null,
            delay = 0,
        } = opts;
        const oscillator = audioCtx.createOscillator();
        const noteGain = audioCtx.createGain();
        const start = audioCtx.currentTime + delay;
        oscillator.type = type;
        oscillator.frequency.setValueAtTime(freq, start);
        if (slideTo !== null) {
            oscillator.frequency.exponentialRampToValueAtTime(slideTo, start + dur);
        }
        noteGain.gain.setValueAtTime(0.0001, start);
        noteGain.gain.linearRampToValueAtTime(vol, start + 0.005);
        noteGain.gain.exponentialRampToValueAtTime(0.0001, start + dur);
        oscillator.connect(noteGain);
        noteGain.connect(masterGain);
        oscillator.start(start);
        oscillator.stop(start + dur + 0.05);
    }

    function playSfx(name, row = 0) {
        if (!audioCtx) return;
        if (name === "paddle") {
            beep({ freq: 220, dur: 0.06 });
        } else if (name === "wall") {
            beep({ freq: 160, dur: 0.045, type: "triangle" });
        } else if (name === "brick") {
            beep({ freq: 330 + row * 45, dur: 0.07, type: "triangle" });
        } else if (name === "power") {
            [523, 659, 784, 1047].forEach((freq, index) => {
                beep({ freq, dur: 0.1, type: "triangle", delay: index * 0.06 });
            });
        } else if (name === "split") {
            [392, 494, 587].forEach((freq, index) => {
                beep({ freq, dur: 0.06, delay: index * 0.05 });
            });
        } else if (name === "life") {
            beep({ freq: 320, dur: 0.35, type: "sawtooth", slideTo: 70 });
        } else if (name === "gameover") {
            [330, 220, 147].forEach((freq, index) => {
                beep({ freq, dur: 0.14, delay: index * 0.15 });
            });
        } else if (name === "levelclear") {
            [262, 330, 392, 523].forEach((freq, index) => {
                beep({ freq, dur: 0.09, type: "triangle", delay: index * 0.07 });
            });
        }
    }

    function bindMute() {
        // Spectator tiles are forced silent and must NOT touch the shared mute
        // preference in localStorage (that belongs to the real /game).
        let muted = spectator.active ? true : localStorage.getItem(MUTED_KEY) === "true";
        const updateMute = () => {
            hud.mute.textContent = muted ? "SOUND: OFF" : "SOUND: ON";
            hud.mute.setAttribute("aria-pressed", String(muted));
            if (masterGain && audioCtx) {
                masterGain.gain.setValueAtTime(muted ? 0 : 1, audioCtx.currentTime);
            }
        };
        updateMute();
        hud.mute.addEventListener("click", () => {
            muted = !muted;
            if (!spectator.active) localStorage.setItem(MUTED_KEY, String(muted));
            updateMute();
            hud.mute.blur();
        });
    }

    // --- High scores (API: GET/POST /api/scores — see app/main.py) -----------
    // GET  -> {"scores": [{initials, score, level, created_at}, ...]}
    // POST {"initials", "score", "level"} -> {"entry": {...}, "top": [...]}
    //      (re-render from "top"; saves a second fetch)
    async function fetchScores() {
        if (scoreRows.dataset.fresh === "true") {
            delete scoreRows.dataset.fresh;
            return;
        }
        try {
            const response = await fetch("/api/scores");
            if (!response.ok) throw new Error(`Score request failed: ${response.status}`);
            const data = await response.json();
            renderScores(data.scores);
        } catch {
            renderScores([]);
        }
    }

    function renderScores(scores) {
        scoreRows.replaceChildren();
        if (!scores.length) {
            const empty = document.createElement("li");
            empty.className = "score-empty";
            empty.textContent = "NO SCORES YET";
            scoreRows.appendChild(empty);
            return;
        }
        scores.forEach((entry, index) => {
            const row = document.createElement("li");
            const values = [
                ["score-rank", String(index + 1).padStart(2, "0")],
                ["score-initials", entry.initials],
                ["score-value", String(entry.score).padStart(6, "0")],
                ["score-level", `LVL ${entry.level}`],
            ];
            for (const [className, value] of values) {
                const cell = document.createElement("span");
                cell.className = className;
                cell.textContent = value;
                row.appendChild(cell);
            }
            scoreRows.appendChild(row);
        });
    }

    function bindScoreForm() {
        const submitBtn = scoreForm.querySelector('button[type="submit"]');
        scoreForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            if (spectator.active) return;  // spectators never POST a score
            if (submitBtn.disabled) return;
            submitBtn.disabled = true;
            try {
                const response = await fetch("/api/scores", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        initials: initialsInput.value.trim() || defaultInitials,
                        score: game.score,
                        level: game.level,
                    }),
                });
                if (!response.ok) throw new Error(`Score submission failed: ${response.status}`);
                const data = await response.json();
                renderScores(data.top);
                scoreRows.dataset.fresh = "true";
                setState(State.ATTRACT);
            } finally {
                submitBtn.disabled = false;
            }
        });
        skipBtn.addEventListener("click", () => setState(State.ATTRACT));
    }

    // --- Startup --------------------------------------------------------------
    function init() {
        resizeCanvas();
        window.addEventListener("resize", resizeCanvas);
        bindInput();
        bindMute();
        bindAi();
        bindScoreForm();
        startLoop();
        setState(State.ATTRACT);
        // ?ai=1 connects the AI and auto-starts level 1 (arena tiles / pop-outs).
        if (aiRequested) enableAi();
    }

    init();
})();
