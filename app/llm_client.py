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

# --- Provider state (runtime truth; .env is persistence only) -------------
_provider: str = "lmstudio"  # "lmstudio" | "openrouter"
_openrouter_key: str = ""  # kept across disconnect for one-click reconnect
_openrouter_model: str = ""  # "" = none selected
# Cached trimmed list from OpenRouter's /models: [{id, name, context_length}].
_openrouter_models: list[dict[str, Any]] | None = None


def get_provider() -> str:
    return _provider


def get_active_model() -> str:
    if _provider == "openrouter":
        return _openrouter_model
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


# --- OpenRouter provider --------------------------------------------------

def _openrouter_headers(key: str) -> dict[str, str]:
    # HTTP-Referer / X-Title are OpenRouter's optional attribution headers.
    return {
        "Authorization": f"Bearer {key}",
        "HTTP-Referer": "http://localhost:8000",
        "X-Title": "ZyleBot",
    }


def get_saved_openrouter_key() -> str:
    """Best available key: runtime copy first, then the one persisted in .env."""
    return _openrouter_key or settings.openrouter_api_key


def _is_free_model(m: dict[str, Any]) -> bool:
    """True when every advertised price is zero (OpenRouter's ':free' variants).
    Pricing values arrive as strings like "0"; a missing pricing block falls
    back to the id suffix convention."""
    pricing = m.get("pricing") or {}
    if not pricing:
        return str(m.get("id", "")).endswith(":free")
    try:
        return all(float(v) == 0.0 for v in pricing.values() if v is not None)
    except (TypeError, ValueError):
        return False


def _is_chat_model(m: dict[str, Any]) -> bool:
    """Keep text-in/text-out models only — OpenRouter's catalog also lists
    music/media generators (e.g. Google Lyria, output text+audio) that would be
    useless in a chat picker."""
    outputs = (m.get("architecture") or {}).get("output_modalities")
    if not outputs:
        return True  # benefit of the doubt on missing metadata
    return set(outputs) == {"text"}


def list_openrouter_models() -> list[dict[str, Any]]:
    return _openrouter_models or []


def set_openrouter_model(model_id: str) -> None:
    global _openrouter_model
    _openrouter_model = model_id


async def activate_openrouter(key: str = "") -> dict[str, Any]:
    """Validate the key against OpenRouter, cache its model list, and swap the
    shared client over to the OpenRouter base. Raises ValueError on a rejected
    key, RuntimeError on network/API trouble — in both cases the current
    client (LM Studio or otherwise) is left untouched.
    """
    global _client, _provider, _openrouter_key, _openrouter_model, _openrouter_models

    key = key or get_saved_openrouter_key()
    if not key:
        raise ValueError("no API key provided or saved")

    # Short timeout for the two setup calls — chat streaming gets the long one.
    candidate = httpx.AsyncClient(
        base_url=settings.openrouter_base_url,
        headers=_openrouter_headers(key),
        timeout=20.0,
    )
    try:
        # GET /key is authenticated (GET /models is public and accepts any key,
        # so it cannot validate).
        response = await candidate.get("/key")
        if response.status_code in (401, 403):
            raise ValueError("OpenRouter rejected the API key")
        response.raise_for_status()

        response = await candidate.get("/models")
        response.raise_for_status()
        raw = response.json().get("data", [])
        raw = [m for m in raw if _is_chat_model(m)]
        if settings.openrouter_free_only:
            raw = [m for m in raw if _is_free_model(m)]
    except httpx.HTTPError as exc:
        await candidate.aclose()
        raise RuntimeError(f"could not reach OpenRouter: {exc}") from exc
    except ValueError:
        await candidate.aclose()
        raise

    models = sorted(
        (
            {
                "id": m.get("id"),
                "name": m.get("name") or m.get("id"),
                "context_length": m.get("context_length"),
            }
            for m in raw
            if m.get("id")
        ),
        key=lambda m: m["id"],
    )

    # Swap the shared client: chat streaming reuses the long agent timeout.
    old = _client
    _client = httpx.AsyncClient(
        base_url=settings.openrouter_base_url,
        headers=_openrouter_headers(key),
        timeout=settings.agent_request_timeout_s,
    )
    await candidate.aclose()
    if old is not None:
        await old.aclose()

    _provider = "openrouter"
    _openrouter_key = key
    _openrouter_models = models
    # Keep the currently-selected model if still listed; else fall back to the
    # one persisted in .env; else none selected.
    ids = {m["id"] for m in models}
    if _openrouter_model not in ids:
        _openrouter_model = settings.openrouter_model if settings.openrouter_model in ids else ""
    return {"model": _openrouter_model or None, "models_count": len(models)}


async def deactivate_openrouter() -> None:
    """Swap back to the LM Studio client. The key and model selection are kept
    in memory (and .env) so reconnecting is one click."""
    global _provider, _context_length_cache
    old = _client
    _provider = "lmstudio"
    _context_length_cache = None
    init_client()  # rebuilds _client on the LM Studio base
    if old is not None:
        await old.aclose()


def _native_api_base() -> str:
    """LM Studio's native REST API lives at the origin, not under /v1."""
    base = settings.lmstudio_base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base.rstrip("/")


async def _fetch_native_models() -> list[dict[str, Any]]:
    """Raw list from LM Studio's native /api/v0/models (id, state, type, context)."""
    # Single enforcement point keeping openrouter mode off the LM Studio native
    # API: the request below uses an absolute URL to the LM Studio origin, so
    # it would bypass the client's OpenRouter base_url without this guard.
    if _provider != "lmstudio":
        return []
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
    if _provider == "openrouter":
        # Context window comes from the /models payload cached at connect time.
        chosen = next(
            (m for m in list_openrouter_models() if m["id"] == _openrouter_model), None
        )
        return chosen.get("context_length") if chosen else None

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
    """Stream chat completion chunks from the active backend's OpenAI-compatible
    API (LM Studio or OpenRouter — both speak the same wire format; OpenRouter's
    keep-alive comment lines are skipped by the `data:` filter below).

    Yields raw parsed chunk dicts in OpenAI's streaming delta format.
    This is the only place in the codebase that knows either backend's wire format.
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
        if response.status_code >= 400:
            # With client.stream() the body isn't read at raise time, so
            # raise_for_status() would only say "Client error '404 …'". Read it
            # and surface the API's actual message in the error bubble.
            body = await response.aread()
            message = body.decode("utf-8", errors="replace")
            try:
                error = json.loads(message).get("error")
                if isinstance(error, dict):
                    message = error.get("message") or message
                elif isinstance(error, str):
                    message = error
            except json.JSONDecodeError:
                pass
            raise RuntimeError(f"LLM API error {response.status_code}: {message}")
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
