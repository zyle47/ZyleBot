# ZyleBot — Project Handoff / State

> **What this file is:** the living "what's done & what's planned" doc for ZyleBot, written
> so a fresh agent (or a future you) can pick up without re-discovering everything. Update it
> as you finish or start work. Last updated: **2026-07-09**.
>
> Companion docs: `README.md` (user-facing setup/run/tools) and the original design plan at
> `~/.claude/plans/you-are-senior-developer-parallel-conway.md` (phased build + rationale).

---

## TL;DR — where we are

**ZyleBot is a working local agentic chat app** (FastAPI + a locally-hosted LLM via LM Studio).
v1 (Phases A–F) is **complete and verified**, plus several enhancements shipped on top:
in-chat model switching, temperature/prompt tuning, action tools with an approval flow, and a
website-style footer in the UI. The app runs, streams, uses tools, persists chats, and switches
models. Current activity is **UI polish** (the footer). Everything runs on the user's machine;
no cloud, no API keys required by default.

- **User:** Nemanja (GitHub `zyle47`). Windows 11, RTX 3080 Ti (12 GB VRAM), Python 3.14.
- **Repo:** private GitHub repo. Branch `master`. `.env`, `data/*.db`, `venv/`, `.claude/` are gitignored.
- **LLM backend:** LM Studio, OpenAI-compatible API at `http://localhost:1234/v1`.

---

## Quick start (for an agent)

```powershell
# 1. LM Studio must be running with a model loaded (Developer tab → Start Server, load a model).
#    ZyleBot does NOT start LM Studio. Check: GET http://localhost:1234/v1/models
# 2. Run the app (from F:\local_mythos):
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
# 3. Open http://127.0.0.1:8000  (raw API docs at /docs)
```

- **Frontend is no-build** (plain HTML/CSS/JS served by FastAPI). CSS/HTML edits → just refresh the browser. `--reload` restarts on Python changes.
- **To verify a change end-to-end:** open the app, send a message, watch the transcript. For tools, ask something that triggers them (e.g. "what's the weather in Belgrade" → `get_weather`; "list files in F:\local_mythos" → `list_directory`).
- **Windows console gotcha:** standalone test scripts that print model output can hit a cp1252 `UnicodeEncodeError` on non-ASCII (degree signs, emoji). Set `PYTHONUTF8=1` when running them.

---

## Architecture map

```
F:\local_mythos\
├── app\
│   ├── main.py            FastAPI app, lifespan, all routes. Wraps agent loop in StreamingResponse.
│   ├── config.py          pydantic-settings. Defaults in code, overridable via .env. `settings` singleton.
│   ├── llm_client.py      ONLY place that knows LM Studio's wire format. Streaming, tool-call delta
│   │                      accumulation, active-model state, context-length detection.
│   ├── agent_loop.py      ReAct multi-step loop. run_agent_turn / resume_after_confirmation /
│   │                      build_system_prompt. Emits SSE events. No HTTP knowledge (testable).
│   ├── db.py              SQLite. Per-conversation isolation. conversations + messages tables.
│   ├── sse.py             SSEEvent dataclass + framing.
│   ├── models.py          Pydantic request bodies (ChatRequest, ConfirmRequest, ModelRequest).
│   ├── model_manager.py   Reads models.json (aliases + per-model context). Drives `lms` CLI to
│   │                      load/unload models.
│   ├── platform_info.py   OS_NAME, SHELL_NAME, shell_argv(command) — cross-platform shell selection.
│   ├── stt.py             Local speech-to-text via faster-whisper. Lazy-loaded singleton model;
│   │                      transcribe(audio_bytes) called via asyncio.to_thread from main.py.
│   ├── tools\
│   │   ├── base.py         @tool decorator, RiskTier (SAFE / CONFIRM_REQUIRED), _REGISTRY.
│   │   ├── __init__.py     Side-effect imports register all tool modules; exposes
│   │   │                   get_openai_tool_schemas / execute_tool / get_tool_risk_tier.
│   │   ├── fs_tools.py      list_directory, read_file, search_files, get_file_info   (SAFE)
│   │   ├── system_tools.py  get_system_info (psutil)                                 (SAFE)
│   │   ├── web_tools.py      web_search (ddgs), fetch_url (offset-paginated), get_weather (SAFE)
│   │   └── action_tools.py   write_file, append_file, make_directory, run_command  (CONFIRM_REQUIRED)
│   ├── static\  style.css, app.js, footer_demo.html (standalone footer playground)
│   └── templates\ index.html
├── models.json            model id → {alias, context_length}
├── data\zylebot.db        SQLite (gitignored)
├── .env / .env.example    config (.env gitignored; keep them in sync — see Conventions)
├── requirements.txt   README.md   HANDOFF.md (this file)   CLAUDE.md
```

