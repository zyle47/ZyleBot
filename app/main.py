import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.requests import Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import agent_loop, db, llm_client, model_manager, pages, stt
from app.config import settings
from app.models import ChatRequest, ConfirmRequest, ModelRequest
from app.sse import SSEEvent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("zylebot.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    llm_client.init_client()
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
    reachable = await llm_client.check_connectivity()
    # Force-refresh so changing the context length (or model) in LM Studio is
    # reflected after a browser refresh, without restarting ZyleBot.
    context_length = (
        await llm_client.get_loaded_context_length(force_refresh=True) if reachable else None
    )
    active = llm_client.get_active_model()
    return {
        "lmstudio_reachable": reachable,
        "model": active,
        "model_alias": model_manager.get_alias(active),
        "context_length": context_length,
    }


@app.post("/api/server/start")
async def start_server():
    """Start LM Studio's local server via the lms CLI. The button click *is* the
    human approval, so no extra confirmation gate (same as model switching)."""
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


@app.get("/api/models")
async def list_models():
    models = await llm_client.list_chat_models()
    for m in models:
        m["alias"] = model_manager.get_alias(m["id"])
    return {"active": llm_client.get_active_model(), "models": models}


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


@app.post("/api/conversations/{conversation_id}/chat")
async def chat(conversation_id: int, req: ChatRequest):
    conv = db.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
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
    return StreamingResponse(
        _sse_from(agent_loop.resume_after_confirmation(conversation_id, req.approved)),
        media_type="text/event-stream",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
