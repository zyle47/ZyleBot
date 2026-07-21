# ZyleBot — Handoff (current state)

> Living *current-state* doc — what exists, what's in flight, and the gotchas that save debugging
> time. **The main (orchestrator) model updates this file every time work lands** — subagents
> report, they don't document. Not a changelog; git history is the changelog. Last updated: **2026-07-20**.
>
> Companions: `CLAUDE.md` (shared project rules + Claude agent routing), `README.md` (user-facing setup/tools),
> `.codex/config.toml` + `.codex/agents/` (project-scoped Codex configuration and specialist roles).

## Where things live

- `app/main.py` — FastAPI app, lifespan, all `/api/*` routes; `/static/*` served no-cache.
- `app/pages.py` — all HTML routes (Jinja): `/` chat shell + `/product/*`, `/resources/*`, and `/about/*` content pages.
- `app/agent_loop.py` — ReAct multi-step loop; pause/resume for tool confirmations; vision mode; empty-content fallbacks.
- `app/llm_client.py` — the ONLY module that knows either backend's wire format (LM Studio + OpenRouter, both OpenAI-style streaming; tool-call deltas, context detection). Provider state is **runtime module globals** here (`_provider`, `_openrouter_*`); `.env` is persistence only, read once at startup.
- `app/model_manager.py` — `models.json` aliases; drives the `lms` CLI (load/unload models, start/stop server).
- `app/db.py` — raw sqlite3, no ORM: `conversations` + `messages`, WAL, guarded in-code migrations.
- `app/config.py` — pydantic-settings `settings` singleton; defaults here, `.env` overrides.
- `app/tools/` — `@tool` registry; SAFE (fs / system / web), SCOPED_WRITE (fixed-target Style Lab CSS update/reset), and CONFIRM_REQUIRED (write_file, append_file, make_directory, run_command). `list_directory` is backend-confined to the repository root derived from `fs_tools.py` and rejects resolved paths outside it.
- `app/command_guard.py` — fail-closed classifier for `run_command`: BLOCK (refused unconditionally inside `run_command` itself, no override) / CONFIRM (today's human gate, the default) / ALLOW (known read-only, lets `agent_loop._needs_confirmation` skip the confirm prompt). Rules live in code, not `.env` — deliberately not a tunable. Recursively unpacks `$()`/`@()`/`&{}` PowerShell subexpressions so a destructive verb can't hide inside an otherwise-safe command; any such nesting is capped at CONFIRM even when benign.
- `app/static/` + `app/templates/` — no-build vanilla JS/CSS/HTML; `app.js` parses SSE manually (fetch reader, since endpoints are POST).
  Jinja inheritance: `base.html` → `index.html` (app shell) and `base.html` → `page.html` → `product/*.html` / `resources/*.html` / `about/*.html` (content pages); shared footer partial `_footer.html`; `style.css` (app) + `pages.css` (content pages only).
- `app/sse.py` / `models.py` / `platform_info.py` / `stt.py` — SSE framing · request DTOs · shell selection · faster-whisper STT.
- `.claude/agents/` + `.codex/agents/` — opt-in specialist roles: no subagent is spawned unless Nemanja explicitly requests agents, delegation, or parallel work in that request. Codex pins Luna/medium for backend + frontend, Mini/high for database, Mini/low for verifier, and Sol/high read-only for reviewer; the agent files are the source of truth. `.codex/config.toml` makes `CLAUDE.md` their shared project instruction source.

## What exists (all working, all committed)

Streaming SSE chat with collapsible reasoning blocks · multi-step tool loop with human Approve/Deny
flow (survives page reload) · SQLite per-conversation memory, auto-titles, context gauge · web tools
(DuckDuckGo search, offset-paginated `fetch_url`, weather) · in-chat model switching via `lms` CLI ·
LM Studio health polling + in-app ▶ start / ■ stop server and ▲ load / ⏏ unload model buttons ·
**OpenRouter cloud mode** (🔑 API key dialog → `POST /api/provider/connect` validates the key via
OpenRouter's authenticated `GET /key`, caches its `/models` list into a searchable datalist, swaps the
shared httpx client; ⏏ Disconnect returns to LM Studio, key stays in `.env` for one-click reconnect;
mode + key + model persist via `persist_env_values()` in `config.py` and auto-resume at startup;
`OPENROUTER_FREE_ONLY=true` by default — only zero-cost `:free` chat models are listed [~15 live],
and non-text-output catalog entries like Google Lyria are always excluded via `_is_chat_model`;
the key input is deliberately NOT `type=password` — that made Chrome's password manager hijack the
model-search datalist as a fake login form; CSS `-webkit-text-security` masks it instead) ·
**no-auto-load guard** (`_turn_blocker()` in `main.py` refuses chat/confirm turns via an SSE `error`
event — before any DB write — when no model is loaded locally or none selected on OpenRouter, so
LM Studio's JIT load can never trigger silently) · voice input (faster-whisper on CPU, so it
never competes for VRAM) · image input (client-side downscale, persisted per message) · dark neon
**Tron-inspired liquid-glass UI** across chat, content pages, dialogs, footer, and Breakout
(Product pages plus a styled `/resources/readme` guide) · cross-platform shell (PowerShell/bash) ·
three-tier command guard in front of `run_command` (BLOCK/CONFIRM/ALLOW — see `app/command_guard.py`) ·
**Breakout minigame** at `/game` (canvas, WebAudio SFX, SQLite `scores` table via `GET/POST /api/scores`,
SAFE `get_game_scores` tool — chat shell untouched: own `game.html`/`game.css`/`game.js`, `app.js` never loads there) ·
four progressive Breakout layouts: classic 10×6 wall, durability mosaic, a 20×12 ZYLE wordmark, then a
full 48×24 micro-brick DAJA-CHAN tribute with a red heart and large tortoiseshell-cat face;
amber Hard bricks need 2 hits, danger-pink Ultima bricks need 3, and shiny silver Piercer bricks need 5
(remaining-hit pips are drawn in-canvas). Destroying a Piercer grants 10 active-play seconds where the
silver-ringed ball instantly destroys contacted bricks and passes through them without reflecting.
Level 2 also hides a pink 4-hit **Splitter** (randomly placed each build, three-dot telegraph): breaking it
forks the ball into three; a life is lost only when the LAST live ball drains, then play resumes single-ball.

Verify e2e: run the app (command in `CLAUDE.md`), send a message; "weather in Belgrade" triggers tools — or dispatch the `verifier` agent.

## Status (2026-07-19)

- **Isolated live Style Lab is implemented and tested (new, uncommitted).** `/style-lab` is linked from the footer (opens in a new tab) and renders a fixed component canvas inside an iframe. `style-lab.js` fetches `style-lab.css` with no-store polling every second and injects it as text into the iframe, so editable CSS cannot reach the lab shell, chat, or approval UI. `update_style_lab_css` is a new `SCOPED_WRITE` tool with no path argument; it atomically replaces only the fixed lab file after enforcing a 128 KiB cap, basic structural balance, no nulls/HTML style tags/external `@import`, `url()`, or protocol strings, and file-link/path checks. `reset_style_lab_css` restores `style-lab.default.css`, which the scoped writer cannot modify. Local models may still select generic `write_file`; an exact lab-file call now bypasses confirmation and delegates inside `write_file` to the same scoped validator, while every other path remains confirmation-required. Tests cover update/reset, schema/risk tier, invalid/external/oversize content, link refusal, generic-writer delegation, and confirmation isolation; full suite passes (16 tests, 2 link cases skipped without Windows symlink privilege). Real HTTP checks confirmed page/frame/script/footer wiring, no-cache CSS serving, and a live write→protected-reset cycle. Browser visual QA was unavailable because no in-app/Chrome browser was connected in this session.

- **`list_directory` workspace boundary is implemented and tested (new, uncommitted).** Relative paths resolve from the ZyleBot repository root (`F:\local_mythos` in this checkout); absolute outside paths, `..` traversal, similarly prefixed sibling folders, and resolved symlink/junction escapes are denied before enumeration. The tool schema advertises the allowed root. Coverage lives in `app/tests/test_fs_tools.py`; the full suite passes (7 tests, with the link-creation case skipped on Windows when the process lacks symlink privilege). This boundary applies specifically to `list_directory`; the other filesystem tools and approved shell commands retain their existing scopes.

- Everything above through the command guard is committed (through `aabd63f`). Uncommitted: current-state docs, aligned Claude/Codex specialist definitions, project-scoped Codex configuration, and the whole Breakout feature (new: `game.html`/`game.css`/`game.js`/`tools/game_tools.py`; edits: `pages.py`, `db.py`, `models.py`, `main.py`, `tools/__init__.py`, `_footer.html`, README).

- **OpenRouter provider + stop server + no-auto-load guard (new, uncommitted).** Edits: `config.py`
  (provider settings + `persist_env_values`), `llm_client.py` (provider seam), `model_manager.py`
  (`stop_server`), `models.py` (`ProviderConnectRequest`), `main.py` (provider routes, provider-aware
  health/models/model, `_turn_blocker`), `index.html`/`app.js`/`style.css` (buttons, `<dialog>`, datalist
  picker), `.env.example`. Verified live: boot unchanged in LM Studio mode; guard refuses chat with
  nothing loaded (SSE error, no DB write, no JIT load); bad key → 401 with `.env` untouched; server
  stop → unreachable → start restores. **Not yet exercised with a real key** (good-key connect, OR
  chat/tools, disconnect, auto-resume) — Nemanja tests that with his OpenRouter key. The system
  prompt is provider- and model-aware: `agent_loop.build_system_prompt()` fills an `{origin}`
  placeholder from `llm_client.get_provider()` + `get_active_model()` (local-via-LM-Studio vs
  cloud-via-OpenRouter, tools always local either way) — the bot now names its own active model.

- **Breakout is done and verified.** Level 1 remains the classic 60-brick wall. Level 2 is a sparse 16×9
  mosaic (24 regular / 29 Hard / 8 Ultima / 1 Piercer); level 3 uses a 20×12 ZYLE mask
  (27 Hard / 30 Ultima / a Piercer in Y's open gap and a Splitter in E's — mask digit 4 = Splitter,
  5 = Piercer, in any level mask). Level 4+ is a completely filled 48×24
  DAJA-CHAN tribute (1,152 one-hit micro-bricks): neon-yellow background, neon-red title and far-left heart,
  and Daja's portrait. The portrait was redrawn by Claude from her photo after the first version rendered
  ~2:1 stretched, then refined per Nemanja's marked-up screenshot: now a 30×19 string-art block (`catArt`,
  anchored row 5/col 9 — the level-4 cell pitch is ~15.9×15 px so art proportions render true) with square
  face, pointed brown-fringed ears, round green eyes/dark pupils, tan forehead blaze, brindled cheeks,
  cream-framed dark nose, a black mouth line running nose→lip→chin shadow→bib, white lip/chin/bib cascade,
  and full-length two-row whiskers per side (lower pair further out; they reach grid cols 9–13 / 34–38,
  clear of the heart which ends at row 14). Preview: `scratchpad daja_v2.py` replicates the generator to PNG.
  Each hit scores, only the final hit decrements `bricksAlive` and raises speed, and clearing level 2 builds
  the ZYLE layout at level speed 420. Piercers take 5 ordinary hits; breaking one grants 10 seconds of
  gameplay-time piercing, which instantly finishes any contacted brick without bouncing and preserves the
  ball's direction while its silver ring is visible. Deterministic JS harnesses verified pattern dimensions,
  2/3/5-hit durability, Piercer placement/countdown/no-bounce collision flow, scoring, rendering, and the
  level-3→4 transition at speed 460. A live browser check verified the revised level-4 face, full yellow wall,
  and ATTRACT-screen `4` shortcut; `/game` + `game.js` serve 200. `game.js` is 869 lines. Existing safeguards remain: GAME_OVER
  exits only by submit/skip/keyboard; arrows work while the ball is glued; ATTRACT shows a fresh dimmed wall;
  score POST validation/order and SAFE score tool work. Dev shortcut: pressing 1/2/3/4 on ATTRACT starts at
  that level's natural arrival speed (`startGame(startLevel)`; digit keys are ATTRACT-only).
  Multiball (Claude): the ball is now `game.balls` (array) throughout physics/render; drained balls are
  marked dead and filtered post-step, the life resolves only when the array empties, and level-clear
  early-returns because `resetBall()` replaces the array. The Splitter is assigned in `buildBricks` (digit
  masks unchanged — one random non-Piercer mosaic brick becomes 4-hit `splitter: true`), splits fan
  ±0.55 rad around the breaking ball's heading at `game.speed`, and pierce applies globally to all balls.
  `game.js` is 925 lines. Not yet play-verified by Nemanja.

