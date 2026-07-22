# ZyleBot — Handoff (current state)

> Living *current-state* doc — what exists, what's in flight, and the gotchas that save debugging
> time. **The main (orchestrator) model updates this file every time work lands** — subagents
> report, they don't document. Not a changelog; git history is the changelog. Last updated: **2026-07-22**.
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

## Status (2026-07-22)

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

- **Champion-seeded neuroevolution — Phase 1 engine implemented and tested (new, uncommitted).** A
  torch-free genetic algorithm in `rl/evo/` that treats the DQN's MLP weights as a genome and optimizes
  the *real* game score directly (no gradients, no reward shaping) — a second attack on the sticky ~683
  plateau, and the basis of a "Code Bullet"-style live-evolution viewer (Phase 2, not yet built).
  `genome.py` codecs the six weight arrays to/from a flat float32 vector in the *exact* `rl.export_policy`
  order and runs the same numpy ReLU forward pass as `app/rl_policy.py` — a unit test asserts bit-for-bit
  parity, so what evolves is bit-for-bit what the arena plays. `evaluate.py` scores a genome as mean raw
  score over a fixed seed set using the plain headless `BreakoutEnv` (single-life, no curriculum/shaping —
  identical to greedy eval), parallel across CPU cores via `multiprocessing` (Windows spawn verified,
  serial and parallel give identical fitnesses). `population.py` is a classic GA: champion-seeded gen-0
  (index 0 is the unmutated champion), elitism, tournament selection, uniform crossover, annealing-sigma
  Gaussian mutation. `evolve.py` (CLI `python -m rl.evo.evolve`) runs generations on per-gen *training*
  seeds (fair, decorrelated) but scores each generation's best on a **fixed validation seed set** so the
  all-time-best curve is trustworthy, not seed-luck; logs `evo_log.csv`, saves `best.npy` on validation
  improvement, and ends with a paired audit of the best genome vs the champion on the **same +10 lower-95
  bar** as every DQN champion (reuses `rl.train.paired_gate_decision`). **The deployed champion in
  `rl/policy/` is never touched** — `--live-export` publishes only to a separate `rl/policy_evo/` slot,
  and promotion to the live game stays a manual, audited step. 14 evo unit tests pass (codec round-trip,
  champion-load shape, forward parity, mutated-genome export round-trip, operator correctness, rollout
  determinism); serial + parallel CLI smokes ran clean end-to-end incl. the audit. Run it (rl venv):
  `rl\venv\Scripts\python.exe -m rl.evo.evolve --generations 100 --population 60 --eval-seeds 8 --workers 8`.
  Rough cost: ~pop×eval-seeds rollouts/gen, embarrassingly parallel — order minutes for 100 gens on 8 cores.