**Key design invariants (don't break these):**
- `agent_loop.py` only ever calls `get_openai_tool_schemas` / `execute_tool` — never touches tool internals. Adding a tool = write one function + `@tool` decorator; schema and dispatch stay in sync automatically.
- All DB access lives in `agent_loop.py` and `main.py` route handlers — never inside `tools/` or `llm_client.py`.
- `llm_client.py` is the only module that knows the LM Studio wire format.
- Tools never raise for *expected* conditions (not-found, too-large, permission-denied) — they return `{"error": ...}`. `execute_tool` normalizes unexpected exceptions too, so a bad tool call never crashes the loop.
- Config: default value lives in `config.py`; `.env` overrides. When you add a config key, mirror it into **both** `.env.example` **and** the live `.env`.

---

## Done ✅

- **Phase A — streaming passthrough.** Token-by-token SSE streaming end-to-end.
- **Phase B — tool registry + native tool-calling.** Verified the model reliably emits OpenAI `tool_calls` (no text-parsing fallback needed).
- **Phase C — multi-step ReAct loop + live tool events + context gauge.** Header shows `used / max` tokens, colors amber ≥70% / red ≥90%. This model streams chain-of-thought in a separate `reasoning_content` field → surfaced as a dimmed collapsible `reasoning_token` block.
- **Phase D — SQLite persistence + per-conversation memory.** Each chat is an isolated brain; survives restarts. Left sidebar with conversation list, new-chat, delete. Only globally-shared fact is `USER_NAME`. Gauge seeds from stored `last_total_tokens` on load.
- **Phase E — polish.** LM-Studio-unreachable handling, tool-error display, auto-titled conversations, README + `.env.example`.
- **Phase F — web access.** `web_search` (DuckDuckGo, keyless), `fetch_url` (trafilatura/bs4 main-text extraction, **offset-based pagination** so long pages don't blow context), `get_weather` (Open-Meteo, keyless). System prompt injects today's date + web/efficiency guidance.
- **Action tools + confirmation flow.** `write_file`, `append_file`, `make_directory`, `run_command` — all `CONFIRM_REQUIRED`. Turn pauses on such a call (`messages.status='pending_confirmation'`, emits `confirmation_required`), resumes via `POST /api/conversations/{id}/confirm`. A reloaded page re-renders the pending approve/deny card.
- **Reliability guards.** Forced tool-less final answer at `AGENT_MAX_STEPS`; dedup guard skips identical repeat `(name, args)` calls; `temperature=0.3` for steadier tool selection.
- **Cross-platform shell.** `platform_info.shell_argv` → PowerShell on Windows, `$SHELL`/bash on POSIX.
- **In-chat model switching (via `lms` CLI).** `model_manager.load_model` runs `lms unload --all` then `lms load <id> --gpu max -c <ctx> -y`. Startup syncs the active model to whatever's actually loaded (no divergence). Dropdown shows aliases with a loading state. `models.json` holds aliases + per-model context. **Verified:** switching loads exactly one model at the right context.
- **Cline (VSCode) connected to the same local model** — independent client of LM Studio :1234, used for a footer integration test-drive. (ZyleBot and Cline are both just clients of LM Studio.)
- **Footer UI.** Website-style footer (brand + Product/Resources/About columns + bottom bar with icon links). Reviewed and cleaned up: column links themed (green hover), `svg` selector scoped under `footer`, GitHub icon path fixed. `#main` widened (`max-width` raised to 1300px) so the chat fills more of the screen.
- **Speech-to-text (voice input).** 🎤 button next to the composer. Click to start/stop recording (browser `MediaRecorder`, mic permission prompt on first use); on stop, the clip is POSTed to `POST /api/transcribe` and transcribed locally with `faster-whisper` (CPU, `int8`, `small` model by default — configurable via `WHISPER_MODEL_SIZE` / `WHISPER_DEVICE` / `WHISPER_COMPUTE_TYPE`). Transcribed text is appended into the message input (not auto-sent), so it can be edited before sending. Runs on CPU by design, to avoid competing with LM Studio for the shared 12GB VRAM. Model weights download from Hugging Face on first use (~500MB for `small`), then cache offline in `~/.cache/huggingface`. **Verified:** real speech ("Testing the local speech-to-text feature for ZyleBot") transcribed correctly (~3s on a warm model, CPU); phonetically mishears the invented word "ZyleBot" as "Zillabot" — expected, not a bug.

---

## In progress / uncommitted ⚠️

`git status` currently shows **uncommitted** footer + width work, plus the new speech-to-text feature:
- `app/static/footer_demo.html` (new, staged) — standalone footer playground.
- `app/static/style.css` (modified) — footer CSS, link/heading theming, scoped `footer svg`, `#main` max-width, mic button + recording-pulse styles.
- `app/templates/index.html` (modified) — `<footer>` markup inside `<main>`, fixed GitHub icon path, 🎤 mic button in the composer.
- `app/stt.py` (new) — local Whisper transcription module.
- `app/main.py`, `app/config.py`, `app/static/app.js` (modified) — `POST /api/transcribe` endpoint, `WHISPER_*` settings, mic recording UI logic.
- `requirements.txt`, `.env`, `.env.example` (modified) — `faster-whisper` + transitive deps (`requests`, `python-multipart`, etc.), `WHISPER_MODEL_SIZE`/`WHISPER_DEVICE`/`WHISPER_COMPUTE_TYPE`.

**Next commit(s)** should bundle these (e.g. `UI: website-style footer + full-width chat area` and a separate `Add local speech-to-text voice input`). Ask the user before committing — they commit deliberately.

---

## Planned / backlog 📋

Nothing is blocking. Optional items previously discussed (only build if the user asks):

- **`run_python` escape hatch** — a `CONFIRM_REQUIRED` tool to execute Python (sibling to `run_command`).
- **`delete_file` / delete action tool** — `CONFIRM_REQUIRED`.
- **Bubble max-width cap** — now that `#main` is wide, short assistant lines can stretch far on wide monitors. Offered a `.bubble { max-width: ~720px }` cap; user hasn't asked for it yet.
- **Headless-browser fetch** — for JS-heavy / bot-protected sites (`fetch_url` currently fails on ESPN, Wikipedia, Reuters, etc.). Big dependency; deferred.
- **Key-based search providers** — `brave` / `tavily` are stubbed behind `SEARCH_PROVIDER`; keys reserved in config but unused.

---

## Known quirks & gotchas 🧭

- **VRAM budget (12 GB):** one model at a time. ~64k context fits fully; higher spills into slow *shared* GPU memory (watch Task Manager → GPU → Shared GPU memory). The current 9B Q4 models are ~6.7 GB.
- **"Mythos True" spills even at 64k** while "Mythos False" fits at 73k despite near-identical models — this is a **per-model load setting** (Flash Attention / KV-cache quantization) that must be set in the **LM Studio GUI** and is persisted per model. `lms load` / the in-app switcher **cannot** set it. User has accepted the small spill ("even with spill, it's perfect") — don't chase this.
- **GPU offload "max" shows 33 layers** now (was 32 earlier) — LM Studio's layer count; not a bug. Use "max".
- **`reasoning_content` is separate from `content`** for this model — the loop handles both; don't merge them.
- **This reasoning model sometimes emits an empty `content` after a tool result** (it keeps everything in its `<think>`/reasoning channel and never produces prose — worst on **Mythos True**, the abliterated model). The old loop treated a tool-less step as the final answer and showed a **blank bubble = looked "stuck"**. Fixed 2026-07-09: `_react_loop` now guards the empty case (`force_reason="empty"` → forced tool-less retry with a no-`<think>`/`<function>` nudge), and `_fallback_from_tool_results` synthesizes a readable reply from the last tool result so a blank bubble is never shown. `/no_think` does **not** help this model (tested). Net effect: weather/tool answers may come back as a bulleted "Here's what I found:" fallback rather than prose — that's the model, not the loop.
- **`fetch_url` pagination:** long pages return `next_offset`/`note`; the model pages via `offset`. `TOOL_MAX_FETCH_CHARS` caps per-chunk size (user set it to **48000** in `.env`; code default is 8000). Don't lower it blindly — an 8k cap once made the model re-fetch offset 0 forever on a 48k page.
- **Some sites block scraping** (JS-heavy / bot-protected). Expected; model falls back to search snippets.
- **`.env` sync:** past bug — new config keys were added to `.env.example` but not the live `.env`. Always update both.

---

## Conventions & constraints

- **Config pattern:** default in `config.py`, override in `.env`. Keep `.env` and `.env.example` in sync.
- **Git:** commit only when the user asks; they commit deliberately. End commit messages with the `Co-Authored-By: Claude` trailer. Repo is **private**.
- **Security constraints (must persist):**
  - **Do NOT help create explosives / weapons / other clearly-harmful things.** The system prompt has a "don't refuse on copyright/training-cutoff grounds, retrieve via tools instead" nudge — that nudge does **not** override genuine harm refusals. This was tested; the refusal is correct behavior.
  - `run_command` gives full shell access but is `CONFIRM_REQUIRED` — the human approves every invocation. That approval gate is the safety; keep it.
- **Weak local model steering (learned):** the 9B follows precise, narrow instructions best — constrain scope, name the exact target, pin the output format, one step at a time. For Cline, tell it to "read the file at <path>" rather than `@mention` (its `@` resolver flakes).

---

## Key facts

| Model id | Alias | Context (`models.json`) |
|---|---|---|
| `qwythos-9b-claude-mythos-5-1m@q4_k_m` | **Mythos False** (default) | 73728 |
| `huihui-qwythos-9b-claude-mythos-5-1m-abliterated` | **Mythos True** | 73728 |
| `qwythos-9b-claude-mythos-5-1m@q8_0` | **Mythos Q8** | 11381 |

> **LM Studio id disambiguation (learned 2026-07-09):** when only ONE quant of a base
> model is downloaded, LM Studio's API reports the **bare** id (`qwythos-9b-claude-mythos-5-1m`).
> Download a **second quant** and it drops the bare id and reports **both** with an `@<quant>`
> suffix (`…@q4_k_m`, `…@q8_0`). So adding the Q8 silently changed the Q4's id → the "Mythos
> False" key had to move from the bare id to `…@q4_k_m`. If a model that used to resolve
> suddenly shows a raw id / won't load, re-check its id against `lms ls` or `/v1/models`.
> The Q8 maxes the 12 GB card (11.99 GB est.), leaving only ~11k context.

- `lms` CLI path: `shutil.which("lms")` → fallback `~/.lmstudio/bin/lms`.
- Notable `.env` values in use: `TEMPERATURE=0.3`, `AGENT_MAX_STEPS=12`, `USER_NAME=Nemanja`, `SEARCH_PROVIDER=duckduckgo`, `TOOL_MAX_FETCH_CHARS=48000`.
