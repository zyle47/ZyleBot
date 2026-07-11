# ZyleBot — Handoff (current state)

> Living *current-state* doc — what exists, what's in flight, and the gotchas that save debugging
> time. **The main (orchestrator) model updates this file every time work lands** — subagents
> report, they don't document. Not a changelog; git history is the changelog. Last updated: **2026-07-11**.
>
> Companions: `CLAUDE.md` (shared project rules + Claude agent routing), `README.md` (user-facing setup/tools),
> `.codex/config.toml` + `.codex/agents/` (project-scoped Codex configuration and specialist roles).

## Where things live

- `app/main.py` — FastAPI app, lifespan, all `/api/*` routes; `/static/*` served no-cache.
- `app/pages.py` — all HTML routes (Jinja): `/` chat shell + `/product/*`, `/resources/*`, and `/about/*` content pages.
- `app/agent_loop.py` — ReAct multi-step loop; pause/resume for tool confirmations; vision mode; empty-content fallbacks.
- `app/llm_client.py` — the ONLY module that knows LM Studio's wire format (streaming, tool-call deltas, context detection).
- `app/model_manager.py` — `models.json` aliases; drives the `lms` CLI (load/unload models, start server).
- `app/db.py` — raw sqlite3, no ORM: `conversations` + `messages`, WAL, guarded in-code migrations.
- `app/config.py` — pydantic-settings `settings` singleton; defaults here, `.env` overrides.
- `app/tools/` — `@tool` registry; SAFE (fs / system / web) vs CONFIRM_REQUIRED (write_file, append_file, make_directory, run_command).
- `app/command_guard.py` — fail-closed classifier for `run_command`: BLOCK (refused unconditionally inside `run_command` itself, no override) / CONFIRM (today's human gate, the default) / ALLOW (known read-only, lets `agent_loop._needs_confirmation` skip the confirm prompt). Rules live in code, not `.env` — deliberately not a tunable. Recursively unpacks `$()`/`@()`/`&{}` PowerShell subexpressions so a destructive verb can't hide inside an otherwise-safe command; any such nesting is capped at CONFIRM even when benign.
- `app/static/` + `app/templates/` — no-build vanilla JS/CSS/HTML; `app.js` parses SSE manually (fetch reader, since endpoints are POST).
  Jinja inheritance: `base.html` → `index.html` (app shell) and `base.html` → `page.html` → `product/*.html` / `resources/*.html` / `about/*.html` (content pages); shared footer partial `_footer.html`; `style.css` (app) + `pages.css` (content pages only).
- `app/sse.py` / `models.py` / `platform_info.py` / `stt.py` — SSE framing · request DTOs · shell selection · faster-whisper STT.
- `.claude/agents/` + `.codex/agents/` — opt-in specialist roles: no subagent is spawned unless Nemanja explicitly requests agents, delegation, or parallel work in that request. Codex pins Luna/medium for backend + frontend, Mini/high for database, Mini/low for verifier, and Sol/high read-only for reviewer; the agent files are the source of truth. `.codex/config.toml` makes `CLAUDE.md` their shared project instruction source.

## What exists (all working, all committed)

Streaming SSE chat with collapsible reasoning blocks · multi-step tool loop with human Approve/Deny
flow (survives page reload) · SQLite per-conversation memory, auto-titles, context gauge · web tools
(DuckDuckGo search, offset-paginated `fetch_url`, weather) · in-chat model switching via `lms` CLI ·
LM Studio health polling + in-app ▶ start-server button · voice input (faster-whisper on CPU, so it
never competes for VRAM) · image input (client-side downscale, persisted per message) · dark neon UI
with website-style footer (Product pages plus a styled `/resources/readme` guide) · cross-platform shell (PowerShell/bash) ·
three-tier command guard in front of `run_command` (BLOCK/CONFIRM/ALLOW — see `app/command_guard.py`).

Verify e2e: run the app (command in `CLAUDE.md`), send a message; "weather in Belgrade" triggers tools — or dispatch the `verifier` agent.

## Status (2026-07-11)

- Everything above is committed (through `aabd63f`). Uncommitted: current-state docs, aligned Claude/Codex specialist definitions, and project-scoped Codex configuration; the redundant root `AGENTS.md` was removed in favor of the `CLAUDE.md` fallback.
- Uncommitted (2026-07-11): **product/about/resource pages + Jinja base layout** — `app/pages.py` owns all HTML routes; shared layout lives in `templates/base.html`/`page.html`/`_footer.html`. The 4 long-form pages under `templates/product/` are wired, the implementation-grounded `templates/about/how_it_works.html` is routed at `/about/how-it-works`, and the rewritten/updated root `README.md` is mirrored by the responsive `templates/resources/readme.html` page at `/resources/readme`. The footer README link now targets that page; GitHub uses an inline mark and `https://github.com/zyle47/ZyleBot`. LM Studio, Docs, Privacy, and Changelog remain dead anchors. `static/footer_demo.html` was deleted (superseded by `_footer.html`).
- Uncommitted (2026-07-11): **command guard for `run_command`** — new `app/command_guard.py` (`check_command`, stdlib-only, no new dependency) classifies every shell command as BLOCK/CONFIRM/ALLOW; wired into `app/tools/action_tools.py` (`run_command` refuses BLOCK before `subprocess.run`, updated tool description, logs every verdict) and `app/agent_loop.py` (`_needs_confirmation` lets only ALLOW-verdict `run_command` calls skip the human confirm gate — every other CONFIRM_REQUIRED tool, and BLOCK/CONFIRM verdicts on `run_command`, behave exactly as before). Table-driven test suite at `app/tests/test_command_guard.py` (~124 cases, stdlib `unittest`, all green). Went through two review rounds before landing: code-reviewer first caught a critical gap where a destructive verb hidden inside a PowerShell `$(...)` subexpression resolved to ALLOW (fixed by recursively classifying extracted subexpression content and capping any nested-syntax command at CONFIRM), plus `.env` being readable unconfirmed (fixed, then hardened further after re-review found the fix only matched the bare literal token and missed `./.env`-style paths — now matches by basename). See `app/command_guard.py`'s module docstring and comments for the accepted, deliberately out-of-scope limitations (dynamically-built command strings, control-flow-wrapped blocked verbs, `?`/`[...]` wildcards, `python3`/`ipython`) — the threat model is a confused local model writing obviously-destructive commands, not a determined adversary.
- Backlog (build only if asked): `run_python` + `delete_file` action tools · bubble max-width cap (~720px) · headless-browser fetch for bot-walled sites · brave/tavily search keys. Possible follow-up worth a deliberate decision (not yet built): narrow the ALLOW tier so `cat`/`type`/`Get-Content` (which can read arbitrary file content, not just enumerate) require confirmation even for non-protected paths — currently accepted as-is since it matches the original spec and CONFIRM was always the fallback before this feature existed.

## Gotchas — expensive lessons, keep these

- **12 GB VRAM**: one model at a time; much past ~64k context spills into slow *shared* GPU memory (Task Manager → GPU). Per-model LM Studio GUI settings (flash attention / KV-cache quant) can't be set via `lms load` — small spills are accepted, don't chase them.
- **Reasoning channel**: these models stream `reasoning_content` separately from `content` — never merge. `content` sometimes comes back empty after a tool result; the loop retries and synthesizes a fallback so a blank bubble never shows ("Here's what I found:" bullets = the model, not a bug; `/no_think` doesn't help — tested).
- **Vision ⊻ tools**: attaching tools makes LM Studio drop the image, so image turns run tools-off and older images collapse to placeholders — re-paste an image to re-examine it.
- **`fetch_url`** pages via `offset`; `TOOL_MAX_FETCH_CHARS=48000` in `.env` — an 8k cap once caused an infinite offset-0 refetch loop. Some sites block scraping; falling back to search snippets is expected.
- **LM Studio ids mutate**: ids silently gain an `@<quant>` suffix once a second quant of the same base model is downloaded — if a model stops resolving, re-check `lms ls` and fix `models.json`.
- **Windows console is cp1252**: set `PYTHONUTF8=1` for anything printing model output.
- **Config keys** go in `config.py` + `.env` + `.env.example` — all three, every time (past bug).
- **Steering the local 9B**: narrow scope, exact target, pinned output format, one step at a time.
- **Content pages vs app shell**: `app.js` hard-crashes without the chat DOM — it loads only via `index.html`'s `scripts` block, never on `page.html` descendants. `style.css` sets `body { overflow: hidden }` for the app layout; content pages scroll only because `pages.css` overrides it via `body.page`.

## Key facts

- Model ids / aliases / context lengths: **`models.json` is the source of truth** — don't duplicate it here.
- `lms` CLI path: `shutil.which("lms")` → fallback `~/.lmstudio/bin/lms`.
- Notable `.env`: `TEMPERATURE=0.3`, `AGENT_MAX_STEPS=12`, `TOOL_MAX_FETCH_CHARS=48000`, `SEARCH_PROVIDER=duckduckgo`, `USER_NAME=Nemanja`.
