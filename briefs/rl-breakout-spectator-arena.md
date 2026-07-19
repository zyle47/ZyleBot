# Brief — Breakout AI Spectator Arena

> Implementation brief for Claude. Planned by Codex with Nemanja on 2026-07-20. Decisions in
> this document are settled: build to this contract without widening the feature. Follow
> `CLAUDE.md`, read `HANDOFF.md`, and inspect the existing RL/game implementation before editing.
> Report what was built, every check run and its result, and any deviation with its reason.

## Mission

Let Nemanja watch several independent copies of the exported Breakout DQN play at once. Add an
**AI Spectator Arena** at `/game/arena`: four games by default in a responsive grid, controls for
1/2/4/6 games, and a literal multi-window pop-out mode. Spectator games start themselves, stay
muted, never pollute the leaderboard, and loop level 1 so they can run unattended.

The arena must also make policy improvement visible. Policy export becomes atomic, the app safely
hot-reloads a newer exported policy without restarting, and an opt-in `--live-export` training flag
publishes each new best checkpoint. The currently running trainer cannot acquire a new flag after
startup; manual export of its `best.pt` remains the supported way to publish snapshots from that run.

This is a viewer and deployment feature. **Do not change physics, observations, rewards, DQN
hyperparameters, checkpoint selection, or the ordinary `/game` rules.** Reward shaping is a
separate future experiment.

## User experience

### Arena page

`GET /game/arena` renders a game-only dashboard consistent with the existing Tron/liquid-glass
game styling. It contains:

- A header with `BREAKOUT // AI ARENA`, active policy step/eval, and connection state.
- Game-count controls: `1`, `2`, `4`, `6`; default `4`, hard maximum `6`.
- `START ALL`, `STOP ALL`, and `POP OUT` controls.
- A responsive grid of independent game tiles. Four games are 2×2 on desktop and one column on
  narrow screens. Each tile shows its slot number and online/offline/running state.
- Aggregate stats: completed arena runs, level-1 clears, clear percentage, mean final score, and
  highest final score.

One **arena run** uses the real browser game's normal three lives and ends on either GAME_OVER or
a level-1 clear. This is intentionally not identical to the trainer's single-life evaluation, so
label arena metrics as viewer statistics and do not present them as `eval_score_mean`.

Add a small `AI ARENA` link on the ordinary game page (game-page markup only). Do not add links to
the chat shell or shared footer.

### Spectator game behavior

Each arena tile is a same-origin iframe using:

```text
/game?embed=1&ai=1&spectator=1&slot=1
```

The query contract is:

- `embed=1` — render only the game board/HUD, without the normal page header/footer.
- `ai=1` — connect the existing AI WebSocket and start level 1 automatically.
- `spectator=1` — unattended viewer rules described below; it is honored only with `ai=1`.
- `slot=N` — integer identity clamped to 1–6 and included in viewer events.

Spectator mode must:

- Start level 1 as soon as the WebSocket is ready.
- Retain the existing AI auto-launch after a lost life.
- Force audio output off **without writing the shared mute preference to localStorage**.
- Never POST a score or focus the initials form.
- On GAME_OVER, report the run, show the normal panel briefly, then restart level 1 after 1.5 s.
- On level-1 clear, report a win, show the clear panel briefly, then restart level 1 after 1.5 s.
  Do not build or enter level 2 in spectator mode because the v1 policy is level-1-only.
- Stop looping if AI goes offline. Show the existing `AI OFFLINE` state and wait for arena/user
  recovery; do not create a reconnect storm.

Ordinary `/game` without these parameters must remain pixel/behavior compatible: no auto-start,
no GAME_OVER restart, normal sound preference, normal score form, and normal level progression.

### Parent/child event contract

Do not have the arena reach into iframe-private JavaScript state. Spectator games publish events
with `window.postMessage`. Send to `window.parent` for iframe mode or `window.opener` for pop-outs.

Ready:

```json
{"type":"breakout-spectator-ready","slot":1}
```

Run finished:

```json
{
  "type":"breakout-spectator-run-end",
  "slot":1,
  "score":450,
  "cleared":false,
  "reason":"game-over"
}
```

Offline:

```json
{"type":"breakout-spectator-offline","slot":1}
```

The arena must accept messages only when `event.origin === location.origin`, validate the event
shape, and accept slot values only in 1–6. Ignore every unknown message. A run result is counted
once even if a state transition or timer fires twice.