- **Champion-seeded neuroevolution — Phase 2 live "6-brain" viewer implemented and tested (new,
  uncommitted).** A Code-Bullet-style browser view at **`/game/evolution`**: six boards each play a
  *different* evolved genome (the current generation's top 6) while a live chart plots gen-best/gen-mean
  fitness vs generation with a dashed "champion line to beat". Fully decoupled from evolution via files —
  no shared memory, matching the existing hot-reload philosophy:
  - **Engine side (`rl.evo.evolve --live-export`)** publishes, throttled by `--viewer-interval` (default
    0.75s), the generation's top-6 genomes to `rl/policy_evo/slot{0..5}/breakout_policy.npz` (each an
    ordinary atomically-published policy dir) plus `rl/policy_evo/status.json` written LAST as the
    generation commit marker (generation, all_time_best, baseline, train_best/mean, sigma, per-slot
    fitness, capped best/mean history for the chart). The deployed champion in `rl/policy/` is still
    never touched. **Resilient to Windows file locks:** because the app reads slot files while the boards
    play (and Defender / the VS Code workspace watcher scan them), `os.replace` can hit `PermissionError`
    (WinError 5) mid-run; viewer publishing is cosmetic, so each replace retries briefly (`_try_fs`) and a
    still-locked frame is *dropped* (`publish_viewer_state` returns False, logged as "viewer frame
    dropped") rather than crashing the run — status.json is the commit marker, so a dropped frame just
    keeps the viewer on the last good generation. (Tip to reduce drops: exclude `rl/policy_evo` from
    Defender / `files.watcherExclude`.)
  - **App side** adds torch-free `app/rl_policy_evo.py` (independent per-slot hot-reload singletons
    reusing `rl_policy.NumpyPolicy`; never reads the champion path), `GET /api/evo/status` (plain file
    read → status.json or `{available:false}`), and `WS /ws/game-agent-evo?slot=0..5` (mirrors the
    champion WS but bound to one slot, re-fetching that slot's genome per message). `main.py` extracted a
    shared `_parse_level_one_state` used by both WebSockets.
  - **Frontend** adds `/game/evolution` route + `game_evolution.html` + `game-evolution.js` (polls
    `/api/evo/status` every 1.5s, builds the 6-tile grid reusing arena tile styles, draws the fitness
    chart on a `<canvas>`, per-tile fitness badges, RESYNC BOARDS) + `game-evolution.css`. `game.js` gets
    a **single** `evoMode` branch: with `&evo=1` a spectator board connects to `/ws/game-agent-evo?slot=N-1`
    instead of the champion WS — every other spectator rule (silent, loop, no score POST, postMessage) is
    reused. Ordinary `/game` and the AI arena are untouched. Boards play full games and pick up the newest
    genome at each restart, so the chart races ahead at evolution speed while games stay watchable.
  - **Historical progress chart is now live and artifact-driven.** `GET /api/evo/history` uses
    `app/evo_history.py` to scan DQN `log.csv`/accepted `gates.csv`, every evolution `evo_log.csv`, saved
    audit JSON, and versioned `rl/policy/history.json`; partial rows and absent/corrupt files fail soft.
    The bottom chart polls it every 1.5s independently of the top run status, so a new generation,
    validation improvement, or rotation re-score appears without editing/reloading source. It currently
    reconstructs **63 chronological stops** through the latest local `2167.8` validation high. Point
    shape/color distinguishes DQN gates, optimistic validation records, and audited/deployed scores; the
    champion cards and expandable full ledger also rebuild from the feed. Future `rl.evo.evolve` runs now
    create/update `run.json` with configuration/completion metadata and persist their final `audit.json`;
    standalone `rl.evo.audit` writes timestamped audit JSON too. New deployed scores are detected from
    `rl/policy/meta.json`, while the three prior champion records (`683.4 → 1128.8 → 1586.45`) live in
    `rl/policy/history.json`. `rl/policy_evo/` and all run artifacts remain ignored; deployed policy/history
    stay deliberately versioned.
  - **Verified:** 49 app tests pass (2 Windows-symlink skips) and all 60 RL tests pass. Coverage includes
    dynamic history extraction (DQN gates, evolution improvements, rotation drops, accepted audits,
    unknown future deployed policy), `/api/evo/history`, and the server-rendered polling page; JavaScript
    syntax, Python compile, and CSS brace checks pass. Live TestClient reads **63 points**, with `2167.8`
    as the latest saved high. **Browser/visual QA (the
    iframe grid rendering, chart animation, boards actually playing evolved genomes) is still for
    Nemanja** — no in-app browser this session. To watch: start `... -m rl.evo.evolve --live-export
    --workers 8`, open `/game/evolution`, record with OBS. Phase 3 (optional in-browser ⏺ recorder) not built.

- **Neuroevolution BEAT the DQN champion — audited PROMOTE, not yet deployed (new, uncommitted).** Run
  `rl/runs_evo/20260721-025154` (champion-seeded, `--eval-seeds 24 --sigma 0.01 --sigma-min 0.003`,
  300 gens) climbed a genuine validation staircase 675→769→825→894→1051→1264→1441 and its saved
  `best.npy` **cleared a fresh 200-game paired audit vs the deployed 3.05M champion: evolved mean 1128.8
  vs champion 710.0, diff +418.8, lower-95 +318.2 (bar >10) → PROMOTE** (held-out seeds base 500000,
  distinct from the run's training/validation tags; wins 140/200 head-to-head). This is the first thing
  to beat the ~683–710 plateau that every DQN fine-tune failed on. The key was more eval-seeds (robust
  selection, not seed-luck) + smaller sigma (fine local search); an earlier run at 8 seeds / sigma 0.02
  found nothing (flat 675) — the 8-seed signal was pure noise (the *champion itself* scored 482–1236
  across different 8-seed draws). NOTE the run's `all_time_best`=1441.9 is max-selection-biased; the
  honest audited mean is ~1129. **DEPLOYED 2026-07-21, since SUPERSEDED by the curriculum champion
  (1586.45) — see the last bullet in this group for the currently live policy.** `best.npy` was exported to `rl/policy/`
  (`meta.json` now `training_steps=3050000` lineage, `eval_score=1128.8`), so the live game AI + arena +
  `/game/evolution` now serve the evolved brain (verified: NumpyPolicy loads it, weights match the genome).
  The previous 3.05M champion (eval 683.4) is recoverable from `rl/runs/20260720-125627/best.pt` (re-export)
  and was also backed up to scratchpad this session — rollback is a re-export.
  - **Follow-up runs hit diminishing returns — don't re-chase without a new idea.** Two runs seeded from
    the deployed 1129 champion failed to beat it. (1) `20260721-130409` (sigma 0.01, `--seed 0`) stalled
    flat and *degraded* (`train_mean` 682→400): sigma 0.01 is too coarse for an already-refined genome,
    **and reusing `--seed 0` re-validates on the exact 16 seeds the champion was selected on, so the
    baseline was its own overfit peak (1441.9) rather than its true ~1129** — an unbeatable-by-construction
    target. Lesson: when seeding from a previous winner, change `--seed` so validation is honest.
    (2) `20260721-134714` (sigma 0.005, `--seed 1`, `--val-seeds 32`) was healthy — honest baseline 1078.4,
    climbed to 1338.1 by gen 44 — but its best **failed a 200-game paired audit vs the deployed champion:
    1213.8 vs 1128.8, diff +85.0, lower-95 −21.9 (bar >10) → KEEP CHAMPION.** A +85 mean sits inside the
    ±107 noise margin of 200 single-life games. Stopped at gen 88. Net: 683→1129 was the real unlock;
    further gains look noise-level for this architecture/observation.
  - **Ops note:** `--workers 10` on the 12-core box makes the desktop unusable while running; use `--workers 6`
    if you need the PC. Ctrl-C is safe — atomic writes clean up, no stray temps, champion untouched.
  - **Diagnosis of the remaining gap (120-episode measurement, 2026-07-21).** Theoretical max for one life
    is **3000** (all 60 bricks: 10×(70+70+50+50+30+30)); the deployed champion averages ~1210 and
    **cleared the board 0/120 times**. It is *not* near a ceiling. Failure has a precise location: only
    **1.7%** die early (<10 bricks — the opening is solid) and only **4.2%** ever reach >45 bricks, with
    median death at **27/60 bricks**. Since ball speed is `340 + 5×bricks` (cap 640), it is competent to
    ~475 and falls apart above it — and evolution can't select for late-game skill it samples in only 4%
    of episodes.
  - **Curriculum-mixed evolution implemented to attack that gap (new, uncommitted).** `rl/evo/evaluate.py`
    takes a `curriculum=(clear_max, prob)` threaded through `rollout_score`/`genome_fitness`/
    `evaluate_population`/`_fitness_worker` (which accepts both the 3- and 4-tuple payload for
    back-compat), reusing `BreakoutEnv`'s existing `curriculum_clear_max`/`curriculum_prob`. `evolve.py`
    adds `--curriculum-clear-max` (default 0, try 0.6) and `--curriculum-prob` (default **0.5** — the mix;
    it warns at ≥0.95 because `prob 1.0` starved the opening and killed past runs). **Training fitness
    only** — the baseline, per-generation validation, and the final audit always use a plain full board,
    so `val_score`/`all_time_best` stay comparable across runs (`train_*` columns are NOT comparable to
    non-curriculum runs, since pre-cleared boards have a lower max score). Curriculum draws come from the
    env's seeded RNG, so identical seeds give identical boards to every genome — selection stays fair.
  - **Evolution → DQN bridge implemented (new, uncommitted).** `rl/evo/to_checkpoint.py` (CLI
    `python -m rl.evo.to_checkpoint --genome <.npy|.npz> --output <.pt>`) maps the six genome arrays onto
    `QNetwork`'s `layers.{0,2,4}` params, loads them into both online and target nets, and writes a real
    trainer checkpoint via `rl.train.save_checkpoint`, so gradient fine-tuning can resume exactly where
    evolution stopped (defaults `agent_steps=3.05M` so epsilon stays annealed at 0.05 and DQN can't
    randomly explore the brain apart). A unit test asserts the converted agent's greedy actions match the
    numpy genome's exactly. **Verified live:** converted the deployed champion and `rl.train --resume`
    started at step 3,050,000 and evaluated it at **1514**. Caveat to remember: evolution optimised raw
    score, so outputs are action *preferences*, not calibrated Q-values — TD updates may re-scale them and
    degrade behaviour, hence the LR `1e-5` + `--anchor-steps 50000 --anchor-fraction 0.25 --learn-every 4`
    recipe; the paired gate means the worst case is wasted compute, never a lost champion.
  - **Standalone evo audit CLI (new, uncommitted).** `rl/evo/audit.py` — the evolution counterpart to
    `rl/audit.py` — paired-audits any genome against the deployed champion **without stopping a run**
    (`best.npy` is already on disk): `python -m rl.evo.audit --candidate rl\runs_evo\<run>\best.npy
    --workers 4` (defaults: 200 episodes, incumbent `rl/policy/breakout_policy.npz`, held-out seed base
    500000, same +10 lower-95 bar). Backed by new `evaluate.score_episodes()` (parallel per-episode
    scores for one genome) and `genome.load_genome()` (accepts `.npy` genome or `.npz` policy; also used
    by `to_checkpoint`). **Always audit before deploying** — a run's `all_time_best` is a max over noisy
    generations and reads high.
  - **20 evo unit tests pass** (added curriculum wiring/determinism/payload-compat and the two
    conversion tests); curriculum + converter + train-resume + audit-CLI smokes all ran clean.
  - **>>> CURRENT LIVE CHAMPION: evolved+curriculum policy, audited 1586.45 (deployed 2026-07-21). <<<**
    Run `rl/runs_evo/20260721-180231` (seeded from the 1128.8 champion; `--eval-seeds 24 --val-seeds 32
    --sigma 0.005 --sigma-min 0.002 --seed 2 --curriculum-clear-max 0.6 --curriculum-prob 0.5`, 300 gens)
    climbed validation 1277.5→1961.6 across 9 steps. **Two independent 200-game paired audits both said
    PROMOTE:** seed-base 500000 → 1575.5 vs 1128.8 (+446.6, lower-95 +333.8); the run's own seed-base
    100042 → 1586.45 vs 1179.15 (+407.30, lower-95 +293.29). Deployed to `rl/policy/`
    (`training_steps=3050000` lineage, `eval_score=1586.45`, weights verified byte-equal to the audited
    genome); the outgoing 1128.8 champion was backed up to scratchpad, and every prior champion remains
    recoverable. Progression: **683 (DQN, hard-stuck) → 1128.8 (evolution) → 1586.45 (curriculum evolution)**.
  - **Lesson — late validation gains were largely validation-overfitting.** `all_time_best` rose
    1722.5→1961.6 over gens 151–243, but the audited truth barely moved: the gen-151 genome audited
    **1575.5** and the final gen-243 genome audited **1586.45** (≈+11, on different seed sets). So the
    val→audit haircut widened from ~0.91 to ~0.81 as the run kept maximising over the same 32 validation
    seeds. **Do not treat `all_time_best` as progress late in a run** — only a paired audit on fresh seeds
    counts, and the curriculum run's real ceiling was reached around gen 151.
  - **Validation-seed rotation implemented to fix that (new, uncommitted).** `--val-rotate-every N`
    (0 = off, default; try 50) redraws the holdout every N generations via
    `validation_seeds(seed, count, rotation)` and **re-scores both the incumbent best and the baseline on
    the fresh boards**, so every comparison stays inside one seed set and `all_time_best` cannot drift.
    A new `val_rotation` column is appended to `evo_log.csv`. **`all_time_best` may legitimately DROP at a
    rotation** — that is an honest re-measurement, not a bug; only compare values within a rotation block.
  - **New champion's failure mode has MOVED (120-episode diagnostic, post-deploy).** The 1586 champion:
    mean 1629.1, **35.2/60 bricks** (was 27.3), early deaths **0.8%** (was 1.7%), and reaches >45 bricks
    **21.7%** of the time (was 4.2% — a 5× improvement; the mid-game problem is solved). But **still
    0/120 full clears**, best run 56/60. The bottleneck is now the **endgame: the last ~15 bricks at ball
    speed 565–640**. Hence the next experiment: curriculum at `--curriculum-clear-max 0.85` (start with
    ~6–9 bricks left at near-max speed) rather than 0.6.
  - **DQN hybrid FAILED — do not retry without recalibration (run `rl/runs/20260721-220811`).** Converted
    the deployed 1586 champion via `rl.evo.to_checkpoint` and ran the full stability recipe (fork,
    `--reset-optimizer --learning-rate 1e-5 --learn-every 4 --anchor-steps 50000 --anchor-fraction 0.25
    --n-step 3 --priority-alpha 0.6`) for all 500k steps. The evolved brain entered at gate baseline
    **1641** (eval 2008) and **collapsed as soon as learning began** at step 3.10M (after the anchor
    warmup): eval fell to ~700 within 20k steps and ended at **634**, with `train_return` going negative
    (−0.63). **Zero promotions** (`gates.csv` has only the fork's attempt-0 baseline); `best.pt` still
    holds the evolved policy and `rl/policy/` was never touched (no `--live-export`). **Diagnostic tell:
    loss fell monotonically (0.234 → 0.031) while the policy got 2.6× worse** — the net fit Q-values fine
    for its own worsening behaviour. Root cause is structural, not a tuning problem: evolution optimises
    raw score, so the network's outputs are action *preferences*, not calibrated Q-values, and TD-fitting
    rescales them and destroys the argmax structure. Making this work would need a distillation /
    policy-evaluation warmup that fits Q-values while holding the argmax fixed — a real project, and DQN
    is now 0-for-every-attempt on this codebase. **Recommendation: put compute into evolution instead.**
  - **`--champion` now accepts a raw `.npy` genome** (uses `genome.load_genome`), so an interrupted
    evolution run can be continued by seeding the next run from its own `best.npy` — no deploy required.
    When doing so, **change `--seed`**: the previous run's validation seeds are the ones that genome was
    selected on, so reusing them gives an overfit (unbeatable) baseline.

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