- **Liquid-glass visual layer is implemented (new, uncommitted).** `style.css` defines shared translucent
  surfaces, backdrop blur/saturation, soft highlights, rounded depth, ambient neon blooms, and a calmer Tron
  grid for the chat shell. `pages.css` extends the system to editorial cards, navigation, CTAs, tables, and
  diagrams; `game.css` applies it to the HUD, stage frame, controls, and overlays while keeping the canvas crisp.
  Responsive overrides preserve the compact mobile layout. No HTML or JavaScript behavior changed; CSS brace
  checks and the standard unittest suite pass. Refresh the browser to evaluate or tune the aesthetic.

- **RL Breakout Double-DQN + AI showcase are implemented and smoke-tested (new, uncommitted).**
  `rl/` contains the deterministic level-1 Gymnasium physics port, torch-free shared 78-value
  observation builder, hand-built replay buffer / target network / Double-DQN agent, and
  train/play/export/plot scripts. Heavy dependencies are isolated in `rl/venv` (Python 3.14,
  torch 2.13.0+cu126 sees CUDA); the app venv was unchanged. An exported 5,000-step smoke policy
  lives in `rl/policy/` (eval score 48; not a trained master policy). `/ws/game-agent` uses pure
  numpy inference from `app/rl_policy.py`; `/game` has an `AI: OFF/ON` button plus `A` shortcut,
  30 Hz state streaming, auto-relaunch after life loss, safe level-2 no-op, and offline fallback.
  Eight RL parity tests and 21 app tests pass (2 Windows link-permission skips); the CUDA smoke
  run produced finite loss 0.0420, CSV/checkpoints, greedy playback, plot, and export. Live HTTP
  `/game` returned 200 and the WebSocket returned an action for valid state plus action 0 for bad
  state. Visual browser interaction remains unverified because no in-app browser was connected;
  Nemanja still needs to run the long 0.5–3 M-step training and play-check the UI. Normal training
  keeps the 10k random warmup; runs of 10k steps or fewer shorten warmup to make the specified 5k
  end-to-end smoke test exercise learning and emit a finite loss.

