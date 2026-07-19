# Breakout for ZyleBot — Implementation Plan

## Context

Nemanja wants a small JS game inside ZyleBot for fun. Decisions made together: **Breakout** (neon paddle/ball/bricks — fits the dark cyberpunk theme), living on a **dedicated `/game` page** (clean isolation; `app.js` hard-crashes without the chat DOM, so a separate page structurally avoids regressions), with three v1 extras: **SQLite high scores**, **synthesized WebAudio SFX** (no audio files), and an **agent tie-in** (a SAFE `get_game_scores` tool so ZyleBot can answer "what's my breakout high score?" in chat). No build step, no frameworks, no CDNs — everything stays local and vanilla, matching the repo.

All code-level assumptions below were verified against the working tree (template block names, `@tool` signature, db helper style, footer structure).

## Files

| File | Action | Purpose |
|---|---|---|
| `app/pages.py` | edit | add `/game` route |
| `app/templates/game.html` | new | game page, extends `page.html` |
| `app/static/game.css` | new (~150 lines) | stage, HUD, overlays, score table |
| `app/static/game.js` | new (~650 lines, cap 800) | the entire game |
| `app/db.py` | edit | `scores` DDL + 2 helpers |
| `app/models.py` | edit | `ScoreSubmit` DTO |
| `app/main.py` | edit | `GET/POST /api/scores` |
| `app/tools/game_tools.py` | new | SAFE `get_game_scores` tool |
| `app/tools/__init__.py` | edit | one registration import line |
| `app/templates/_footer.html` | edit | `/game` link in Resources column |
| `README.md` | edit | tool table row + game mention |
| `HANDOFF.md` | edit (orchestrator, at end) | record what landed |

