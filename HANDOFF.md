# ZyleBot — Handoff (current state)

> Living *current-state* doc — what exists, what's in flight, and the gotchas that save debugging
> time. **The main (orchestrator) model updates this file every time work lands** — subagents
> report, they don't document. Not a changelog; git history is the changelog. Last updated: **2026-07-11**.
>
> Companions: `CLAUDE.md` (always-loaded rules + agent routing), `README.md` (user-facing setup/tools),
> `AGENTS.md` (same ground rules for non-Claude tools, e.g. Cline).

## Where things live

- `app/main.py` — FastAPI app, lifespan, all routes; `/static/*` served no-cache.
- `app/agent_loop.py` — ReAct multi-step loop; pause/resume for tool confirmations; vision mode; empty-content fallbacks.
- `app/llm_client.py` — the ONLY module that knows LM Studio's wire format (streaming, tool-call deltas, context detection).
- `app/model_manager.py` — `models.json` aliases; drives the `lms` CLI (load/unload models, start server).
- `app/db.py` — raw sqlite3, no ORM: `conversations` + `messages`, WAL, guarded in-code migrations.
- `app/config.py` — pydantic-settings `settings` singleton; defaults here, `.env` overrides.
- `app/tools/` — `@tool` registry; SAFE (fs / system / web) vs CONFIRM_REQUIRED (write_file, append_file, make_directory, run_command).
- `app/static/` + `app/templates/` — no-build vanilla JS/CSS/HTML; `app.js` parses SSE manually (fetch reader, since endpoints are POST).
- `app/sse.py` / `models.py` / `platform_info.py` / `stt.py` — SSE framing · request DTOs · shell selection · faster-whisper STT.
- `.claude/agents/` — subagent roster; **per-subsystem invariants live in those files** + the code-reviewer's checklist, not duplicated here.

## What exists (all working, all committed)

Streaming SSE chat with collapsible reasoning blocks · multi-step tool loop with human Approve/Deny
flow (survives page reload) · SQLite per-conversation memory, auto-titles, context gauge · web tools
(DuckDuckGo search, offset-paginated `fetch_url`, weather) · in-chat model switching via `lms` CLI ·
LM Studio health polling + in-app ▶ start-server button · voice input (faster-whisper on CPU, so it
never competes for VRAM) · image input (client-side downscale, persisted per message) · dark neon UI
with website-style footer · cross-platform shell (PowerShell/bash).

Verify e2e: run the app (command in `CLAUDE.md`), send a message; "weather in Belgrade" triggers tools — or dispatch the `verifier` agent.

## Status (2026-07-11)

- Everything above is committed (through `aabd63f`). Uncommitted: this doc trim, `CLAUDE.md` tweak, untracked `AGENTS.md`.
- Backlog (build only if asked): `run_python` + `delete_file` action tools · bubble max-width cap (~720px) · headless-browser fetch for bot-walled sites · brave/tavily search keys.

## Gotchas — expensive lessons, keep these

- **12 GB VRAM**: one model at a time; much past ~64k context spills into slow *shared* GPU memory (Task Manager → GPU). Per-model LM Studio GUI settings (flash attention / KV-cache quant) can't be set via `lms load` — small spills are accepted, don't chase them.
- **Reasoning channel**: these models stream `reasoning_content` separately from `content` — never merge. `content` sometimes comes back empty after a tool result; the loop retries and synthesizes a fallback so a blank bubble never shows ("Here's what I found:" bullets = the model, not a bug; `/no_think` doesn't help — tested).
- **Vision ⊻ tools**: attaching tools makes LM Studio drop the image, so image turns run tools-off and older images collapse to placeholders — re-paste an image to re-examine it.
- **`fetch_url`** pages via `offset`; `TOOL_MAX_FETCH_CHARS=48000` in `.env` — an 8k cap once caused an infinite offset-0 refetch loop. Some sites block scraping; falling back to search snippets is expected.
- **LM Studio ids mutate**: ids silently gain an `@<quant>` suffix once a second quant of the same base model is downloaded — if a model stops resolving, re-check `lms ls` and fix `models.json`.
- **Windows console is cp1252**: set `PYTHONUTF8=1` for anything printing model output.
- **Config keys** go in `config.py` + `.env` + `.env.example` — all three, every time (past bug).
- **Steering the local 9B**: narrow scope, exact target, pinned output format, one step at a time.

## Key facts

- Model ids / aliases / context lengths: **`models.json` is the source of truth** — don't duplicate it here.
- `lms` CLI path: `shutil.which("lms")` → fallback `~/.lmstudio/bin/lms`.
- Notable `.env`: `TEMPERATURE=0.3`, `AGENT_MAX_STEPS=12`, `TOOL_MAX_FETCH_CHARS=48000`, `SEARCH_PROVIDER=duckduckgo`, `USER_NAME=Nemanja`.