- **RL fine-tuning controls are implemented (new, uncommitted).** `rl.train` now accepts
  `--learning-rate`, `--eval-episodes`, and `--fork-run` alongside the arena work's opt-in
  `--live-export`. Resumes preserve the optimizer/checkpoint learning rate unless explicitly
  overridden. Forking creates a collision-safe new run directory, re-evaluates the source policy
  as a comparable baseline, and seeds that branch with local `best.pt`/`latest.pt`, avoiding
  duplicate/out-of-order rows in the source log. Changing eval sample size on a non-fork resume is
  rejected. Checkpoints now record learning rate and eval count. Eleven RL tests pass, including
  checkpoint metadata/LR round-trip/run-directory coverage; a real CPU fork smoke from the 5k
  checkpoint verified `3e-5`, two-episode re-baselining, isolated log/checkpoints, and one added step.

- **RL reward shaping and stability controls are implemented (new, uncommitted).** The training
  environment accepts a configurable learning-only paddle-contact bonus (`--paddle-hit-reward`;
  optional, not part of the current stability-first recommendation) and counts each real descending-ball collision exactly once.
  It never changes browser/game score, and greedy checkpoint evaluation remains unshaped raw score.
  The DQN now supports global gradient clipping (`--gradient-clip-norm`; fresh-run default `10`) and
  records it in checkpoints; pre-feature checkpoints resume unclipped unless explicitly opted in.
  Paddle reward is also checkpointed and changing it on an ordinary resume is rejected—use
  `--fork-run` so reward semantics cannot mix inside one CSV. RL suite coverage includes
  exact-once shaping, unchanged raw score/legacy reward, clipping invocation, and old-checkpoint
  compatibility. A real one-step CPU fork from the old 5k checkpoint also verified the full CLI and
  persisted `paddle_hit_reward=0.05`, `gradient_clip_norm=10`, `learning_rate=3e-5`, and eval metadata.

