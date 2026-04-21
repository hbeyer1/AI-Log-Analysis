"""Parse conversations.json exports and filter to substantive sessions."""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any, Optional


SUBSTANTIVE_MIN_CHARS = 100
SUBSTANTIVE_MIN_MESSAGES = 2


@dataclass
class Message:
    sender: str
    text: str
    created_at: Optional[str] = None
    tool_calls: list[str] = field(default_factory=list)


@dataclass
class Session:
    uuid: str
    name: str
    created_at: str
    messages: list[Message]
    models_used: list[str] = field(default_factory=list)

    @property
    def total_chars(self) -> int:
        return sum(len(m.text) for m in self.messages)

    @property
    def is_substantive(self) -> bool:
        return (
            self.total_chars >= SUBSTANTIVE_MIN_CHARS
            and len(self.messages) >= SUBSTANTIVE_MIN_MESSAGES
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "created_at": self.created_at,
            "models_used": self.models_used,
            "messages": [asdict(m) for m in self.messages],
            "total_chars": self.total_chars,
            "message_count": len(self.messages),
        }


def _extract_message_text(msg: dict[str, Any]) -> str:
    text = msg.get("text") or ""
    if text:
        return text
    content = msg.get("content")
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return ""


def _extract_tool_calls(msg: dict[str, Any]) -> list[str]:
    """Return tool names invoked inside this message's content blocks.

    Gracefully returns [] when content is missing, not a list, or has no
    tool_use blocks. Deduplicates while preserving first-seen order.
    """
    content = msg.get("content")
    if not isinstance(content, list):
        return []
    seen: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_use":
            continue
        name = block.get("name")
        if not isinstance(name, str) or not name:
            continue
        if name not in seen:
            seen.append(name)
    return seen


def _extract_model(msg: dict[str, Any]) -> Optional[str]:
    for key in ("model", "model_id", "model_name"):
        v = msg.get(key)
        if isinstance(v, str) and v:
            return v
    meta = msg.get("metadata")
    if isinstance(meta, dict):
        for key in ("model", "model_id", "model_name"):
            v = meta.get(key)
            if isinstance(v, str) and v:
                return v
    return None


def parse_conversations(raw: list[dict[str, Any]]) -> list[Session]:
    sessions: list[Session] = []
    for conv in raw:
        if not isinstance(conv, dict):
            continue
        messages: list[Message] = []
        models: list[str] = []
        for m in (conv.get("chat_messages") or []):
            if not isinstance(m, dict):
                continue
            messages.append(Message(
                sender=m.get("sender", "unknown"),
                text=_extract_message_text(m),
                created_at=m.get("created_at") or None,
                tool_calls=_extract_tool_calls(m),
            ))
            model = _extract_model(m)
            if model and model not in models:
                models.append(model)
        sessions.append(
            Session(
                uuid=conv.get("uuid", ""),
                name=conv.get("name") or "(untitled)",
                created_at=conv.get("created_at", ""),
                messages=messages,
                models_used=models,
            )
        )
    return sessions


def filter_substantive(sessions: list[Session]) -> list[Session]:
    return [s for s in sessions if s.is_substantive]
