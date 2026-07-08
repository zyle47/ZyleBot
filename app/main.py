import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.requests import Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import agent_loop, db, llm_client
from app.config import settings
from app.models import ChatRequest, ConfirmRequest
from app.sse import SSEEvent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("zylebot.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    llm_client.init_client()
    if await llm_client.check_connectivity():
        logger.info("LM Studio reachable at %s", settings.lmstudio_base_url)
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
templates = Jinja2Templates(directory="app/templates")


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/health")
async def health():
    reachable = await llm_client.check_connectivity()
    # Force-refresh so changing the context length (or model) in LM Studio is
    # reflected after a browser refresh, without restarting ZyleBot.
    context_length = (
        await llm_client.get_loaded_context_length(force_refresh=True) if reachable else None
    )
    return {
        "lmstudio_reachable": reachable,
        "model": settings.lmstudio_model,
        "context_length": context_length,
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
        db.update_conversation_title(conversation_id, _title_from_message(req.message))
    return StreamingResponse(
        _sse_from(agent_loop.run_agent_turn(conversation_id, req.message)),
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