- **Optional overnight plateau adaptation is implemented (new, uncommitted).**
  `rl.train --adaptive-patience-steps N` watches greedy raw-score improvements; after `N` steps
  without a new best it halves Adam LR (floor `1e-5`) and reheats epsilon to `0.15` for 100k steps,
  linearly cooling to the normal `0.05`. A new best resets the clock; adjustments also reset it and
  are capped at three. Reward shaping never auto-changes, avoiding a safe-bouncing objective. The
  patience clock, adjustment count, LR, and exploration pulse are checkpoint-safe, while each event
  is recorded separately in `adaptive.csv`. Default behavior remains disabled/unchanged; adaptation
  is no longer recommended until an anchored 500k validation branch proves stable. A real
  one-step CPU fork with patience 1 triggered the controller end to end (`3e-5 -> 1.5e-5`, epsilon
  pulse through step 105001), persisted the state, and wrote the expected `adaptive.csv` event.

- **Fine-tuning replay warmup is corrected (new, uncommitted).** Fresh training still uses the
  required 10k fully random warmup, but any resumed checkpoint now refills its new replay buffer
  using the loaded policy's epsilon-greedy actions. Previously every fork collected 10k random-policy
  transitions before updating the champion, a likely cause of the repeatable post-fork collapse.
  `--reset-optimizer` optionally discards inherited Adam moments after applying any requested LR.
  The older 5M shaped/adaptive experiment is not the current recommendation because it collapsed.

