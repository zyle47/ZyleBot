import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.requests import Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import agent_loop, db, llm_client, model_manager, pages, stt
from app.config import persist_env_values, settings
from app.models import (
    ChatRequest,
    ConfirmRequest,
    ModelRequest,
    ProviderConnectRequest,
    ScoreSubmit,
)
from app.sse import SSEEvent
from app.rl_policy import PolicyUnavailableError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("zylebot.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    llm_client.init_client()
    # Auto-resume openrouter mode if it was active last run. On any failure
    # (bad key, offline) fall through to LM Studio WITHOUT rewriting .env — a
    # transient failure must not silently flip the persisted mode.
    if settings.llm_provider == "openrouter" and settings.openrouter_api_key:
        try:
            result = await llm_client.activate_openrouter()
            logger.info(
                "Resumed OpenRouter mode (%s models, active: %s)",
                result["models_count"],
                result["model"] or "none selected",
            )
        except (ValueError, RuntimeError) as exc:
            logger.warning("Could not resume OpenRouter mode (%s) — falling back to LM Studio", exc)
    if llm_client.get_provider() == "lmstudio":
        if await llm_client.check_connectivity():
            logger.info("LM Studio reachable at %s", settings.lmstudio_base_url)
            # Align the active model with whatever LM Studio actually has loaded, so
            # the server never diverges from reality (or resets to a stale default).
            loaded = await llm_client.detect_loaded_model()
            if loaded:
                llm_client.set_active_model(loaded)
                logger.info("Active model synced to loaded: %s", loaded)
        else:
            logger.warning(
                "LM Studio NOT reachable at %s — start it with `lms server start` "
                "and load a model with `lms load %s`",
                settings.lmstudio_base_url,
                settings.lmstudio_model,
            )
    yield
    await llm_client.close_client()


app = FastAPI(title="ZyleBot", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(pages.router)


@app.middleware("http")
async def revalidate_static(request: Request, call_next):
    """Browsers heuristically cache /static/* (no Cache-Control from StaticFiles),
    so JS/CSS edits kept needing a hard refresh. `no-cache` forces revalidation;
    unchanged files still come back as cheap 304s via the ETag."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/api/health")
async def health():
    has_saved_key = bool(llm_client.get_saved_openrouter_key())
    if llm_client.get_provider() == "openrouter":
        # No OpenRouter ping here — this is polled every 10s and the key was
        # validated at connect; a revoked key surfaces on the next chat.
        model = llm_client.get_active_model()
        label = next(
            (m["name"] for m in llm_client.list_openrouter_models() if m["id"] == model),
            model,
        )
        return {
            "provider": "openrouter",
            "connected": True,
            "model": model,
            "model_label": label or None,
            "context_length": await llm_client.get_loaded_context_length(),
            "has_saved_key": has_saved_key,
        }

    reachable = await llm_client.check_connectivity()
    # Force-refresh so changing the context length (or model) in LM Studio is
    # reflected after a browser refresh, without restarting ZyleBot.
    context_length = (
        await llm_client.get_loaded_context_length(force_refresh=True) if reachable else None
    )
    active = llm_client.get_active_model()
    # Report what LM Studio *actually* has loaded — the server can be up with no
    # model in VRAM, and the active (default) model must not be mistaken for it.
    loaded = await llm_client.detect_loaded_model() if reachable else None
    return {
        "provider": "lmstudio",
        "lmstudio_reachable": reachable,
        "model": active,
        "model_alias": model_manager.get_alias(active),
        "loaded_model": loaded,
        "loaded_model_alias": model_manager.get_alias(loaded) if loaded else None,
        "context_length": context_length,
        "has_saved_key": has_saved_key,
    }


def _require_lmstudio_mode() -> None:
    """Defense in depth: the lms-CLI endpoints are hidden in openrouter mode,
    but must also refuse if called directly."""
    if llm_client.get_provider() != "lmstudio":
        raise HTTPException(status_code=409, detail="not in LM Studio mode — disconnect first")


@app.post("/api/server/start")
async def start_server():
    """Start LM Studio's local server via the lms CLI. The button click *is* the
    human approval, so no extra confirmation gate (same as model switching)."""
    _require_lmstudio_mode()
    result = await asyncio.to_thread(model_manager.start_server)
    # Trust reachability over CLI output: the server may need a moment to accept
    # connections, and the CLI can misreport while bootstrapping LM Studio itself.
    reachable = False
    for _ in range(15):
        if await llm_client.check_connectivity():
            reachable = True
            break
        await asyncio.sleep(1)
    if not reachable:
        raise HTTPException(
            status_code=502,
            detail=result.get("error") or "server did not become reachable",
        )
    # Same re-sync as startup: align the active model with what's actually loaded.
    loaded = await llm_client.detect_loaded_model()
    if loaded:
        llm_client.set_active_model(loaded)
        logger.info("LM Studio server started; active model synced to %s", loaded)
    else:
        logger.info("LM Studio server started; no model loaded yet")
    return {"ok": True, "model_loaded": loaded}


@app.post("/api/server/stop")
async def stop_server():
    """Stop LM Studio's local server via the lms CLI. The button click is the
    human approval, same as start."""
    _require_lmstudio_mode()
    result = await asyncio.to_thread(model_manager.stop_server)
    # Mirror start: trust real (un)reachability over CLI output.
    for _ in range(5):
        if not await llm_client.check_connectivity():
            return {"ok": True}
        await asyncio.sleep(1)
    raise HTTPException(
        status_code=502,
        detail=result.get("error") or "server is still reachable after stop",
    )


@app.post("/api/model/unload")
async def unload_model():
    """Unload whatever LM Studio has loaded, freeing VRAM. The button click is
    the human approval (same as model switching)."""
    _require_lmstudio_mode()
    result = await asyncio.to_thread(model_manager.unload_all)
    if not result["ok"]:
        raise HTTPException(status_code=500, detail=result["error"])
    return {"ok": True}


@app.get("/api/models")
async def list_models():
    if llm_client.get_provider() == "openrouter":
        # Served from the cache populated at connect — no live OpenRouter call.
        return {
            "provider": "openrouter",
            "active": llm_client.get_active_model(),
            "models": llm_client.list_openrouter_models(),
            "free_only": settings.openrouter_free_only,
        }
    models = await llm_client.list_chat_models()
    for m in models:
        m["alias"] = model_manager.get_alias(m["id"])
    return {"provider": "lmstudio", "active": llm_client.get_active_model(), "models": models}


@app.post("/api/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="empty audio")
    suffix = Path(audio.filename or "").suffix or ".webm"
    try:
        text = await asyncio.to_thread(stt.transcribe, audio_bytes, suffix)
    except Exception as exc:  # noqa: BLE001 - surface as a clean 500, don't crash the app
        logger.exception("Transcription failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"text": text}


@app.post("/api/model")
async def set_model(req: ModelRequest):
    if llm_client.get_provider() == "openrouter":
        # Cloud model switching is just picking an id — no lms CLI, no VRAM.
        available = {m["id"] for m in llm_client.list_openrouter_models()}
        if req.model not in available:
            raise HTTPException(status_code=400, detail=f"unknown model: {req.model}")
        llm_client.set_openrouter_model(req.model)
        persist_env_values({"OPENROUTER_MODEL": req.model})
        return {
            "active": req.model,
            "context_length": await llm_client.get_loaded_context_length(),
        }

    available = {m["id"] for m in await llm_client.list_chat_models()}
    if req.model not in available:
        raise HTTPException(status_code=400, detail=f"unknown model: {req.model}")

    # Load it via the lms CLI: unload others (one model at a time on the GPU) and
    # load the target at its configured context. Blocking, so run in a thread.
    context = model_manager.get_context_length(req.model)
    result = await asyncio.to_thread(model_manager.load_model, req.model, context)
    if not result["ok"]:
        raise HTTPException(status_code=500, detail=result["error"])

    llm_client.set_active_model(req.model)
    return {
        "active": llm_client.get_active_model(),
        "context_length": await llm_client.get_loaded_context_length(force_refresh=True),
    }


# --- Provider (LM Studio ↔ OpenRouter) ------------------------------------

@app.post("/api/provider/connect")
async def provider_connect(req: ProviderConnectRequest):
    """Switch to OpenRouter mode. Empty api_key = reuse the saved key."""
    new_key = req.api_key.strip()
    try:
        result = await llm_client.activate_openrouter(new_key)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    updates = {"LLM_PROVIDER": "openrouter"}
    if new_key:  # only persist when a new key was actually entered
        updates["OPENROUTER_API_KEY"] = new_key
    persist_env_values(updates)
    logger.info("Connected to OpenRouter (%s models available)", result["models_count"])
    return {
        "ok": True,
        "provider": "openrouter",
        "model": result["model"],
        "models_count": result["models_count"],
    }


@app.post("/api/provider/disconnect")
async def provider_disconnect():
    """Back to LM Studio mode. The key stays saved for one-click reconnect."""
    await llm_client.deactivate_openrouter()
    persist_env_values({"LLM_PROVIDER": "lmstudio"})
    # Same re-sync as startup: align the active model with LM Studio's reality.
    if await llm_client.check_connectivity():
        loaded = await llm_client.detect_loaded_model()
        if loaded:
            llm_client.set_active_model(loaded)
    logger.info("Disconnected from OpenRouter — back to LM Studio mode")
    return {"ok": True, "provider": "lmstudio"}


# --- Conversations -------------------------------------------------------

@app.get("/api/conversations")
async def list_conversations():
    return db.list_conversations()


@app.post("/api/conversations")
async def create_conversation():
    return db.create_conversation()


@app.get("/api/conversations/{conversation_id}/messages")
async def conversation_messages(conversation_id: int):
    conv = db.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    # If a turn is paused awaiting confirmation, surface it so a reloaded page can
    # re-render the approve/deny prompt.
    pending = db.get_pending_confirmation(conversation_id)
    pending_calls = json.loads(pending["tool_calls_json"]) if pending else None
    return {
        "conversation": conv,
        "messages": db.get_render_messages(conversation_id),
        "last_total_tokens": conv.get("last_total_tokens"),
        "pending_confirmation": pending_calls,
    }


@app.delete("/api/conversations/{conversation_id}")
async def remove_conversation(conversation_id: int):
    db.delete_conversation(conversation_id)
    return {"ok": True}


# --- Game scores ----------------------------------------------------------

@app.get("/api/scores")
async def get_scores(limit: int = 10):
    return {"scores": db.top_scores(limit=max(1, min(limit, 50)))}


@app.post("/api/scores")
async def submit_score(req: ScoreSubmit):
    entry = db.insert_score(req.initials, req.score, req.level)
    # Fresh top-10 in the response saves the frontend a second fetch.
    return {"entry": entry, "top": db.top_scores()}


@app.get("/api/game-agent/status")
async def game_agent_status():
    """Read-only policy status for the arena header. Never loads torch or LM
    Studio; a plain GET, so no confirmation tier. Triggers the throttled
    hot-reload check, so a freshly published policy surfaces here within ~1s."""
    from app import rl_policy

    try:
        policy = rl_policy.get_policy()
    except PolicyUnavailableError:
        return {"available": False}
    return {
        "available": True,
        "observation_version": policy.observation_version,
        "training_steps": policy.training_steps,
        "eval_score": policy.eval_score,
    }


@app.websocket("/ws/game-agent")
async def game_agent(websocket: WebSocket):
    """Serve level-one Breakout actions from the exported numpy policy."""
    await websocket.accept()
    from app import rl_policy

    try:
        rl_policy.get_policy()  # availability gate; also primes the singleton
    except PolicyUnavailableError:
        await websocket.send_json({"error": "no-policy"})
        await websocket.close(code=1000)
        return

    try:
        while True:
            try:
                payload = await websocket.receive_json()
                # Re-fetch each message so an atomically hot-reloaded policy takes
                # effect mid-connection; once loaded this keeps the last good one.
                policy = rl_policy.get_policy()
                bricks = payload.get("bricks")
                if (
                    not isinstance(bricks, str)
                    or len(bricks) != 60
                    or not bricks.isdigit()
                ):
                    raise ValueError("invalid level-one brick string")
                state = {
                    "paddle_x": float(payload["paddle_x"]),
                    "balls": [
                        [float(value) for value in ball]
                        for ball in payload["balls"]
                        if len(ball) == 4
                    ],
                    "speed": float(payload["speed"]),
                    "pierce": float(payload["pierce"]),
                    "bricks": [(int(hits), 1) for hits in bricks],
                }
                action = policy.act(state)
            except (
                PolicyUnavailableError,
                AttributeError,
                IndexError,
                KeyError,
                TypeError,
                ValueError,
                OverflowError,
            ):
                action = 0
            await websocket.send_json({"action": action})
    except WebSocketDisconnect:
        return


def _title_from_message(message: str) -> str:
    title = " ".join(message.strip().split())
    return title[:47] + "…" if len(title) > 48 else title or "New conversation"


async def _sse_from(agen):
    """Wrap an agent-loop async generator of SSEEvents into an SSE byte stream,
    with a last-resort error guard."""
    try:
        async for event in agen:
            yield event.encode()
    except Exception as exc:  # noqa: BLE001 - last-resort guard
        logger.exception("Unhandled error in agent turn")
        yield SSEEvent("error", {"message": str(exc)}).encode()
        yield SSEEvent("done").encode()


async def _turn_blocker() -> str | None:
    """Reason the LLM turn cannot start, or None if it can.

    This is the no-auto-load guard: LM Studio's JIT load can only trigger from
    /chat/completions, so refusing here (before any DB write) means chatting
    never loads a model behind the user's back.
    """
    if llm_client.get_provider() == "openrouter":
        if not llm_client.get_active_model():
            return "No OpenRouter model selected — pick one in the header first."
        return None
    # Two-step check only to give distinct messages: detect_loaded_model()
    # returns None for both "unreachable" and "nothing loaded".
    if not await llm_client.check_connectivity():
        return "LM Studio is not reachable — start the server first."
    if await llm_client.detect_loaded_model() is None:
        return "No model is loaded in LM Studio — load one with the Load model button first."
    return None


async def _refusal_stream(message: str):
    """SSE refusal using the existing error-bubble contract — the frontend's
    postAndRead() never checks res.ok, so an HTTP error would break it."""
    yield SSEEvent("error", {"message": message}).encode()
    yield SSEEvent("done").encode()


@app.post("/api/conversations/{conversation_id}/chat")
async def chat(conversation_id: int, req: ChatRequest):
    conv = db.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    blocked = await _turn_blocker()
    if blocked:
        return StreamingResponse(_refusal_stream(blocked), media_type="text/event-stream")
    # Auto-title the conversation from its first user message.
    if not db.get_render_messages(conversation_id):
        title = _title_from_message(req.message) if req.message.strip() else "📷 Image"
        db.update_conversation_title(conversation_id, title)
    return StreamingResponse(
        _sse_from(agent_loop.run_agent_turn(conversation_id, req.message, req.images)),
        media_type="text/event-stream",
    )


@app.post("/api/conversations/{conversation_id}/confirm")
async def confirm(conversation_id: int, req: ConfirmRequest):
    conv = db.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    # confirm() also resumes the LLM after tool execution, so it gets the same
    # no-auto-load guard as chat().
    blocked = await _turn_blocker()
    if blocked:
        return StreamingResponse(_refusal_stream(blocked), media_type="text/event-stream")
    return StreamingResponse(
        _sse_from(agent_loop.resume_after_confirmation(conversation_id, req.approved)),
        media_type="text/event-stream",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
