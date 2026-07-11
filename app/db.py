import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT 'New conversation',
    created_at TEXT NOT NULL,
    last_total_tokens INTEGER
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT,
    tool_calls_json TEXT,
    tool_call_id TEXT,
    status TEXT NOT NULL DEFAULT 'complete',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connect():
    conn = sqlite3.connect(settings.db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_SCHEMA)
        # Migration: images_json holds a JSON array of base64 data URLs attached to
        # a (user) message, for vision-capable models. Added after initial release,
        # so add it to pre-existing databases without a destructive rebuild.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
        if "images_json" not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN images_json TEXT")


# --- Conversations -------------------------------------------------------

def create_conversation(title: str = "New conversation") -> dict[str, Any]:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO conversations (title, created_at) VALUES (?, ?)",
            (title, _now()),
        )
        conv_id = cur.lastrowid
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conv_id,)
        ).fetchone()
    return dict(row)


def list_conversations() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, title, created_at, last_total_tokens "
            "FROM conversations ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_conversation(conversation_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
    return dict(row) if row else None


def update_conversation_title(conversation_id: int, title: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id)
        )


def update_conversation_tokens(conversation_id: int, total_tokens: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE conversations SET last_total_tokens = ? WHERE id = ?",
            (total_tokens, conversation_id),
        )


def delete_conversation(conversation_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))


# --- Messages ------------------------------------------------------------

def set_message_status(message_id: int, status: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE messages SET status = ? WHERE id = ?", (status, message_id)
        )


def get_pending_confirmation(conversation_id: int) -> dict[str, Any] | None:
    """Return the assistant message awaiting tool-call confirmation, if any."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, tool_calls_json FROM messages "
            "WHERE conversation_id = ? AND status = 'pending_confirmation' "
            "ORDER BY id DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
    return dict(row) if row else None


def insert_message(
    conversation_id: int,
    role: str,
    content: str | None = None,
    tool_calls_json: str | None = None,
    tool_call_id: str | None = None,
    status: str = "complete",
    images_json: str | None = None,
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO messages "
            "(conversation_id, role, content, tool_calls_json, tool_call_id, status, "
            "images_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (conversation_id, role, content, tool_calls_json, tool_call_id, status,
             images_json, _now()),
        )
        return cur.lastrowid


def get_openai_messages(conversation_id: int) -> list[dict[str, Any]]:
    """Return the conversation's messages in OpenAI wire format, ready to send
    straight to llm_client (assistant tool_calls and tool results reconstructed)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content, tool_calls_json, tool_call_id, images_json "
            "FROM messages WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ).fetchall()

    messages: list[dict[str, Any]] = []
    for r in rows:
        role = r["role"]
        if role == "assistant" and r["tool_calls_json"]:
            tool_calls = json.loads(r["tool_calls_json"])
            messages.append(
                {
                    "role": "assistant",
                    "content": r["content"],
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"]),
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            )
        elif role == "tool":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": r["tool_call_id"],
                    "content": r["content"],
                }
            )
        elif role == "user" and r["images_json"]:
            # Multimodal user turn: OpenAI/LM Studio expect content as an array of
            # text + image_url parts.
            parts: list[dict[str, Any]] = []
            if r["content"]:
                parts.append({"type": "text", "text": r["content"]})
            for url in json.loads(r["images_json"]):
                parts.append({"type": "image_url", "image_url": {"url": url}})
            messages.append({"role": "user", "content": parts})
        else:
            messages.append({"role": role, "content": r["content"]})
    return messages


def get_render_messages(conversation_id: int) -> list[dict[str, Any]]:
    """Return messages shaped for the frontend to re-render a past conversation.

    Unlike get_openai_messages, this keeps tool_calls/results as separate,
    display-friendly items and parses their JSON payloads.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content, tool_calls_json, tool_call_id, images_json "
            "FROM messages WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ).fetchall()

    items: list[dict[str, Any]] = []
    for r in rows:
        role = r["role"]
        if role == "system":
            continue  # internal, not shown in the transcript
        if role == "assistant" and r["tool_calls_json"]:
            items.append(
                {
                    "role": "assistant",
                    "content": r["content"] or "",
                    "tool_calls": json.loads(r["tool_calls_json"]),
                }
            )
        elif role == "tool":
            try:
                result = json.loads(r["content"]) if r["content"] else None
            except json.JSONDecodeError:
                result = r["content"]
            items.append({"role": "tool", "tool_call_id": r["tool_call_id"], "result": result})
        else:
            item: dict[str, Any] = {"role": role, "content": r["content"] or ""}
            if role == "user" and r["images_json"]:
                item["images"] = json.loads(r["images_json"])
            items.append(item)
    return items