- **Anti-stall reward shaping is implemented (new, uncommitted).** Optional
  `--stall-paddle-hits 10 --stall-penalty 0.1` works with the recommended `0.01` contact bonus:
  contacts 1–9 earn the bonus, contact 10 subtracts `0.1`, so a ten-return no-brick block nets zero.
  Each brick hit resets the counter; continued stalling is penalized once per non-overlapping block
  of ten. Counts and penalty events are exposed in env `info`, configuration is checkpointed, and
  changing it on a non-fork resume is rejected. Greedy evaluation remains raw score and unshaped.
  The stronger `0.15` penalty is deliberately reserved for a later fork after evidence the agent has
  learned the neutral rule.

- **RL champion gating and anchored fine-tuning are implemented (new, uncommitted).** The completed
  5M shaped run's apparent `870/10` winner was selection noise: two independent paired 50-game audits
  put the original champion at `659.8`/`656.8` versus the nominee's `326.8`/`403.8`. That original
  step-1.73M policy was restored before conservative fine-tuning. Run `20260720-125627` subsequently
  produced the current deployed step-3.05M champion: its 200-game promotion gate scored `730.65`
  versus `635.95` (+94.7, lower95 +23.8), and a separate fresh paired 200-game audit scored `695.3`
  versus the original's `618.15`. The deployed `rl/policy/meta.json` records step 3.05M with a
  standalone greedy `eval_score` of `683.4` — the `730.65`/`695.3` figures above are 200-game *paired*
  audit means against the prior incumbent, a stricter/different measurement than the single-policy eval.
  Quick 10-game evals only nominate candidates; every gate attempt is written to `gates.csv`, and
  only accepted champions replace `best.pt`/live export.
  Fine-tuning can reserve 50k frozen-champion transitions (`--anchor-steps 50000`) and draw 25% of
  each batch from that protected slice (`--anchor-fraction 0.25`), while `--learn-every 4` reduces
  destructive update pressure and speeds collection. README now recommends an unshaped, LR `1e-5`,
  500k validation fork before any long run. A real 400-step CPU fork verified robust baseline
  creation, anchored/every-fourth learning, persisted metadata, and rejection of a lucky but
  statistically invalid candidate.

