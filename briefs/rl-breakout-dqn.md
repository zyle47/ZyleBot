# Brief — RL agent that learns ZyleBot Breakout (DQN from scratch)

> Implementation brief for Codex. Planned by Claude with Nemanja on 2026-07-19; decisions here are
> settled — build to this spec, don't relitigate. Follow `CLAUDE.md`; consult `HANDOFF.md` for
> current state. Report back: what you built, checks run + results, and any deviations with reasons.

## Mission

Train a DQN — written from scratch in PyTorch, no RL libraries — to play the Breakout game that
ships at `/game` (`app/static/game.js`). Then let visitors watch it: an **AI PLAYS** toggle on the
game page streams live state to a FastAPI WebSocket, and the trained policy answers with paddle
moves. The point of "from scratch" is pedagogical: the replay buffer, target network, and
epsilon-greedy machinery should be hand-built and clearly commented — this code doubles as a
reading artifact.

**Scope v1: master Level 1 only** (the classic 10×6 wall — no Piercer, no Splitter). But the
physics port and observation layout must already carry the multiball/pierce machinery (see specs)
so levels 2–4 become a follow-up, not a redesign.

## Hard constraints (non-negotiable)

- **No git commands, ever** (read-only `git status`/`diff`/`log` only if a task genuinely needs it). Nemanja commits.
- **Do not touch the chat shell**: `app/static/app.js`, `app/static/style.css`, `app/templates/index.html`, `app/templates/base.html` stay untouched. Game-page files only (`game.js`, `game.css` if needed, `game.html`).
- **Zero new dependencies in the app venv** (`./venv`). Verified already present and sufficient: `numpy 2.5.1`, `websockets 16.0` (uvicorn's WS backend). All heavy deps (torch etc.) live in a **separate** `rl/venv`.
- **Do not edit `HANDOFF.md`** — the orchestrator documents.
- **Do not weaken the CONFIRM_REQUIRED gate** or touch `app/command_guard.py` / tool tiers. The new WebSocket endpoint is read-only inference — it needs no confirmation tier and must not add one.
- `rl/features.py` must stay **torch-free** (pure numpy) — the app venv imports it.
- Don't edit `app/db.py` or the database; scores API is untouched.

## Environment facts (verified on this machine)

- System Python: **3.14.0 only** (`py -0p`). App venv is 3.14.
- Create `rl/venv` with Python 3.14. Install torch from the current CUDA index
  (`pip install torch --index-url https://download.pytorch.org/whl/cu126` — use whichever cuXXX
  index is current). Fallback A: CPU-only torch (the MLP is tiny; CPU trains it fine). Fallback B
  (only if cp314 wheels don't exist at all): tell Nemanja to install Python 3.13 side-by-side.
- GPU: RTX 3080 Ti 12 GB, driver 596.21. `--device auto` should pick cuda when available.
- The existing `.gitignore` `venv/` pattern is unanchored and already covers `rl/venv/`.
  **Add one entry**: `rl/runs/` (checkpoints + logs). Keep `rl/policy/` tracked — the exported
  ~350 KB `.npz` is what makes AI PLAYS work from a fresh clone.

## Deliverables

```
rl/                        # new subproject at repo root
  __init__.py              # empty — makes `rl` importable from the app venv (repo root is cwd)
  breakout_env.py          # 1:1 physics port as a gymnasium.Env
  features.py              # observation builder — pure numpy, shared by training AND the app
  dqn/
    __init__.py
    network.py             # QNetwork MLP
    replay_buffer.py       # preallocated numpy ring buffer
    agent.py               # epsilon-greedy act · Double-DQN learn step · target sync
  train.py                 # training loop: CSV log, tqdm, periodic eval, checkpoints, resume
  play.py                  # run a checkpoint greedily, print per-episode score/steps stats
  export_policy.py         # checkpoint → rl/policy/breakout_policy.npz + meta.json
  plot.py                  # matplotlib learning curves from the CSV
  requirements.txt         # torch, gymnasium, numpy, matplotlib, tqdm
  tests/test_env.py        # physics + MDP unit tests (unittest, style of app/tests/)
app/rl_policy.py           # npz loader + pure-numpy forward pass (no torch anywhere)
app/main.py                # + `/ws/game-agent` WebSocket endpoint
app/static/game.js         # + AI PLAYS toggle & state streaming
app/templates/game.html    # + the toggle button in the HUD row
.gitignore                 # + `rl/runs/`
README.md                  # short new section: what the RL subproject is, how to train/watch
```

---

## Part 1 — Physics port (`rl/breakout_env.py`)

A `gymnasium.Env` that mirrors `game.js` **exactly**. The JS is the spec — read it before writing
Python. Everything physics lives in `game.js` lines 15–33 (constants), 265–308 (`buildBricks`),
342–351 (launch), 416–595 (`stepPhysics`). Headless: no rendering, no audio, no DOM, no HUD.

Constants to port verbatim: `W=800 H=600`, `PHYSICS_STEP=1/120`, paddle 110×14 at `y=560`,
`PADDLE_SPEED=520`, `BALL_R=7`, `BALL_BASE_SPEED=340`, `SPEED_PER_BRICK=5`, `SPEED_PER_LEVEL=40`,
`MAX_SPEED=640`, `MAX_DEFLECT=π/3`, `MIN_VY_FRAC=0.25`, `START_LIVES=3`, `PIERCER_HITS=5`,
`PIERCER_DURATION=10`, `SPLITTER_HITS=4`, `SPLIT_SPREAD=0.55`. Level-1 wall: 10 cols × 6 rows,
`top=60 side=20 gap=6 brick_h=22`, row points `[70,70,50,50,30,30]` → 60 bricks, brick width
`(800−40−9·6)/10 = 70.6`.

Port `stepPhysics` preserving **order of operations** — subtle behavior lives in the ordering:

1. Move paddle (`±520·dt`, clamp centre to `[w/2, W−w/2]`), tick `pierce_remaining` down.
2. Per ball, in array order: integrate → side walls (left is `if`, right is `elif`; each sets
   position flush, reflects vx, then `enforce_bounce`) → top wall (separate `if`) → drain
   (`y−R > H` marks the ball dead, `continue`) → paddle (only when `vy > 0` and AABB overlap:
   snap `y`, `offset = clamp((x−paddle_x)/(w/2), −1, 1)`, `angle = offset·MAX_DEFLECT`,
   `vx = sin(angle)·speed`, `vy = −cos(angle)·speed`, then `enforce_bounce`) → bricks.
3. Brick pass: iterate bricks in build order, skip dead, closest-point circle-vs-AABB test.
   On the **first** hit only (the JS `break`): if not piercing, push the ball out along the
   smaller-overlap axis and reflect that axis away from the brick centre; damage
   `hitsTaken = brick.hits if piercing else 1`; score `+= points·hitsTaken`; on brick death
   decrement `bricks_alive` and `speed = min(speed+5, 640)`; a dead Piercer sets
   `pierce_remaining = 10`; a dead Splitter spawns 2 extra balls fanned `±0.55` rad around
   `heading = atan2(vx, −vy)` at full speed; while piercing renormalize velocity magnitude to
   `speed` instead of bouncing, otherwise `enforce_bounce`; if `bricks_alive == 0` handle level
   clear and **return early** (the JS skips the survivor filter — port that).
4. After the ball loop: `balls = [not dead] + spawned`. Empty list → life resolution.

Port `enforce_bounce` exactly (game.js:427–436): renormalizes total velocity magnitude to the
current `speed`, preserves both signs, clamps `|vy|` into `[0.25·speed, speed]`, rebuilds `vx`
from the Pythagorean remainder. This clamp shapes every rally — get it exactly right.

Keep Piercer/Splitter/multiball code paths live in the port even though Level 1 never triggers
them (they're needed for wire-format stability and the levels-2–4 follow-up). Level-1 `buildBricks`
needs no pattern masks — don't port the level 2–4 art patterns yet; take layout row/col counts
from a `LEVEL_LAYOUTS`-equivalent that only defines level 1 and raises on `level > 1`… **no** —
level clear on level 1 terminates the episode (below), so `level > 1` is unreachable; assert that.

### MDP wrapping

- **Actions** `Discrete(3)`: 0 = none, 1 = left, 2 = right (the keyboard model). One env step
  applies the action for **4 physics substeps** of `1/120` s (30 Hz decisions). If the life ends
  or the level clears mid-substep, stop substepping immediately.
- **Observation**: `float32[78]`, built by `rl/features.py` (spec below).
- **Reward** per env step: sum of `brick_points_scored/100` over the substeps, **+5** on level
  clear, **−5** on life lost, else 0.
- **Episode**: `reset(seed=...)` centres the paddle, builds the wall, and launches immediately
  (no READY state) with the seeded random angle `(u−0.5)·π/6`, `u∼U[0,1)` — i.e. ±15° around
  straight up (game.js:343–349). `terminated` on life lost **or** level clear (single-life
  episodes — cleaner credit assignment); `truncated` after 20 000 env steps (an agent dodging
  the last brick could rally forever).
- **Determinism**: all randomness through one `np.random.Generator` seeded in `reset`. Two envs
  stepped with the same seed and action sequence must produce bit-identical trajectories.
- Expose the raw state (paddle, balls, bricks, speed, pierce) via a method or `info` so
  `features.py` and tests can reach it without touching privates.

### `rl/features.py` — the shared observation builder

Pure numpy, **no torch import** (the app venv imports this module; note that in the docstring).
One public function, `build_observation(state) -> np.ndarray[float32, 78]`, where `state` is a
plain dict — the *contract* between the training env and the browser bridge:

```python
{"paddle_x": float,                  # centre, logical px
 "balls": [[x, y, vx, vy], ...],     # live balls only, ≤ 3
 "speed": float, "pierce": float,    # current magnitude, seconds remaining
 "bricks": [(hits, max_hits), ...]}  # build order (row-major), dead bricks hits=0
```

Layout (78 dims): `paddle_x/800` (1) · three ball slots sorted **lowest first** (largest y), each
`[x/800, y/600, vx/640, vy/640, alive]`, unused slots all-zero (15) · `speed/640`, `pierce/10`
(2) · brick grid `hits/max_hits` in build order, 60 entries (60). Level 1 always has exactly 60
brick entries.

---

## Part 2 — DQN from scratch (`rl/dqn/`)

No SB3, no rllib, no copy-pasted trainer. Comment the *why* at each mechanism (this is the
teaching artifact): why a target network, why replay breaks correlation, why Double DQN fights
overestimation.

- `network.py`: MLP `78 → 256 → 256 → 3`, ReLU, outputs Q-values per action.
- `replay_buffer.py`: preallocated numpy arrays (obs, action, reward, next_obs, done), capacity
  **500 000** (~300 MB), ring insertion, uniform batch sampling → torch tensors on demand.
- `agent.py`: epsilon-greedy act (ε linear **1.0 → 0.05 over 150 000** agent steps, then flat);
  learn step = **Double DQN** target — online net argmaxes next-state actions, target net
  evaluates them; `target = r + γ·(1−done)·Q_target(s', argmax_a Q_online(s', a))`; Huber loss,
  Adam **1e-4**, γ **0.99**, batch **256**; hard target-net sync every **2 000** learn steps.
- `train.py`: flags `--steps` (default 2 000 000), `--seed`, `--device auto|cuda|cpu`,
  `--resume <ckpt>`. Warmup 10 000 steps of random actions before any learning; then one learn
  step per env step. Every 10 000 steps: 5 greedy eval episodes on a separate env; append a CSV
  row (`steps, epsilon, loss, train_return, eval_score_mean, eval_len_mean`) under
  `rl/runs/<timestamp>/log.csv`; save `latest.pt` every eval and `best.pt` on a new best eval
  score; tqdm progress bar. Checkpoints carry optimizer + ε + step count so `--resume` truly
  resumes.
- `play.py`: `--ckpt` path, N greedy episodes, print score/steps per episode + mean.
- `plot.py`: `--run` dir → PNG with eval-score and loss curves.

Run commands (document in README): `rl\venv\Scripts\python.exe -m rl.train`, `-m rl.play`, etc.
from the repo root. Nemanja runs the long training himself — your job is that a **smoke run**
(`--steps 5000`) completes end-to-end: buffer fills, loss is finite, CSV + checkpoint written,
`play.py` loads the checkpoint.

Calibration expectations (for Nemanja, put in README): random policy ≈ 0–100 score;
paddle-tracking emerges ~100–300k steps; reliable level-1 clears somewhere in 0.5–3 M steps
(hours, not days — CPU-bound env stepping is the bottleneck, that's expected).

---

## Part 3 — AI PLAYS in the real browser game

1. `rl/export_policy.py`: `--ckpt` → `rl/policy/breakout_policy.npz` (each layer's weight/bias as
   named arrays) + `rl/policy/meta.json` (obs version tag `"level1-v1"`, training steps, eval
   score). Runs in the rl venv.
2. `app/rl_policy.py` (app venv, **numpy only**): lazily load the npz on first use from the
   module-constant path `rl/policy/breakout_policy.npz` (no `.env` knob in v1); forward pass =
   two ReLU matmuls + linear head; `act(state_dict) -> int` uses
   `rl.features.build_observation`, then argmax. Missing/corrupt npz → raise a specific
   exception the endpoint turns into a clean close.
3. `app/main.py`: `@app.websocket("/ws/game-agent")`. Per message, client sends compact state
   JSON; server replies `{"action": 0|1|2}`. Contract:

   ```
   client → {"paddle_x": 400.0, "balls": [[x,y,vx,vy], ...],
             "bricks": "60-char digit string, hits remaining per brick in build order",
             "speed": 340.0, "pierce": 0.0}
   server → {"action": 1}
   ```

   Level-1 max_hits is 1, so the digit string maps to `(hits, 1)` pairs. If the payload doesn't
   parse or `len(bricks) != 60` (e.g. the game advanced to level 2), reply `{"action": 0}` —
   never crash the socket mid-game. No policy file on disk → accept, send
   `{"error": "no-policy"}`, close normally. Inference is a ~90k-param numpy matmul —
   microseconds; no thread offload needed, but never block on anything else in the handler.
4. `game.js` + `game.html`: an `AI: OFF/ON` HUD toggle button (mirror the SOUND button's markup/
   style contract) plus the `A` key on the ATTRACT screen. When toggled on from ATTRACT: open the
   WS; on open, `startGame(1)` and auto-launch; every **2nd** rAF frame while PLAYING (~30 Hz —
   matches the training cadence) send current state (serialize hits of `game.bricks` in build
   order; live balls only), and on each reply map the action onto `game.input.left/right`
   (sticky until the next reply). READY after a lost life: auto-launch again. GAME_OVER: stop
   streaming, leave the normal submit/skip flow alone (do not auto-restart, per the existing
   stray-click rule). WS error/close → toggle to OFF, clear `game.input`, show a small
   "AI OFFLINE" badge near the toggle, human input untouched. Mouse/keyboard stay live
   throughout (last input wins, exactly like today). Level 2+ reached by the AI: keep streaming;
   the server no-ops (above) and the human can take over — acceptable v1 behavior.

---

## Acceptance checklist (all must pass; report results explicitly)

Env tests — `rl/tests/test_env.py`, `unittest`, runnable via
`rl\venv\Scripts\python.exe -m unittest discover -s rl/tests`:

- [ ] Level-1 wall: 60 bricks, correct AABBs for corners, row points `[70,70,50,50,30,30]`.
- [ ] Paddle deflection: ball landing at offsets −1 / 0 / +1 leaves at −60° / 0° / +60° from
      vertical, magnitude = `speed`.
- [ ] `enforce_bounce`: post-bounce `|v| == speed` and `|vy| ≥ 0.25·speed`, signs preserved.
- [ ] Brick side-resolution: a ball arriving mostly-horizontal reflects vx, mostly-vertical
      reflects vy; ball ends outside the brick.
- [ ] Scoring + speed ramp: each broken brick adds its row points and +5 speed, capped at 640.
- [ ] Drain → `terminated=True`, reward includes −5; level clear (drive it by deleting all but
      one brick through the raw-state accessor) → `terminated=True`, reward includes +5.
- [ ] Same seed + same action sequence ⇒ identical observation sequences; different seeds ⇒
      different launch angles.
- [ ] `build_observation`: correct shape/dtype/ranges; ball-slot sort order; env-built state dict
      and an equivalent wire-format dict produce identical vectors.

Integration:

- [ ] Smoke train (`--steps 5000`) end-to-end in the rl venv; artifacts appear under `rl/runs/`.
- [ ] `export_policy.py` on the smoke checkpoint → npz+meta; `app/rl_policy.py` loads it in the
      **app** venv and returns actions (add a small unittest under `app/tests/` for the numpy
      forward pass matching torch's output on a fixed input, tolerance 1e-5 — generate the
      reference values in the rl venv, hardcode them in the test).
- [ ] Existing app suite still green: `./venv/Scripts/python.exe -m unittest discover -s app/tests`.
- [ ] App boots; `/game` works with AI OFF exactly as before; toggle ON with the smoke policy →
      paddle visibly moves under WS control; kill the server → badge appears, toggle OFF, human
      play still works.
- [ ] Chat shell untouched (`git status` may be used read-only to confirm the file list).

## Out of scope (do not build)

Levels 2–4 training · curriculum · Dueling/PER/rainbow variants · pixel observations · any
`.env`/config knobs · touch/gamepad input · auto-submitting AI scores · committing anything.
