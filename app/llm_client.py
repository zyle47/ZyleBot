import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger("zylebot.llm_client")

_client: httpx.AsyncClient | None = None

# The active model can be switched at runtime (via the UI). Starts at the
# configured default; resets to it on server restart.
_active_model: str | None = None


def get_active_model() -> str:
    return _active_model or settings.lmstudio_model


def set_active_model(name: str) -> None:
    global _active_model, _context_length_cache
    _active_model = name
    _context_length_cache = None  # force a re-fetch for the new model's window


def init_client() -> None:
    global _client
    _client = httpx.AsyncClient(
        base_url=settings.lmstudio_base_url,
        timeout=settings.agent_request_timeout_s,
    )


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def check_connectivity() -> bool:
    if _client is None:
        return False
    try:
        response = await _client.get("/models")
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def _native_api_base() -> str:
    """LM Studio's native REST API lives at the origin, not under /v1."""
    base = settings.lmstudio_base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base.rstrip("/")


async def _fetch_native_models() -> list[dict[str, Any]]:
    """Raw list from LM Studio's native /api/v0/models (id, state, type, context)."""
    if _client is None:
        return []
    try:
        response = await _client.get(f"{_native_api_base()}/api/v0/models")
        response.raise_for_status()
        return response.json().get("data", [])
    except (httpx.HTTPError, ValueError):
        return []


async def list_chat_models() -> list[dict[str, Any]]:
    """Chat-capable models available in LM Studio (embeddings excluded), each with
    id, state ('loaded'/'not-loaded'), and max/loaded context length."""
    models = await _fetch_native_models()
    return [
        {
            "id": m.get("id"),
            "state": m.get("state"),
            "loaded_context_length": m.get("loaded_context_length"),
            "max_context_length": m.get("max_context_length"),
        }
        for m in models
        if m.get("type") != "embeddings"
    ]


async def detect_loaded_model() -> str | None:
    """Return the id of the currently-loaded chat model, if any. Used at startup
    to align the active model with LM Studio's real state (no divergence)."""
    for m in await _fetch_native_models():
        if m.get("state") == "loaded" and m.get("type") != "embeddings":
            return m.get("id")
    return None


_context_length_cache: int | None = None


async def get_loaded_context_length(force_refresh: bool = False) -> int | None:
    """Return the active model's loaded context window size (e.g. 16384).

    Uses LM Studio's native /api/v0/models endpoint. Returns None if the active
    model isn't loaded yet or the info is unavailable (caller degrades gracefully).
    """
    global _context_length_cache
    if _context_length_cache is not None and not force_refresh:
        return _context_length_cache

    models = await _fetch_native_models()
    if not models:
        return None

    # Prefer the active model; otherwise fall back to whichever is loaded.
    active = get_active_model()
    chosen = next((m for m in models if m.get("id") == active), None)
    if chosen is None:
        chosen = next((m for m in models if m.get("state") == "loaded"), None)
    if chosen is None:
        return None
    _context_length_cache = chosen.get("loaded_context_length")
    return _context_length_cache


async def stream_chat_completion(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | None = "auto",
) -> AsyncIterator[dict[str, Any]]:
    """Stream chat completion chunks from LM Studio's OpenAI-compatible API.

    Yields raw parsed chunk dicts in OpenAI's streaming delta format.
    This is the only place in the codebase that knows LM Studio's wire format.
    """
    if _client is None:
        raise RuntimeError("llm_client not initialized — call init_client() first")

    payload: dict[str, Any] = {
        "model": get_active_model(),
        "messages": messages,
        "stream": True,
        "temperature": settings.temperature,
        # Ask LM Studio to append a final chunk carrying token usage totals.
        "stream_options": {"include_usage": True},
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice

    async with _client.stream("POST", "/chat/completions", json=payload) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed SSE chunk from LM Studio: %r", data)
                continue


def accumulate_tool_call_deltas(
    accumulator: dict[int, dict[str, Any]], delta_tool_calls: list[dict[str, Any]]
) -> None:
    """Fold a chunk's tool_calls delta fragments into `accumulator`, keyed by index.

    OpenAI-format streaming sends tool call arguments as string deltas that
    must be concatenated per index/id across chunks.
    """
    for fragment in delta_tool_calls:
        index = fragment["index"]
        entry = accumulator.setdefault(index, {"id": None, "name": None, "arguments": ""})
        if fragment.get("id"):
            entry["id"] = fragment["id"]
        function = fragment.get("function") or {}
        if function.get("name"):
            entry["name"] = function["name"]
        if function.get("arguments"):
            entry["arguments"] += function["arguments"]


def finalize_tool_calls(accumulator: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert the accumulator dict into an ordered list of complete tool calls."""
    calls = []
    for index in sorted(accumulator):
        entry = accumulator[index]
        try:
            arguments = json.loads(entry["arguments"]) if entry["arguments"] else {}
        except json.JSONDecodeError:
            arguments = {}
        calls.append({"id": entry["id"], "name": entry["name"], "arguments": arguments})
    return calls