- **Champion gates now preserve and fully confirm promising nominees (new, uncommitted).** Every
  positive 50-game paired result automatically expands to 200 games on disjoint continuation seeds,
  even if the initial 50-game confidence bound already appears promotable. Only the final 200-game
  paired confidence decision can promote it; a still-positive rejection is archived as
  `nominees/step-<N>.pt` and marked in `gates.csv` instead of disappearing. This closes the early-pass
  winner's-curse gap exposed by the step-2.87M checkpoint. The maximum is configurable/checkpointed
  via `--champion-max-eval-episodes` (recommended `200`).

- **Prioritized replay + n-step returns are implemented (new, uncommitted).** Replay buffers now use
  an efficient proportional sum tree (online and protected-champion partitions remain separate),
  sample by TD-error priority with configurable `--priority-alpha` (recommended validation value
  `0.6`), and apply importance-weighted per-item Huber loss with beta annealed from `0.4` to `1.0` on
  a checkpointed fine-tuning-local counter. `--n-step 3` accumulates discounted rewards, flushes every
  terminal tail without leaking across episodes, and stores the exact bootstrap discount. These work
  with the 25% champion anchor and every-fourth update path. Gate nominations are skipped during the
  frozen-anchor warmup. All **21 RL tests pass**; real 1k-step CPU and CUDA forks exercised anchor
  collection, three-step insertion, prioritized sampling/updates, weighted learning, metadata, and
  gate handling. The CUDA path sustained ~620 steps/s in the smoke and persisted 176 local PER-beta
  updates without touching the deployed policy.

- **1.0M-step anchored validation fork is complete — champion held (`20260720-195555`).** Forked from
  the deployed 3.05M champion with the README stability-first recipe (unshaped, LR `1e-5`,
  `--reset-optimizer --learn-every 4 --anchor-steps 50000 --anchor-fraction 0.25 --n-step 3
  --priority-alpha 0.6`) and run to step 4.05M — 1.0M trained steps, 2× the 500k target — before Nemanja
  stopped it. Result: **47 gate attempts, zero promotions.** The single `accepted=True` row in
  `gates.csv` is attempt 0 (the fork re-establishing the 3.05M policy as its own incumbent baseline,
  `mean_difference=0`, incumbent==candidate==`683.4` — matching the deployed `meta.json`). Two candidates
  actually *won* their full 200-game paired audit on the mean (`step-3390000` +34.4, `step-4010000` +28.1)
  but both failed the +10 lower-95 promotion bar (lower-95 `-37.1`/`-41.3`), so they were rejected and
  archived under `nominees/` rather than promoted. `best.pt` was never overwritten (mtime = fork start)
  and the deployed `rl/policy/*` is untouched — the browser showcase still serves the 3.05M champion.
  Decisive live evidence that the winner's-curse gate holds the line and that the conservative recipe
  found no statistically real improvement over 3.05M. (`gates.csv` columns:
  `steps,attempt,quick_score,candidate_mean,incumbent_mean,mean_difference,lower_95,episodes,accepted,archived`.
  Note `eval_score_mean` in `log.csv` is the run's own greedy diagnostic, swings ~430–990, and is *not*
  the promotion signal — only paired `gates.csv` decisions are.)

