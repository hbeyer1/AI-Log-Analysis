"""Stage 2 — Prompt 1: conversation-level features + objective segmentation.

One LLM call per conversation. Input is the (optionally redacted) transcript
with indexed, role-labelled turns. Output is a JSON object:

    {
      "conversation_features": { work_related, num_objectives, num_turns, ... },
      "objectives": [ { objective_id, description, turn_indices }, ... ]
    }

The file-level output, conv_features.jsonl, has one row per conversation with
conversation_id, conversation_features, and objectives. Prompt 2 later slices
each objective's sub-transcript using turn_indices.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from data import Session, Message
from llm_client import LLMClient, DEFAULT_MODEL_HEAVY


PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text()


# ------------------------------ dataclasses ------------------------------ #


@dataclass
class Objective:
    objective_id: int
    description: str
    turn_indices: list[int] = field(default_factory=list)


@dataclass
class Prompt1Result:
    session_id: str
    name: str
    created_at: str
    models_used: list[str]
    conversation_features: dict[str, Any] = field(default_factory=dict)
    objectives: list[Objective] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_s: float = 0.0
    model: str = ""
    error: Optional[str] = None


# --------------------------- transcript formatting ---------------------- #


def _role_of(m: Message) -> str:
    if m.sender == "human":
        return "user"
    if m.sender == "assistant":
        return "assistant"
    return m.sender or "system"


def format_transcript(session: Session) -> str:
    lines: list[str] = []
    for i, m in enumerate(session.messages):
        role = _role_of(m)
        header = f"[Turn {i} | {role}" + (f" | {m.created_at}" if m.created_at else "") + "]"
        lines.append(header)
        lines.append(m.text.rstrip())
        lines.append("")
    return "\n".join(lines).strip()


# ------------------------------- parsing -------------------------------- #


def _strip_fences(text: str) -> str:
    s = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```\s*$", s, re.DOTALL)
    return m.group(1).strip() if m else s


def _extract_json_object(text: str) -> Optional[dict[str, Any]]:
    cleaned = _strip_fences(text)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_objectives(raw: Any, n_turns: int) -> list[Objective]:
    if not isinstance(raw, list):
        return []
    out: list[Objective] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            oid = int(item.get("objective_id") or (len(out) + 1))
        except (TypeError, ValueError):
            oid = len(out) + 1
        desc = str(item.get("description", "")).strip()
        raw_idx = item.get("turn_indices") or []
        indices: list[int] = []
        if isinstance(raw_idx, list):
            for v in raw_idx:
                try:
                    i = int(v)
                except (TypeError, ValueError):
                    continue
                if 0 <= i < n_turns:
                    indices.append(i)
        if not desc or not indices:
            continue
        out.append(Objective(objective_id=oid, description=desc, turn_indices=sorted(set(indices))))
    return out


# --------------------------------- runner ------------------------------- #


async def _run_one(
    client: LLMClient,
    session: Session,
    prompt_template: str,
    model: str,
) -> Prompt1Result:
    started = time.monotonic()
    res = Prompt1Result(
        session_id=session.uuid,
        name=session.name,
        created_at=session.created_at,
        models_used=list(session.models_used),
        model=model,
    )
    try:
        transcript = format_transcript(session)
        prompt = (prompt_template
                  .replace("{conversation_id}", session.uuid)
                  .replace("{transcript}", transcript))
        result = await client.complete(prompt, model=model, max_tokens=8192, temperature=0.0)
        res.input_tokens += result.input_tokens
        res.output_tokens += result.output_tokens
        res.cost_usd += result.cost_usd

        obj = _extract_json_object(result.text)
        if obj is None:
            strict = prompt + "\n\nYour previous response was not valid JSON. Return ONLY the JSON object, no prose."
            retry = await client.complete(strict, model=model, max_tokens=8192, temperature=0.0)
            res.input_tokens += retry.input_tokens
            res.output_tokens += retry.output_tokens
            res.cost_usd += retry.cost_usd
            obj = _extract_json_object(retry.text) or {}

        features = obj.get("conversation_features")
        if isinstance(features, dict):
            res.conversation_features = features
        res.objectives = _parse_objectives(obj.get("objectives"), len(session.messages))
    except Exception as e:
        res.error = str(e)

    res.duration_s = round(time.monotonic() - started, 2)
    return res


async def run_prompt1(
    sessions: list[Session],
    *,
    prompt_template: Optional[str] = None,
    model: str = DEFAULT_MODEL_HEAVY,
    concurrency: int = 5,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> list[Prompt1Result]:
    if prompt_template is None:
        prompt_template = load_prompt("prompt1.txt")

    client = LLMClient()
    sem = asyncio.Semaphore(concurrency)
    done = 0
    lock = asyncio.Lock()

    async def one(s: Session) -> Prompt1Result:
        nonlocal done
        async with sem:
            out = await _run_one(client, s, prompt_template, model)
        async with lock:
            done += 1
            if progress_cb:
                progress_cb(done, len(sessions))
        return out

    return await asyncio.gather(*(one(s) for s in sessions))


# ----------------------------- persistence ------------------------------ #


def save_prompt1_results(results: list[Prompt1Result], features_path: Path, log_path: Path) -> None:
    with features_path.open("w") as f:
        for r in results:
            row = {
                "conversation_id": r.session_id,
                "name": r.name,
                "created_at": r.created_at,
                "models_used": r.models_used,
                "conversation_features": r.conversation_features,
                "objectives": [
                    {"objective_id": o.objective_id, "description": o.description,
                     "turn_indices": o.turn_indices}
                    for o in r.objectives
                ],
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    log = [{
        "session_id": r.session_id,
        "name": r.name,
        "n_objectives": len(r.objectives),
        "input_tokens": r.input_tokens,
        "output_tokens": r.output_tokens,
        "cost_usd": round(r.cost_usd, 6),
        "duration_s": r.duration_s,
        "model": r.model,
        "error": r.error,
    } for r in results]
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2))


def load_features(features_path: Path) -> Optional[list[dict[str, Any]]]:
    if not features_path.exists():
        return None
    out: list[dict[str, Any]] = []
    for line in features_path.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def load_prompt1_log(log_path: Path) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []
    return json.loads(log_path.read_text())