**Zero changes** to `app.js`, `style.css`, `index.html`, `config.py`, `.env`/`.env.example` (no new config keys → the three-way sync rule isn't triggered).

## 1. Page + routing

**`app/pages.py`** — new section after About pages, mirroring existing handlers; needs `from app.config import settings` (not currently imported there):

```python
# --- Game -------------------------------------------------------------------

@router.get("/game")
async def game(request: Request):
    # Arcade initials default from USER_NAME ("Nemanja" -> "NEM"); USER_NAME defaults to "" so fall back to "ZYL".
    initials = "".join(c for c in settings.user_name.upper() if c.isalnum())[:3] or "ZYL"
    return templates.TemplateResponse(request, "game.html", {"default_initials": initials})
```

**`app/templates/game.html`** extends **`page.html`** (not `base.html`): it already provides brand header, "back to console" link, footer, and `body_class=page` (which restores scrolling that `style.css`'s `body{overflow:hidden}` removes), and it never loads `app.js`. Verified blocks: `page_content`, `head_extra` (needs `{{ super() }}` to keep `pages.css`), `title`/`scripts` from `base.html`.

```jinja
{% extends "page.html" %}
{% block title %}Breakout — ZyleBot{% endblock %}
{% block head_extra %}{{ super() }}
    <link rel="stylesheet" href="/static/game.css">
{% endblock %}
{% block page_content %}
    <div id="game-root" data-initials="{{ default_initials }}">
        <div class="game-hud">
            <span id="hud-score">SCORE 000000</span>
            <span id="hud-level">LVL 1</span>
            <span id="hud-lives">LIVES ●●●</span>
            <button id="mute-btn" type="button" aria-pressed="false">SOUND: ON</button>
        </div>
        <div class="game-stage">
            <canvas id="game-canvas" width="800" height="600"></canvas>
            <div id="game-overlay"><!-- attract / pause / game-over panels, JS-toggled --></div>
        </div>
        <p class="game-help">mouse or ◄ ► to move · Space launches / pauses · Esc pauses</p>
    </div>
{% endblock %}
{% block scripts %}
    <script src="/static/game.js"></script>
{% endblock %}
```

**`game.css`**: `.game-stage { position:relative; aspect-ratio:4/3; max-width:800px; border:1px solid var(--border); box-shadow: var(--shadow-green); }`, canvas `display:block; width:100%; height:100%`, absolutely-positioned `#game-overlay` panels in `--font-mono`, HUD row, top-10 table, initials input (`text-transform:uppercase`). Reuse existing `:root` vars from `style.css` — no new colors.

**Link**: `_footer.html` Resources column (lines 24–35) — add `<a href="/game">Breakout</a>` after the README link (line 26). Footer is shared, so this covers the chat shell and all content pages. Do **not** touch the crowded `index.html` chat header.

## 2. `game.js` architecture (single IIFE, `"use strict"`)

Ordered sections, implementable top-to-bottom:

1. **Constants + theme.** Logical playfield `W=800, H=600`. Read theme once via `getComputedStyle(document.documentElement).getPropertyValue("--green")` etc. with hex fallbacks. Bricks: 10×6, rows top-down colored `[pink, pink, amber, amber, green, green]`, points `[70,70,50,50,30,30]`. Paddle 110×14, ball r=7. Ball base 340 px/s, +5/brick, +40/level, cap 640; paddle 520 px/s.
2. **DPR-correct sizing.** Logic in logical coords; `resizeCanvas()` sets `canvas.width = stageWidth × devicePixelRatio` (re-read DPR every call — Windows multi-monitor DPI changes fire `resize`), `ctx.setTransform(scale,0,0,scale,0,0)` per frame. Mouse maps via `(e.clientX - rect.left)/rect.width * W`.
3. **Loop.** `requestAnimationFrame` + fixed-timestep accumulator (physics 120 Hz, `STEP=1/120`), `dt` clamped to 0.25 s; auto-pause on `visibilitychange` while PLAYING.
4. **State machine.** `ATTRACT → READY (ball glued) → PLAYING ↔ PAUSED; LIFE_LOST → READY; LEVEL_CLEAR → READY (level+1, same wall, faster); GAME_OVER → submit → ATTRACT`. Single `setState()` also toggles DOM overlay panels.
5. **Input.** keydown/keyup held-flags for arrows; mousemove sets paddle x directly (last input wins). Space = launch/pause-toggle, Esc = pause, Enter = start/restart. `preventDefault()` on Space/Arrows/Esc **except when `e.target.tagName === "INPUT"`** (initials field). First keydown/pointerdown calls `ensureAudio()`.
6. **Physics.** At 120 Hz and ≤640 px/s, max step ≈5.3 px < ball radius → simple overlap tests, no swept collision. Walls reflect; bottom = life lost. Paddle deflection: `off = clamp((ball.x - paddle.cx)/(paddle.w/2), -1, 1)`, angle `off × 60°` from vertical, speed preserved; enforce `|vy| ≥ 0.25 × speed` (kills horizontal rallies). Bricks: circle-AABB, reflect on smaller penetration axis, max one brick per step.
7. **Neon rendering.** Clear to bg; glow via `shadowColor`/`shadowBlur≈14` set **once per brick row** (draw grouped by row) and once each for ball/paddle, then reset to 0. **HUD and overlays are DOM, not canvas** (crisp mono text, real `<input maxlength="3">` and mute `<button>`); JS updates `textContent` only on change. Scanline vibe comes free from existing `body::before` — don't re-implement.
8. **Audio** (~70 lines, same file) — see §3.
9. **Scores UI** — see §4.

**Excluded from v1** (put this list in a top-of-file comment): power-ups, multiball, multi-hit bricks, per-level layouts, particles/screen-shake, touch, gamepad, extra lives, paddle shrink, volume slider (mute only), pause menu.

## 3. WebAudio SFX

- Lazy singleton `AudioContext` created/resumed only inside a user-gesture handler (autoplay policy). Master `GainNode`; mute = gain 0 (scheduling stays branch-free).
- `beep({freq, dur=0.08, type="square", vol=0.18, slideTo=null, delay=0})`: oscillator → per-note gain envelope (5 ms attack, exp decay) → master.
- Map: `paddle` 220 Hz sq 60 ms · `wall` 160 Hz tri 45 ms · `brick` 330+row×45 Hz tri 70 ms · `life` slide 320→70 saw 350 ms · `gameover` 330/220/147 descending · `levelclear` 262/330/392/523 arpeggio.
- Mute persisted at `localStorage["zylebot.breakout.muted"]`; `#mute-btn` updates `aria-pressed` + label and calls `blur()` in its click handler (else Space re-clicks it).

## 4. High scores

**`app/db.py`** — append to `_SCHEMA` (idempotent `CREATE TABLE IF NOT EXISTS`, picked up by existing `executescript` in `init_db()`; no ALTER migration needed):

```sql
CREATE TABLE IF NOT EXISTS scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game TEXT NOT NULL DEFAULT 'breakout',
    initials TEXT NOT NULL,
    score INTEGER NOT NULL,
    level INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scores_game_score ON scores(game, score DESC);
```

New `# --- Scores ---` section, module-level helpers in house style (use `_connect()` + `_now()`; the contextmanager auto-commits — no manual commits):

```python
def insert_score(initials: str, score: int, level: int, game: str = "breakout") -> dict[str, Any]: ...
def top_scores(game: str = "breakout", limit: int = 10) -> list[dict[str, Any]]:
    # ORDER BY score DESC, id ASC (earlier entry wins ties) LIMIT ?
```

**`app/models.py`** — Pydantic v2 DTO with normalization:

```python
class ScoreSubmit(BaseModel):
    initials: str = Field(min_length=1, max_length=3)
    score: int = Field(ge=0, le=1_000_000)
    level: int = Field(default=1, ge=1, le=99)
    # field_validator uppercases, strips non-alnum, rejects empty result
```

**`app/main.py`** — `# --- Game scores ---` section after the Conversations block (~line 195); add `ScoreSubmit` to the `app.models` import:

```python
@app.get("/api/scores")           # -> {"scores": db.top_scores(limit=clamp(limit,1,50))}
@app.post("/api/scores")          # -> {"entry": ..., "top": db.top_scores()}  (saves a second fetch)
```

**Frontend**: attract panel fetches/renders top-10 (`RANK INITIALS SCORE LVL`, mono, `padStart(6,"0")`). Game over: initials input prefilled from `#game-root.dataset.initials`, Enter/SUBMIT posts and re-renders returned `top`, SKIP bypasses → ATTRACT.

## 5. Agent tie-in — `app/tools/game_tools.py` (new)

Mirror the existing `@tool` pattern exactly (verified signature in `app/tools/base.py:25`):

```python
from typing import Any
from app import db
from app.tools.base import RiskTier, tool

@tool(
    name="get_game_scores",
    description=("Read the local Breakout arcade high-score table stored by this app: "
                 "top entries with initials, score, level reached, and date. Use when the "
                 "user asks about their Breakout / arcade / game high scores."),
    parameters_schema={"type": "object", "properties": {
        "limit": {"type": "integer", "description": "How many entries to return (default 10, max 25)."}}},
    risk_tier=RiskTier.SAFE,
)
def get_game_scores(limit: int = 10) -> dict[str, Any]:
    k = max(1, min(int(limit), 25))
    return {"game": "breakout", "scores": db.top_scores(limit=k)}
```

Register in `app/tools/__init__.py` alongside the others: `from app.tools import game_tools as _game_tools  # noqa: F401,E402`. Schema build + dispatch are automatic via `_REGISTRY`. No import cycle (`db` imports only `config`).

## 6. Implementation order (each phase independently verifiable)

1. **Scores backend** — db.py DDL+helpers, models.py DTO, main.py routes. Verify by curl alone.
2. **Page skeleton** — pages.py route, game.html, game.css, stub game.js (canvas sizing + theme read + static neon frame).
3. **Core game** — loop, states, input, physics, bricks, lives, levels, HUD, overlays. Playable with silent audio stubs. The big one; watch the ~650-line budget.
4. **Audio** — synth + wiring + mute persistence.
5. **Scores frontend** — attract top-10, game-over submit flow (needs 1+3).
6. **Tool + links + docs** — game_tools.py, `__init__.py` import, footer link, README row (needs only 1). Then orchestrator updates HANDOFF.md.

(Phases 1 and 2–4 are independent backend/frontend lanes — an observation only; specialists remain opt-in per project rules.)

## 7. Verification

Run: `.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload` (LM Studio running for chat/tool checks; not needed for phases 1–5).

- **P1**: restart creates `scores` table in `data/zylebot.db`. Use `curl.exe` (bare `curl` is an Invoke-WebRequest alias in PS 5.1):
  - `curl.exe http://127.0.0.1:8000/api/scores`
  - `curl.exe -X POST http://127.0.0.1:8000/api/scores -H "Content-Type: application/json" -d "{\"initials\":\"nem\",\"score\":1200,\"level\":2}"` → initials normalized to `NEM`
  - negative score → expect 422
- **P2**: `/game` renders, console clean; open `/` and send a chat message (regression check — app.js untouched by design).
- **P3**: full playthrough — launch, scoring, 3 lives → game over, wall clear → level 2 faster. Arrows don't scroll page, Space pauses, tab-away auto-pauses, window resize keeps playfield crisp and mouse mapping accurate.
- **P4**: all six SFX audible; mute persists across refresh; no AudioContext console warning before first gesture.
- **P5**: game over → prefilled initials → submit → attract shows entry; curl agrees.
- **P6**: in chat ask "what's my breakout high score?" → `get_game_scores` runs as SAFE (no approval card) with real data; footer link visible on `/` and a product page.
- Project rules: **no git commands**; HANDOFF.md updated by the orchestrator at the end (record: `/game` route, new files, tool name, the "extend page.html" decision, excluded-features list).

## 8. Risks / gotchas

- **Key handling**: `preventDefault()` for Space/Arrows/Esc must skip events targeting the initials `<input>`, or typing breaks. Mute button must `blur()` after click.
- **Hidden-tab rAF freeze**: clamp accumulated dt (0.25 s) + auto-pause on `visibilitychange` — no ball teleport on return.
- **AudioContext**: construct/resume only in gesture handlers.
- **shadowBlur cost**: set per-row/entity, not per-rect; blur ≤ ~16. (Escape hatch if ever needed: pre-rendered offscreen bricks — v2, don't build now.)
- **DPI**: re-read `devicePixelRatio` inside every `resizeCanvas()` call.
- **Chat regressions**: structurally prevented — only shared file touched is `_footer.html` (one anchor).
- **DB**: `_connect()` auto-commits; keep helpers commit-free like the rest of db.py.
- **Score abuse**: irrelevant on localhost single-user, but Pydantic bounds keep a buggy client from writing garbage.
- Note: `tests/` doesn't exist in the tree (HANDOFF mention is stale); a `tests/test_scores.py` for DTO normalization + ordering would be net-new infra — optional, not part of v1.
