import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

from app import db, llm_client
from app.command_guard import Verdict, check_command
from app.config import settings
from app.platform_info import OS_NAME, SHELL_NAME
from app.sse import SSEEvent
from app.tools import RiskTier, execute_tool, get_openai_tool_schemas, get_tool_risk_tier

logger = logging.getLogger("zylebot.agent_loop")

_BASE_SYSTEM_PROMPT = (
    "You are ZyleBot, a helpful local assistant running on the user's computer. "
    "Always look into your memory first!"
    "You have read-only tools to inspect the filesystem and system, a get_weather tool, "
    "and tools to search the web (web_search) and read pages (fetch_url).\n"
    "Tool guidance:\n"
    "- If the user asks for information that may exist on the web (song lyrics, articles, "
    "documentation, product details, current facts) explicitly, RETRIEVE it with web_search/fetch_url "
    "instead of declining or answering from memory. You have live web access — do not refuse "
    "such requests on copyright or knowledge-cutoff grounds; find the source and summarize it.\n"
    "- For weather, always use get_weather (one accurate call) — never web_search.\n"
    "- For current events or facts past your training data, use web_search FIRST to "
    "find the exact URL, THEN fetch_url that one page. Never guess or invent URLs.\n"
    "- If a fetch_url result is truncated, continue the SAME page by calling fetch_url "
    "again with the next_offset it gives you — do NOT re-fetch the same offset or keep "
    "trying different URLs for content you already have.\n"
    "- Never repeat a tool call that already returned an error or empty result.\n"
    "- Stop calling tools as soon as you can answer; then give a clear, concise reply "
    "citing any source URLs. Never invent facts or URLs.\n"
    "If you have gathered partial information and cannot get more, answer with what you "
    "have rather than giving up."
)


def build_system_prompt() -> str:
    """The system prompt is built fresh each turn (never persisted) so config
    changes like USER_NAME take effect immediately. The user's name is the only
    globally-shared fact; there is no cross-conversation memory."""
    prompt = (
        f"{_BASE_SYSTEM_PROMPT} You are running on {OS_NAME}; run_command uses "
        f"{SHELL_NAME}, so write any commands in {SHELL_NAME} syntax. "
        f"Today's date is {date.today().isoformat()}."
    )
    if settings.user_name:
        prompt += f" The user's name is {settings.user_name}."
    return prompt


async def _context_event(last_usage: dict[str, Any] | None) -> SSEEvent | None:
    """Build a `context` SSE event from the turn's final token usage, if available."""
    if not last_usage:
        return None
    used = last_usage.get("total_tokens")
    if used is None:
        return None
    max_ctx = await llm_client.get_loaded_context_length()
    return SSEEvent(
        "context",
        {
            "used": used,
            "max": max_ctx,
            "prompt_tokens": last_usage.get("prompt_tokens"),
            "completion_tokens": last_usage.get("completion_tokens"),
        },
    )


def _fallback_from_tool_results(messages: list[dict[str, Any]]) -> str:
    """Build a minimal reply from the most recent tool result, for the rare case
    where the model produced no text at all. Better than a blank bubble."""
    for msg in reversed(messages):
        if msg.get("role") == "tool" and msg.get("content"):
            try:
                data = json.loads(msg["content"])
            except (json.JSONDecodeError, TypeError):
                return str(msg["content"])[:800]
            if isinstance(data, dict) and data.get("error"):
                return f"I ran into an issue: {data['error']}"
            if isinstance(data, dict):
                lines = "\n".join(f"- {k}: {v}" for k, v in data.items())
                return f"Here's what I found:\n{lines}"
            return f"Here's what I found:\n{json.dumps(data, indent=2)[:800]}"
    return "I wasn't able to produce a response for that. Please try rephrasing."


def _needs_confirmation(tc: dict[str, Any]) -> bool:
    if get_tool_risk_tier(tc["name"]) != RiskTier.CONFIRM_REQUIRED:
        return False
    if tc["name"] == "run_command":
        command = (tc.get("arguments") or {}).get("command", "")
        if check_command(command).verdict is Verdict.ALLOW:
            return False
    return True


def _cancel_pending(conversation_id: int) -> None:
    """If a confirmation was left unanswered, resolve it as cancelled so the stored
    history stays valid (every assistant tool_call gets a following tool result)."""
    pending = db.get_pending_confirmation(conversation_id)
    if not pending:
        return
    for tc in json.loads(pending["tool_calls_json"] or "[]"):
        db.insert_message(
            conversation_id,
            "tool",
            json.dumps({"note": "Cancelled — the user moved on without confirming."}),
            tool_call_id=tc["id"],
        )
    db.set_message_status(pending["id"], "complete")