- **Level-1 plateau-breaking levers are implemented and tested (new, uncommitted).** To push past the
  stuck ~683/≈20-brick single-life ceiling, four opt-in flags change the *learning problem* (diagnosed
  root causes: replay coverage collapse, a ~3 s credit horizon, exhausted exploration).
  `rl/breakout_env.py` gains **training-only** `--curriculum-clear-max F` (at each reset pre-clear a
  uniform-random fraction `< F` of the wall and advance ball speed to match, so the buffer holds
  fast-ball endgame states) and `--curriculum-prob P` (apply that pre-clear on only fraction `P` of
  resets; the rest stay full-board openings). `rl/dqn/agent.py` makes `--gamma` (discount/horizon) and
  `--epsilon-decay-steps` (exploration schedule) instance attributes, checkpointed with legacy defaults
  (`0.99` / `150k`). Eval always uses a plain full-board `BreakoutEnv()`, so `eval_score_mean` stays
  comparable. All follow the semantic-flag pattern (validate → resolve → resume-guard → checkpoint →
  load): **curriculum-clear-max, curriculum-prob, and gamma are fork-required** to change on resume;
  epsilon-decay is a free forward-only override. New `rl/audit.py` runs a standalone paired A/B between
  two checkpoints on identical seeds (reuses `evaluate_episodes` + `paired_gate_decision`) and prints a
  PROMOTE/KEEP verdict on the same `+10` lower-95 bar. **38 RL tests pass** (was 21); CPU smokes gave
  finite loss and checkpoints carrying all four fields; audit.py ran clean.
  - **Expensive lesson — curriculum must MIX, not replace.** `--curriculum-prob 1.0` (clear *every*
    reset) leaves only ~4% full-board episodes, starving the slow opening that eval measures. Two live
    CUDA runs proved it: (1) a **fresh** run (`20260720-223715`, clear-max 0.6, prob 1.0, gamma 0.997)
    never learned — `eval` flat ~30–80 and `train_return` flat −4.4 through 400k steps; (2) a **fork** of
    the 3.05M champion (`20260721-003906`, clear-max 0.4, prob 1.0, gamma 0.99, anchor 50k/25%) *forgot*
    the opening — paired gate audits sat at ~390–434 vs the champion's ~700 and never recovered. Both
    killed. `--curriculum-prob ~0.5` (default stays `1.0` for back-compat) is the corrective and is now
    the recommended value everywhere. Also note two fine-tunes of the champion have now failed to beat
    683 (the earlier plain 1M null run + this curriculum fork), so its basin is sticky — a mix-fork is
    the next test and a fresh mix run the fallback.
  - README documents both recommended commands (fresh 1M mix run; mix-fork of the champion). Nothing
    auto-exports (no `--live-export` on experiments), so the 3.05M champion stays the live policy;
    **Nemanja runs training and A/B-audits `best.pt` vs `rl\runs\20260720-125627\best.pt` before any deploy.**

- **AI Spectator Arena is implemented and verified (new, uncommitted).** Built to
  `briefs/rl-breakout-spectator-arena.md`. `/game/arena` runs 1/2/4/6 independent spectator games
  (default 4, hard cap 6) as same-origin iframes of the new board-only `/game?embed=1&ai=1&spectator=1&slot=N`,
  with `START ALL` / `STOP ALL` / literal `POP OUT` (stable per-slot window names; popped slots stop
  their iframe to avoid double inference; blocked popups degrade to `POPUP BLOCKED`). Spectator rules
  live in `game.js` behind `spectator.active` (params-gated, ordinary `/game` untouched): auto-start
  level 1, forced-silent without writing the shared `MUTED_KEY`, no score POST, loop level 1 on
  GAME_OVER / level-1 clear (never builds level 2), and `postMessage` ready/run-end/offline to
  `window.opener || window.parent`. The arena (`game-arena.js`) accepts only same-origin, shape- and
  slot-validated (1–6) messages, aggregates **viewer** stats (runs / clears / clear% / mean / high —
  explicitly NOT trainer `eval_score_mean`), and polls `GET /api/game-agent/status` every 2 s.
  Template refactor: shared `_game_board.html` included by `game.html` (full page + `AI ARENA` link)
  and new `game_embed.html` (body `game-embed`); new `game_arena.html`. `base.html`/`page.html`/
  `index.html`/`app.js`/`style.css` untouched.
  - **Atomic export + hot reload.** `rl/export_policy.py` now has a torch-free `publish_policy()`
    (staged temp files → `os.replace` meta.json → `os.replace` the `.npz` LAST as the commit marker;
    temps cleaned on failure) that also writes `observation_version`/`training_steps`/`eval_score`
    scalars *inside* the NPZ so fresh weights never pair with a stale `meta.json`. `app/rl_policy.py`
    stays numpy-only: a lazy singleton stat-checks the commit marker (throttled ≤1/s across sockets),
    fully validates a candidate (shape + `level1-v1`) before swapping, and keeps the last known-good on
    a bad/missing/wrong-version replacement (`no-policy` only if none ever loaded). `/ws/game-agent`
    re-fetches the policy per message so a swap takes effect mid-run. `rl.train --live-export` (opt-in,
    off by default) publishes each improved `best.pt` the same way; the current run still publishes via
    manual `rl.export_policy --ckpt <run>\best.pt` (both hot-reload without a restart).
  - **Verified:** full app suite **39 tests green** (2 Windows-symlink skips) incl. new pages
    (full/embed/arena, DOM-id preservation), atomic-export/strict-meta/staged-failure, hot-reload
    swap + corrupt/wrong-shape/wrong-version/missing keep-last-good, and `/api/game-agent/status`
    unavailable/loaded/reloaded; RL env suite **11 green**; `node --check` on both JS files + CSS brace
    balance OK. Live: all routes 200, status read the live policy (step 1.73M / eval 884 mid-training —
    hot reload sees the current file), and a **4-client / 30 Hz / 60 s** WS harness passed (5092 msgs,
    avg <1 ms, peak 2.27 ms round-trip, `/status` max 12.8 ms, no disconnects, all actions in 0|1|2).
    **Browser/visual QA (iframe grid render, real pop-out windows, spectator loop visuals) is still
    for Nemanja** — no in-app browser was connected this session.

