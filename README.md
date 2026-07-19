# ZyleBot

> **Local intelligence. Your machine. Your rules.**

ZyleBot is a local-first, agentic chat app that connects a lightweight FastAPI backend
to an LLM served by [LM Studio](https://lmstudio.ai/). It can reason through multi-step
tasks, inspect files and system information, search and read the web, check weather,
switch local models, and propose actions on your machine—all from a streaming web UI.

**Local inference** · **Persistent conversations** · **Human-approved actions** ·
**No frontend build step**

[Quick start](#quick-start) · [Tool arsenal](#tool-arsenal) ·
[How it works](#how-it-works) · [Configuration](#configuration) ·
[Privacy](#privacy-and-network-access) · [Limitations](#limitations)

---

## Highlights

| Capability | What it gives you |
|---|---|
| **Multi-step agent loop** | The model can reason, call a tool, inspect the result, correct course, and continue until it can answer. |
| **Visible execution** | Reasoning, tool activity, approval requests, and the final answer stream into the conversation as they happen. |
| **Local model control** | LM Studio provides inference; ZyleBot can detect the loaded model, show its context window, and switch configured models. |
| **Human authority** | File changes pause for approval. Shell commands are classified as allowed, approval-required, or blocked before execution. |
| **Persistent memory** | Conversations and messages live in a local SQLite database and survive restarts. |
| **Voice and vision** | Dictate with local `faster-whisper`, or attach an image for a dedicated vision turn. |
| **Cross-platform shell** | PowerShell is used on Windows; macOS and Linux use the configured `$SHELL` with a bash fallback. |

ZyleBot is Windows-first and primarily tested there, but its application and tool paths
support Windows, macOS, and Linux.

## Architecture at a glance

```text
Browser UI
   │  JSON requests + streamed SSE events
   ▼
FastAPI application
   ├── Agent loop ─────────────── LM Studio / local LLM
   ├── Tool registry ──────────── Filesystem, system, web, weather, shell
   ├── Conversation store ─────── SQLite
   └── Speech transcription ───── faster-whisper (local CPU by default)
```

The browser is a vanilla HTML/CSS/JavaScript client. There is no Node.js dependency or
frontend compilation step.

## Quick start

### 1. Prerequisites

- **Windows, macOS, or Linux** (Windows is the primary development platform).
- **Python 3.14**.
- **LM Studio**, with at least one tool-calling-capable chat model downloaded.
- The `lms` CLI on `PATH` if you want to use ZyleBot's in-app server-start and model-switch controls.

The configured default model is `qwythos-9b-claude-mythos-5-1m`, but ZyleBot can work
with another model that reliably emits OpenAI-compatible tool calls.

### 2. Install

**Windows / PowerShell**

```powershell
py -3.14 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

**macOS / Linux**

```bash
python3.14 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

The defaults work as-is for a standard LM Studio server on `localhost:1234`. Edit `.env`
when you want a different model, user name, database location, timeout, or tool limit.

### 3. Prepare LM Studio

Start LM Studio's OpenAI-compatible local server and load a chat model. You can do this
from **Developer → Local Server** in LM Studio or with its CLI:

```powershell
lms server start
lms load <model-id>
```

Recommended starting point for a 12 GB GPU:

- **GPU offload:** maximum / all layers
- **Context length:** `65536` (64k)
- **Flash Attention:** on

Longer contexts may spill into shared GPU memory and become much slower. ZyleBot reads
the loaded context-window size and displays live usage in the header gauge.

### 4. Run ZyleBot

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). FastAPI's interactive API
reference is available at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

If LM Studio is unreachable after the web app opens, the header exposes a **Start
server** control that runs `lms server start`. A model still needs to be available to
answer chat requests.

## Tool arsenal

### Read-only tools

These run automatically so the agent can gather evidence without interrupting the turn.

| Tool | Purpose |
|---|---|
| `list_directory` | List a folder's contents with basic metadata. |
| `read_file` | Read a text file up to the configured byte limit, with best-effort UTF-8 decoding. |
| `search_files` | Find files recursively with a glob pattern. |
| `get_file_info` | Inspect file or folder metadata. |
| `get_system_info` | Report OS, CPU, memory, and per-drive disk usage. |
| `web_search` | Search the web through DuckDuckGo by default. |
| `fetch_url` | Download and extract readable page text in offset-based chunks. |
| `get_weather` | Resolve a place and return current Open-Meteo conditions. |
| `get_game_scores` | Read the Breakout high-score table (play at `/game`). |

### File actions

These always pause the agent loop and show their exact arguments in an **Approve / Deny**
card before anything changes.

| Tool | Purpose |
|---|---|
| `write_file` | Create or overwrite a UTF-8 text file. |
| `append_file` | Append UTF-8 text, creating the file when needed. |
| `make_directory` | Create a directory and any missing parents. |

### Guarded shell commands

`run_command` uses PowerShell on Windows and the native configured shell on macOS/Linux.
Every proposed command is classified before it can run:

| Verdict | Behaviour |
|---|---|
| **Allow** | A narrow set of recognized read-only commands can run automatically. |
| **Confirm** | Anything not proven read-only pauses for explicit approval. |
| **Block** | Clearly dangerous operations—such as destructive deletion, formatting, elevation, encoded execution, and nested shells—are refused without an override. |

The command guard is a fail-closed safety layer, not a security sandbox. An approved
command runs with the same operating-system privileges as the ZyleBot process, so only
approve a command you would run yourself.

## How it works

1. **You send a message.** The browser posts it to the local FastAPI app.
2. **ZyleBot rebuilds the context.** The selected conversation's saved messages are sent
   to the active LM Studio model; LM Studio itself remains stateless.
3. **The model answers or requests a tool.** Tool requests use validated names and
   structured arguments from ZyleBot's registry.
4. **ZyleBot enforces the boundary.** Read-only work can continue, consequential actions
   pause for approval, and blocked shell commands are refused.
5. **The result returns to the model.** It can use the evidence, call another tool,
   change direction, or finish.
6. **The browser receives a live stream.** Reasoning, tool events, approval cards, and
   answer text arrive over server-sent events.
7. **The conversation is saved.** SQLite keeps each conversation independent and restores
   it after a restart.

The loop stops after `AGENT_MAX_STEPS` tool rounds. Repeated identical calls are skipped,
and reaching the ceiling forces a final answer from the evidence already gathered.

## Model control

`models.json` maps LM Studio model IDs to friendly aliases and preferred context lengths.
The file is read fresh when model data is requested, so aliases and context settings can
be adjusted without changing application code.

At startup, ZyleBot checks what LM Studio actually has loaded and aligns its active model
to that runtime state. Switching from the header unloads the current model first so a
single-GPU machine does not accidentally hold two models in VRAM.

## Configuration

Copy `.env.example` to `.env`; the local file is intentionally ignored by Git. The most
useful settings are:

| Variable | Default | Purpose |
|---|---:|---|
| `LMSTUDIO_BASE_URL` | `http://localhost:1234/v1` | LM Studio's OpenAI-compatible API base. |
| `LMSTUDIO_MODEL` | `qwythos-9b-claude-mythos-5-1m` | Fallback model ID before runtime detection. |
| `TEMPERATURE` | `0.3` | Sampling temperature; lower is steadier for tool selection. |
| `AGENT_MAX_STEPS` | `12` | Maximum tool-calling rounds in one turn. |
| `AGENT_REQUEST_TIMEOUT_S` | `120` | Timeout for one LM Studio request. |
| `USER_NAME` | empty | Optional fact injected into every conversation's system prompt. |
| `ZYLEBOT_DB_PATH` | `data/zylebot.db` | SQLite database location. |
| `SEARCH_PROVIDER` | `duckduckgo` | Web-search backend; DuckDuckGo needs no API key. |
| `TOOL_MAX_FETCH_CHARS` | `8000` | Maximum characters returned per fetched-page chunk. |
| `COMMAND_TIMEOUT_S` | `30` | Maximum shell-command runtime. |
| `COMMAND_MAX_OUTPUT_CHARS` | `10000` | Maximum command output returned to the model. |
| `WHISPER_MODEL_SIZE` | `small` | Local speech-recognition model size. |
| `WHISPER_DEVICE` | `cpu` | Keeps transcription from competing with the LLM for VRAM. |

See [`.env.example`](.env.example) for the complete, commented list.

## Privacy and network access

The model inference, agent loop, conversation database, file tools, and default speech
transcription run on your machine. ZyleBot does not require a hosted inference account or
an API key for its default search and weather providers.

Features that inherently need the internet still make outbound requests:

- `web_search` contacts the configured search provider.
- `fetch_url` contacts the page you ask it to read.
- `get_weather` uses Open-Meteo's geocoding and forecast APIs.
- `faster-whisper` downloads its selected model from Hugging Face on first use, then uses
  the local cache.
- LM Studio handles any model downloads you initiate there.

Conversation data stays in the configured SQLite file unless you explicitly use a tool
or command to send it elsewhere.

## Project map

```text
app/
├── main.py             FastAPI lifecycle and JSON/SSE API routes
├── pages.py            HTML page routes
├── agent_loop.py       Multi-step tool loop and approval pause/resume
├── llm_client.py       LM Studio protocol boundary
├── model_manager.py    LM Studio CLI model/server control
├── command_guard.py    Allow / confirm / block command classifier
├── db.py               SQLite persistence and migrations
├── tools/              Registered read-only and action tools
├── templates/          Jinja chat shell and content pages
└── static/             Vanilla JavaScript and CSS
models.json             Model aliases and preferred context lengths
.env.example            Documented configuration template
```

## Limitations

- **Vision and tools are separate.** The current LM Studio stack drops image input when
  tools are attached, so image turns intentionally run without the tool registry.
- **Some sites resist extraction.** Bot-protected or JavaScript-heavy pages can reject
  `fetch_url`; the agent may fall back to search snippets or another source.
- **The shell is not sandboxed.** The guard blocks known-dangerous patterns and approval
  controls authority, but an approved command still runs as your user.
- **Large contexts cost memory.** On a 12 GB GPU, going much beyond roughly 64k tokens can
  spill into shared memory and slow inference sharply.
- **Windows gets the most testing.** macOS and Linux paths are supported but less heavily
  exercised.

## Development checks

Run the current standard-library test suite with:

```powershell
.\venv\Scripts\python.exe -m unittest discover -s app/tests -v
```

HTML and CSS changes need only a browser refresh; static assets are served with
revalidation enabled, so there is no frontend build step.