async def run_agent_turn(
    conversation_id: int, user_message: str, images: list[str] | None = None
) -> AsyncIterator[SSEEvent]:
    """Run one multi-step ReAct turn for a single conversation.

    Loads *only* this conversation's history from SQLite, streams the turn, and
    persists every new message back under the same conversation_id. `images` is an
    optional list of base64 data URLs attached to this user turn (vision models).
    """
    _cancel_pending(conversation_id)
    db.insert_message(
        conversation_id,
        "user",
        user_message,
        images_json=json.dumps(images) if images else None,
    )
    # This model can't see an image AND use tools in the same request, so a turn
    # that carries an image runs in vision mode (tools disabled) — see _react_loop.
    async for ev in _react_loop(conversation_id, vision_mode=bool(images)):
        yield ev


def _collapse_multimodal(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace image parts in any multimodal message with a short text placeholder.

    Used on tool-enabled turns because this model can't combine vision with
    tool-calling (attaching `tools` makes LM Studio drop the image), and because
    re-sending old images every turn needlessly burns vision tokens.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            texts = [p["text"] for p in content if p.get("type") == "text" and p.get("text")]
            n_imgs = sum(1 for p in content if p.get("type") == "image_url")
            placeholder = " ".join(texts)
            if n_imgs:
                tag = f"[{n_imgs} image{'s' if n_imgs > 1 else ''} attached earlier]"
                placeholder = f"{placeholder} {tag}".strip()
            out.append({**m, "content": placeholder})
        else:
            out.append(m)
    return out


async def resume_after_confirmation(
    conversation_id: int, approved: bool
) -> AsyncIterator[SSEEvent]:
    """Resume a turn paused on a confirm_required tool call: execute (or refuse)
    the pending calls, persist their results, then continue the ReAct loop."""
    pending = db.get_pending_confirmation(conversation_id)
    if not pending:
        yield SSEEvent("error", {"message": "no action is awaiting confirmation"})
        yield SSEEvent("done")
        return

    loop = asyncio.get_running_loop()
    for tc in json.loads(pending["tool_calls_json"] or "[]"):
        yield SSEEvent("tool_call", {"id": tc["id"], "name": tc["name"], "arguments": tc["arguments"]})
        if approved:
            result = await loop.run_in_executor(None, execute_tool, tc["name"], tc["arguments"])
        else:
            result = {"note": "User denied this action; it was not performed."}
        yield SSEEvent("tool_result", {"id": tc["id"], "name": tc["name"], "result": result})
        db.insert_message(conversation_id, "tool", json.dumps(result), tool_call_id=tc["id"])

    db.set_message_status(pending["id"], "complete")
    async for ev in _react_loop(conversation_id):
        yield ev


async def _react_loop(
    conversation_id: int, vision_mode: bool = False
) -> AsyncIterator[SSEEvent]:
    """The core multi-step loop. Rebuilds the model input from the DB (so it works
    for both a fresh turn and a post-confirmation resume), streams each step, and
    pauses by returning after a `confirmation_required` event when a step requests
    a confirm_required tool.

    In `vision_mode` (the current turn carries an image) tools are disabled so the
    model can actually see the image — this model drops images when tools are
    attached. On normal turns, historical images are collapsed to text placeholders.
    """
    history = db.get_openai_messages(conversation_id)
    if not vision_mode:
        history = _collapse_multimodal(history)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt()},
        *history,
    ]
    tools = (
        None
        if vision_mode
        else get_openai_tool_schemas({RiskTier.SAFE, RiskTier.CONFIRM_REQUIRED})
    )
    loop = asyncio.get_running_loop()

    final_content = ""
    # Why the forced final answer at the end runs: "max_steps" (exhausted the loop)
    # or "empty" (a step returned no tools and no text — see the guard below).
    force_reason = "max_steps"
    last_usage: dict[str, Any] | None = None
    # Dedup guard: identical (name, args) calls already run this loop are skipped.
    executed_calls: set[str] = set()
    for step in range(settings.agent_max_steps):
        content_buffer = ""
        tc_accumulator: dict[int, dict[str, Any]] = {}

        try:
            async for chunk in llm_client.stream_chat_completion(messages, tools=tools):
                if chunk.get("usage"):
                    last_usage = chunk["usage"]
                choices = chunk.get("choices") or [{}]
                delta = choices[0].get("delta", {})

                reasoning = delta.get("reasoning_content")
                if reasoning:
                    yield SSEEvent("reasoning_token", {"text": reasoning})

                token = delta.get("content")
                if token:
                    content_buffer += token
                    yield SSEEvent("assistant_token", {"text": token})

                if delta.get("tool_calls"):
                    llm_client.accumulate_tool_call_deltas(tc_accumulator, delta["tool_calls"])
        except Exception as exc:  # noqa: BLE001 - surface any streaming failure
            logger.exception("LM Studio streaming failed")
            yield SSEEvent("error", {"message": str(exc)})
            yield SSEEvent("done")
            return

        tool_calls = llm_client.finalize_tool_calls(tc_accumulator)

        if not tool_calls:
            if content_buffer.strip():
                # No tools requested and we have text -> this is the final answer.
                db.insert_message(conversation_id, "assistant", content_buffer)
                yield SSEEvent("final", {"content": content_buffer, "truncated_max_steps": False})
                async for ev in _finish(conversation_id, last_usage):
                    yield ev
                return
            # No tools AND no text: the model produced nothing usable (this reasoning
            # model sometimes never closes its <think> block, leaving `content` empty
            # while emitting a stray "<function...>" into its reasoning). Force one
            # clean, tool-less answer instead of emitting a blank reply that looks
            # like a hang.
            force_reason = "empty"
            break

        # Persist the assistant message that requested the tools (in-memory OpenAI
        # format for the next step, and compact form in the DB).
        messages.append(
            {
                "role": "assistant",
                "content": content_buffer or None,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])},
                    }
                    for tc in tool_calls
                ],
            }
        )
        msg_id = db.insert_message(
            conversation_id,
            "assistant",
            content_buffer or None,
            tool_calls_json=json.dumps(
                [{"id": tc["id"], "name": tc["name"], "arguments": tc["arguments"]} for tc in tool_calls]
            ),
        )

        # If any requested tool needs confirmation, pause the whole turn: mark the
        # message pending and hand off to the UI. The turn resumes via /confirm.
        needs_confirm = [tc for tc in tool_calls if _needs_confirmation(tc)]
        if needs_confirm:
            db.set_message_status(msg_id, "pending_confirmation")
            yield SSEEvent(
                "confirmation_required",
                {
                    "message_id": msg_id,
                    "calls": [
                        {"id": tc["id"], "name": tc["name"], "arguments": tc["arguments"]}
                        for tc in needs_confirm
                    ],
                },
            )
            async for ev in _finish(conversation_id, last_usage):
                yield ev
            return

        # Otherwise execute all (safe) tool calls now.
        for tc in tool_calls:
            name, arguments = tc["name"], tc["arguments"]
            yield SSEEvent("tool_call", {"id": tc["id"], "name": name, "arguments": arguments})

            call_key = f"{name}:{json.dumps(arguments, sort_keys=True)}"
            if call_key in executed_calls:
                result = {
                    "note": "Duplicate call skipped — you already ran this exact tool "
                    "call this turn. Use the result you already have, or do something "
                    "different (e.g. a new offset, a different URL, or answer now)."
                }
            else:
                executed_calls.add(call_key)
                result = await loop.run_in_executor(None, execute_tool, name, arguments)

            yield SSEEvent("tool_result", {"id": tc["id"], "name": name, "result": result})
            result_json = json.dumps(result)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result_json})
            db.insert_message(conversation_id, "tool", result_json, tool_call_id=tc["id"])
        # Loop again: model now sees the tool results and decides its next move.

    # Force ONE final answer with tools disabled. Reached either because the model
    # hit the step limit ("max_steps") or returned an empty, tool-less step ("empty").
    if force_reason == "empty":
        nudge = (
            "You have not answered the user yet. Answer now in plain text, using the "
            "information already gathered above. Do NOT call any tools and do NOT emit "
            "<think> or <function> tags — write only the final reply."
        )
    else:
        nudge = (
            "You have reached the tool-use limit for this turn. Do not request any "
            "more tools. Answer the user now, as best you can, using the information "
            "already gathered above."
        )
    messages.append({"role": "system", "content": nudge})
    content_buffer = ""
    try:
        async for chunk in llm_client.stream_chat_completion(messages, tools=None):
            if chunk.get("usage"):
                last_usage = chunk["usage"]
            choices = chunk.get("choices") or [{}]
            delta = choices[0].get("delta", {})
            reasoning = delta.get("reasoning_content")
            if reasoning:
                yield SSEEvent("reasoning_token", {"text": reasoning})
            token = delta.get("content")
            if token:
                content_buffer += token
                yield SSEEvent("assistant_token", {"text": token})
    except Exception as exc:  # noqa: BLE001
        logger.exception("LM Studio streaming failed on forced final answer")
        yield SSEEvent("error", {"message": str(exc)})
        yield SSEEvent("done")
        return

    final_content = content_buffer.strip() or final_content
    if not final_content:
        # Last-resort safety net: never show a blank bubble. Summarize the most
        # recent tool result so the user at least sees what was gathered.
        final_content = _fallback_from_tool_results(messages)
    db.insert_message(conversation_id, "assistant", final_content)
    yield SSEEvent(
        "final",
        {"content": final_content, "truncated_max_steps": force_reason == "max_steps"},
    )
    async for ev in _finish(conversation_id, last_usage):
        yield ev


async def _finish(
    conversation_id: int, last_usage: dict[str, Any] | None
) -> AsyncIterator[SSEEvent]:
    """Persist the turn's token count and emit the context + done events."""
    if last_usage and last_usage.get("total_tokens") is not None:
        db.update_conversation_tokens(conversation_id, last_usage["total_tokens"])
    ctx_event = await _context_event(last_usage)
    if ctx_event:
        yield ctx_event
    yield SSEEvent("done")