- Backlog (build only if asked): `run_python` + `delete_file` action tools · bubble max-width cap (~720px) · headless-browser fetch for bot-walled sites · brave/tavily search keys. Possible follow-up worth a deliberate decision (not yet built): narrow the ALLOW tier so `cat`/`type`/`Get-Content` (which can read arbitrary file content, not just enumerate) require confirmation even for non-protected paths — currently accepted as-is since it matches the original spec and CONFIRM was always the fallback before this feature existed.

## Gotchas — expensive lessons, keep these

- **12 GB VRAM**: one model at a time; much past ~64k context spills into slow *shared* GPU memory (Task Manager → GPU). Per-model LM Studio GUI settings (flash attention / KV-cache quant) can't be set via `lms load` — small spills are accepted, don't chase them.
- **Reasoning channel**: these models stream `reasoning_content` separately from `content` — never merge. `content` sometimes comes back empty after a tool result; the loop retries and synthesizes a fallback so a blank bubble never shows ("Here's what I found:" bullets = the model, not a bug; `/no_think` doesn't help — tested).
- **Vision ⊻ tools**: attaching tools makes LM Studio drop the image, so image turns run tools-off and older images collapse to placeholders — re-paste an image to re-examine it.
- **`fetch_url`** pages via `offset`; `TOOL_MAX_FETCH_CHARS=48000` in `.env` — an 8k cap once caused an infinite offset-0 refetch loop. Some sites block scraping; falling back to search snippets is expected.
- **LM Studio ids mutate**: ids silently gain an `@<quant>` suffix once a second quant of the same base model is downloaded — if a model stops resolving, re-check `lms ls` and fix `models.json`.
- **Windows console is cp1252**: set `PYTHONUTF8=1` for anything printing model output.
- **Config keys** go in `config.py` + `.env` + `.env.example` — all three, every time (past bug).
- **OpenRouter mode must never touch LM Studio's native API**: `_fetch_native_models()` early-returns unless provider is lmstudio — it requests an *absolute* URL to the LM Studio origin, so the client's OpenRouter base_url would NOT protect it. Keep that guard if you refactor. Frontend branches everything on `data.provider` from `/api/health`; the lms-CLI endpoints 409 in openrouter mode.
- **Chat refusals must be SSE, not HTTP errors**: `postAndRead()` in `app.js` never checks `res.ok`, so guard refusals stream `event: error` + `done` on a 200 — an HTTP 4xx would silently break the composer.
- **Steering the local 9B**: narrow scope, exact target, pinned output format, one step at a time.
- **Content pages vs app shell**: `app.js` hard-crashes without the chat DOM — it loads only via `index.html`'s `scripts` block, never on `page.html` descendants. `style.css` sets `body { overflow: hidden }` for the app layout; content pages scroll only because `pages.css` overrides it via `body.page`.

## Key facts

- Model ids / aliases / context lengths: **`models.json` is the source of truth** — don't duplicate it here.
- `lms` CLI path: `shutil.which("lms")` → fallback `~/.lmstudio/bin/lms`.
- Notable `.env`: `TEMPERATURE=0.3`, `AGENT_MAX_STEPS=12`, `TOOL_MAX_FETCH_CHARS=48000`, `SEARCH_PROVIDER=duckduckgo`, `USER_NAME=Nemanja`.
