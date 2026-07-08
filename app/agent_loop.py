import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

from app import db, llm_client
from app.config import settings
from app.platform_info import OS_NAME, SHELL_NAME
from app.sse import SSEEvent
from app.tools import RiskTier, execute_tool, get_openai_tool_schemas, get_tool_risk_tier

logger = logging.getLogger("zylebot.agent_loop")

_BASE_SYSTEM_PROMPT = (
    "You are ZyleBot, a helpful local assistant running on the user's computer. "
    "You have read-only tools to inspect the filesystem and system, a get_weather tool, "
    "and tools to search the web (web_search) and read pages (fetch_url).\n"
    "Tool guidance:\n"
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
    conversation_id: int, user_message: str
) -> AsyncIterator[SSEEvent]:
    """Run one multi-step ReAct turn for a single conversation.

    Loads *only* this conversation's history from SQLite, streams the turn, and
    persists every new message back under the same conversation_id.
    """
    _cancel_pending(conversation_id)
    db.insert_message(conversation_id, "user", user_message)
    async for ev in _react_loop(conversation_id):
        yield ev


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


async def _react_loop(conversation_id: int) -> AsyncIterator[SSEEvent]:
    """The core multi-step loop. Rebuilds the model input from the DB (so it works
    for both a fresh turn and a post-confirmation resume), streams each step, and
    pauses by returning after a `confirmation_required` event when a step requests
    a confirm_required tool."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt()},
        *db.get_openai_messages(conversation_id),
    ]
    tools = get_openai_tool_schemas({RiskTier.SAFE, RiskTier.CONFIRM_REQUIRED})
    loop = asyncio.get_running_loop()

    final_content = ""
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
            # No tools requested -> this is the final answer.
            db.insert_message(conversation_id, "assistant", content_buffer)
            yield SSEEvent("final", {"content": content_buffer, "truncated_max_steps": False})
            async for ev in _finish(conversation_id, last_usage):
                yield ev
            return

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
        needs_confirm = [
            tc for tc in tool_calls
            if get_tool_risk_tier(tc["name"]) == RiskTier.CONFIRM_REQUIRED
        ]
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

    # Circuit breaker: steps exhausted -> force ONE final answer with tools disabled.
    messages.append(
        {
            "role": "system",
            "content": (
                "You have reached the tool-use limit for this turn. Do not request any "
                "more tools. Answer the user now, as best you can, using the information "
                "already gathered above."
            ),
        }
    )
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

    final_content = content_buffer or final_content
    db.insert_message(conversation_id, "assistant", final_content)
    yield SSEEvent("final", {"content": final_content, "truncated_max_steps": True})
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
