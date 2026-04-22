"""Stage 1 — PII redaction. Replaces PII with typed placeholders, verifies
structurally, retries once on failure, writes redacted_sessions.jsonl."""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from data import Session, Message
from llm_client import LLMClient, DEFAULT_MODEL_LIGHT


PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text()


def save_prompt(name: str, content: str) -> None:
    (PROMPTS_DIR / name).write_text(content)


@dataclass
class PIIResult:
    session_id: str
    name: str
    created_at: str
    models_used: list[str]
    messages: list[Message]
    skipped: bool = False
    verified: bool = False
    failed_reason: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_s: float = 0.0
    model: str = ""


def _messages_payload(session: Session) -> list[dict[str, Any]]:
    return [
        {"idx": i, "role": "user" if m.sender == "human" else m.sender, "text": m.text}
        for i, m in enumerate(session.messages)
    ]


def _strip_fences(text: str) -> str:
    s = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```\s*$", s, re.DOTALL)
    return m.group(1).strip() if m else s


def _extract_json_array(text: str) -> Optional[list[Any]]:
    cleaned = _strip_fences(text)
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, list) else None


# Accept redacted text up to 1.5× longer (placeholders can be verbose) and
# down to 0.3× shorter (short messages can be almost entirely PII).
LEN_RATIO_MAX = 1.5
LEN_RATIO_MIN = 0.3


def _verify(original: list[Message], redacted: list[dict[str, Any]]) -> Optional[str]:
    if len(redacted) != len(original):
        return f"message_count_mismatch:{len(redacted)}!={len(original)}"

    for i, (orig, red) in enumerate(zip(original, redacted)):
        red_role = str(red.get("role", "")).strip()
        orig_role = "user" if orig.sender == "human" else orig.sender
        if red_role and red_role != orig_role:
            return f"role_mismatch_at_{i}:{red_role}!={orig_role}"

        red_text = red.get("text")
        if not isinstance(red_text, str):
            return f"text_not_string_at_{i}"

        orig_len = max(1, len(orig.text))
        red_len = len(red_text)
        ratio = red_len / orig_len
        if ratio > LEN_RATIO_MAX or ratio < LEN_RATIO_MIN:
            return f"length_ratio_at_{i}:{ratio:.2f}"

    return None


async def _redact_one(
    client: LLMClient,
    session: Session,
    prompt_template: str,
    model: str,
) -> PIIResult:
    started = time.monotonic()
    res = PIIResult(
        session_id=session.uuid,
        name=session.name,
        created_at=session.created_at,
        models_used=session.models_used,
        messages=session.messages,
        model=model,
    )

    payload = _messages_payload(session)
    payload_json = json.dumps(payload, ensure_ascii=False)
    prompt = prompt_template.replace("{messages_json}", payload_json)

    try:
        result = await client.complete(prompt, model=model, max_tokens=16384, temperature=0.0)
        res.input_tokens += result.input_tokens
        res.output_tokens += result.output_tokens
        res.cost_usd += result.cost_usd
        arr = _extract_json_array(result.text)

        reason = None
        if arr is None:
            reason = "unparseable_response"
        else:
            reason = _verify(session.messages, arr)

        if reason:
            strict = prompt + (
                "\n\nYour previous response was rejected: " + reason +
                ". Return ONLY the JSON array with exactly the same number of messages in the same order, "
                "with identical role values, and only PII replaced by [TYPE_N] placeholders."
            )
            retry = await client.complete(strict, model=model, max_tokens=16384, temperature=0.0)
            res.input_tokens += retry.input_tokens
            res.output_tokens += retry.output_tokens
            res.cost_usd += retry.cost_usd
            arr = _extract_json_array(retry.text)
            reason = _verify(session.messages, arr) if arr is not None else "unparseable_response"

        if reason is None and arr is not None:
            new_messages: list[Message] = []
            for orig, red in zip(session.messages, arr):
                new_messages.append(Message(
                    sender=orig.sender,
                    text=str(red.get("text", "")),
                    created_at=orig.created_at,
                    tool_calls=orig.tool_calls,
                ))
            res.messages = new_messages
            res.verified = True
        else:
            res.failed_reason = reason or "unknown"
    except Exception as e:
        res.failed_reason = f"exception:{e}"

    res.duration_s = round(time.monotonic() - started, 2)
    return res


def skipped_result(session: Session) -> PIIResult:
    return PIIResult(
        session_id=session.uuid,
        name=session.name,
        created_at=session.created_at,
        models_used=session.models_used,
        messages=session.messages,
        skipped=True,
    )


async def run_pii(
    sessions: list[Session],
    *,
    enabled: bool = True,
    prompt_template: Optional[str] = None,
    model: str = DEFAULT_MODEL_LIGHT,
    concurrency: int = 5,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> list[PIIResult]:
    if not enabled:
        results = [skipped_result(s) for s in sessions]
        if progress_cb:
            progress_cb(len(results), len(results))
        return results

    if prompt_template is None:
        prompt_template = load_prompt("pii_redact.txt")

    client = LLMClient()
    sem = asyncio.Semaphore(concurrency)
    done = 0
    lock = asyncio.Lock()

    async def one(s: Session) -> PIIResult:
        nonlocal done
        async with sem:
            out = await _redact_one(client, s, prompt_template, model)
        async with lock:
            done += 1
            if progress_cb:
                progress_cb(done, len(sessions))
        return out

    return await asyncio.gather(*(one(s) for s in sessions))


def save_pii_results(results: list[PIIResult], path: Path, log_path: Path) -> None:
    with path.open("w") as f:
        for r in results:
            row = {
                "uuid": r.session_id,
                "name": r.name,
                "created_at": r.created_at,
                "models_used": r.models_used,
                "messages": [
                    {"sender": m.sender, "text": m.text, "created_at": m.created_at,
                     "tool_calls": m.tool_calls}
                    for m in r.messages
                ],
                "pii_verified": r.verified,
                "pii_failed_reason": r.failed_reason,
                "pii_skipped": r.skipped,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    log = [{
        "session_id": r.session_id,
        "name": r.name,
        "skipped": r.skipped,
        "verified": r.verified,
        "failed_reason": r.failed_reason,
        "input_tokens": r.input_tokens,
        "output_tokens": r.output_tokens,
        "cost_usd": round(r.cost_usd, 6),
        "duration_s": r.duration_s,
        "model": r.model,
    } for r in results]
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2))


def load_redacted_sessions(path: Path) -> Optional[list[dict[str, Any]]]:
    if not path.exists():
        return None
    out: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def load_pii_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text())


def sessions_from_redacted(rows: list[dict[str, Any]]) -> list[Session]:
    """Rehydrate Session objects from a redacted_sessions.jsonl row list."""
    sessions: list[Session] = []
    for row in rows:
        messages = [
            Message(
                sender=m.get("sender", "unknown"),
                text=m.get("text", ""),
                created_at=m.get("created_at"),
                tool_calls=m.get("tool_calls") or [],
            )
            for m in row.get("messages", [])
        ]
        sessions.append(Session(
            uuid=row.get("uuid", ""),
            name=row.get("name", ""),
            created_at=row.get("created_at", ""),
            messages=messages,
            models_used=row.get("models_used") or [],
        ))
    return sessions
