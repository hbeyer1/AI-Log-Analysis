"""Parse conversations.json exports and filter to substantive sessions."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


SUBSTANTIVE_MIN_CHARS = 100
SUBSTANTIVE_MIN_MESSAGES = 2


@dataclass
class Message:
    sender: str
    text: str


@dataclass
class Session:
    uuid: str
    name: str
    created_at: str
    messages: list[Message]

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
            "messages": [asdict(m) for m in self.messages],
            "total_chars": self.total_chars,
            "message_count": len(self.messages),
        }

    def to_transcript(self) -> str:
        """Format as a plain-text transcript for prompts."""
        lines = []
        for m in self.messages:
            role = "User" if m.sender == "human" else "Assistant"
            lines.append(f"[{role}]\n{m.text}\n")
        return "\n".join(lines).strip()


def _extract_message_text(msg: dict[str, Any]) -> str:
    """Prefer the top-level `text` field; fall back to concatenating text-type content blocks."""
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


def parse_conversations(raw: list[dict[str, Any]]) -> list[Session]:
    sessions: list[Session] = []
    for conv in raw:
        if not isinstance(conv, dict):
            continue
        messages = [
            Message(sender=m.get("sender", "unknown"), text=_extract_message_text(m))
            for m in (conv.get("chat_messages") or [])
            if isinstance(m, dict)
        ]
        sessions.append(
            Session(
                uuid=conv.get("uuid", ""),
                name=conv.get("name") or "(untitled)",
                created_at=conv.get("created_at", ""),
                messages=messages,
            )
        )
    return sessions


def filter_substantive(sessions: list[Session]) -> list[Session]:
    return [s for s in sessions if s.is_substantive]