### Literal pop-out mode

`POP OUT` opens the selected number of independent spectator games as real browser windows. It
must run directly from the user's click (no delayed popup calls), use stable per-slot window names,
and retain a handle for each opened window. A popped-out game reports through `window.opener` using
the same event contract.

Avoid doubling inference load: successfully popped-out slots replace/stop their iframe instance.
If the browser blocks a window, retain that slot in the grid and show a clear `POPUP BLOCKED`
message. Closing a pop-out must not break the other games; `START ALL` can recreate missing slots.

## Template structure

The current `game.html` extends `page.html`, so placing it directly in an iframe would also embed
the site header/footer. Refactor the board markup once rather than duplicating it:

```text
app/templates/_game_board.html     # shared HUD, canvas, panels, help
app/templates/game.html            # existing full page; includes _game_board.html
app/templates/game_embed.html      # extends base.html; board only
app/templates/game_arena.html      # full arena dashboard
```

Update the existing `/game` route in `app/pages.py` to accept the boolean `embed` query parameter
and select `game_embed.html` only when true. Pass the existing default initials to both templates.
The embedded template still loads the existing theme CSS plus `pages.css`/`game.css`; give its body
a dedicated class so it fills its iframe cleanly. Do not edit `base.html`, `page.html`, `index.html`,
`app.js`, or `style.css`.

## Arena implementation files

Expected additions/edits:

```text
app/pages.py
app/templates/_game_board.html
app/templates/game.html
app/templates/game_embed.html
app/templates/game_arena.html
app/static/game.js
app/static/game.css
app/static/game-arena.js
app/static/game-arena.css
app/rl_policy.py
app/main.py
rl/export_policy.py
rl/train.py
app/tests/test_rl_policy.py
app/tests/test_game_agent.py
README.md
```

Keep the arena dependency-free: vanilla JavaScript/CSS/Jinja, no frontend build step.

## Atomic policy publishing

The app may read a policy while training/export is active, so `export_policy.py` must never expose
a partially written artifact.

Refactor export into a reusable function callable by both its CLI and `train.py`. For each publish:

1. Write the NPZ to a temporary file in `rl/policy/` (same filesystem as the destination).
2. Flush/close it completely.
3. Write metadata to a temporary JSON file and close it.
4. Atomically replace `meta.json`.
5. Atomically replace `breakout_policy.npz` **last**; its replacement is the commit marker.
6. Clean up abandoned temporary files on failure without touching the last good export.

Use `os.replace`, not delete-then-rename. Preserve the six existing named weight/bias arrays. Put
the authoritative observation version, training step, and eval score inside the NPZ as additional
scalar arrays as well as in human-readable `meta.json`, so the app never pairs new weights with
stale metadata during the two-file replacement window. `meta.json` must remain strict JSON—no NaN
or Infinity.

## Safe app-side hot reload

`app/rl_policy.py` remains numpy-only and torch-free. Extend its lazy singleton to detect a changed
policy commit marker (`st_mtime_ns` plus file size is sufficient), throttled to at most one stat
check per second across all sockets.

Reload rules:

- Fully open, parse, shape-check, and metadata-check a candidate before swapping it into service.
- Required observation version remains `level1-v1`.
- On a bad/missing replacement, log a warning and keep the last known-good in-memory policy.
- If no valid policy has ever loaded, retain the existing `PolicyUnavailableError`/`no-policy`
  behavior.
- Multiple WebSockets share one current immutable policy object. No torch and no thread offload.
- A newly published policy may become active during an arena run; this is acceptable for the
  viewer. The status label must update when the swap succeeds.

Add a read-only endpoint:

```text
GET /api/game-agent/status
```

Response with a loaded policy:

```json
{
  "available": true,
  "observation_version": "level1-v1",
  "training_steps": 720000,
  "eval_score": 454.0
}
```

No policy:

```json
{"available":false}
```

The arena polls every two seconds. This endpoint must not load torch, touch LM Studio, or add a
confirmation tier.

## Optional live export from training

Add an opt-in flag to `rl.train`:

```text
--live-export
```

Default is false. When true, every newly improved `best.pt` is also published atomically to the
fixed `rl/policy/` location with its current step and eval score. Do not export `latest.pt`, do not
publish on non-improving evaluations, and do not alter checkpoint selection.

The current active training process was started before this flag exists and cannot adopt it. To
publish snapshots from that run, the documented command remains:

```powershell
$run = Get-ChildItem .\rl\runs -Directory |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

.\rl\venv\Scripts\python.exe -m rl.export_policy `
    --ckpt "$($run.FullName)\best.pt"
```

After hot reload lands, this manual export must update the arena without restarting ZyleBot.

## Performance and concurrency

Four games produce roughly 120 state/action exchanges per second; six produce roughly 180. The
network is small and numpy inference is synchronous, so batching is out of scope. Do not add a
queue, worker pool, or torch server unless measurements prove the existing design insufficient.

Run a live four-client WebSocket harness for at least 60 seconds at 30 Hz per client. It must have:

- No disconnects or malformed replies.
- Every reply action in `0|1|2`.
- No event-loop starvation visible on `/api/game-agent/status`.
- Report measured message count and approximate latency; no hard latency number is pinned because
  Windows/BLAS scheduling varies, but interaction must remain visibly smooth.

Cap user-selected games at six in both UI logic and server-rendered defaults. Each client retains
the existing one-request-in-flight backpressure in `game.js`.

## Tests and acceptance checklist

Automated tests:

- [ ] Ordinary `/game` renders the shared board and preserves its existing DOM ids.
- [ ] `embed=1` selects board-only markup; false/absent values keep the full page.
- [ ] Game-count and slot values are clamped to 1–6.
- [ ] Atomic export produces valid NPZ + strict metadata and leaves the previous pair usable if a
      staged write fails.
- [ ] Numpy hot reload swaps to a valid newer policy.
- [ ] Corrupt, missing, wrong-shaped, or wrong-observation-version replacements keep the last good
      policy; initial absence still produces `no-policy`.
- [ ] `/api/game-agent/status` accurately reports unavailable/loaded/reloaded state.
- [ ] Existing numpy-vs-torch forward tolerance remains `1e-5`.
- [ ] Existing malformed WebSocket payloads still return action 0 without closing the socket.
- [ ] Full RL env suite and full app suite remain green.
- [ ] `node --check app/static/game.js app/static/game-arena.js` passes; CSS braces balance.

Live/browser acceptance:

- [ ] `/game/arena` starts four visibly independent agents with separate launches and sockets.
- [ ] 1/2/4/6 controls create exactly that many games; STOP/START is repeatable.
- [ ] Spectator GAME_OVER loops level 1 after 1.5 s and does not submit a score.
- [ ] Spectator level clear counts a win and loops level 1 instead of entering level 2.
- [ ] Normal `/game` retains manual start, three lives, score form, sound preference, and level 2.
- [ ] Spectator audio is silent without changing the normal game's saved SOUND setting.
- [ ] Arena aggregate totals update exactly once per completed run.
- [ ] POP OUT creates real independent windows; blocked popups degrade cleanly to grid tiles.
- [ ] Manual policy export changes the displayed step/eval and active policy without app restart.
- [ ] Replacing the export with a deliberately corrupt candidate leaves running games on the last
      good policy (restore the valid export afterward).
- [ ] Killing ZyleBot marks tiles offline without a reconnect storm; controls remain usable after
      the server returns.
- [ ] Four-client/30-Hz/60-second WebSocket concurrency check passes and results are reported.

## Documentation

Add a short README subsection covering:

- `/game/arena` and the 1/2/4/6 viewer controls.
- Grid vs pop-out behavior.
- The fact that arena stats use three-life browser runs and are not trainer eval statistics.
- Manual best-policy export during the current run.
- `--live-export` for future training runs.
- Policy hot reload and the six-game cap.

Update `HANDOFF.md` at completion with current state and verification results; keep it concise and
do not turn it into a changelog.

## Hard constraints

- No git commands; Nemanja handles version control.
- Do not touch `app/static/app.js`, `app/static/style.css`, `app/templates/index.html`,
  `app/templates/base.html`, or `app/templates/page.html`.
- No new app-venv or frontend dependencies; `app/rl_policy.py` stays numpy-only.
- Do not change Breakout physics, scoring, rewards, observation layout, DQN architecture,
  hyperparameters, replay behavior, or ordinary game state transitions.
- Do not write spectator results to SQLite and do not edit `app/db.py`.
- Do not weaken confirmation/tool safety or touch `app/command_guard.py`.
- Do not auto-enable live export by default.
- No training dashboard, video recording, policy tournament, model-vs-model comparison, or levels
  2–4 agent support in this task.
