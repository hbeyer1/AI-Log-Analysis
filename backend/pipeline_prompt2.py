"""Stage 3 — Prompt 2: per-objective structured interview. One LLM call per
objective on the sliced sub-transcript. Writes objectives.jsonl."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from data import Session, Message, role_of
from json_utils import extract_json_object
from llm_client import LLMClient, DEFAULT_MODEL_HEAVY
from prompt_utils import load_prompt


INTERVIEW_FIELDS = (
    "underlying_intent",
    "domain",
    "topic",
    "deliverable",
    "workflow_and_resolution",
    "user_approach",
    "user_signals",
    "language_and_tone",
    "additional_notes",
)


@dataclass
class ObjectiveReport:
    conversation_id: str
    objective_id: int
    description: str
    fields: dict[str, Any] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_s: float = 0.0
    model: str = ""
    error: Optional[str] = None


def format_sub_transcript(session: Session, turn_indices: list[int]) -> str:
    ordered = sorted(set(i for i in turn_indices if 0 <= i < len(session.messages)))
    lines: list[str] = []
    for i in ordered:
        m = session.messages[i]
        role = role_of(m)
        header = f"[Turn {i} | {role}" + (f" | {m.created_at}" if m.created_at else "") + "]"
        lines.append(header)
        lines.append(m.text.rstrip())
        lines.append("")
    return "\n".join(lines).strip()


@dataclass
class _Task:
    session: Session
    objective_id: int
    description: str
    turn_indices: list[int]


def _collect_tasks(
    sessions_by_id: dict[str, Session],
    features: list[dict[str, Any]],
) -> list[_Task]:
    tasks: list[_Task] = []
    for row in features:
        sid = row.get("conversation_id", "")
        session = sessions_by_id.get(sid)
        if session is None:
            continue
        for obj in row.get("objectives") or []:
            if not isinstance(obj, dict):
                continue
            try:
                oid = int(obj.get("objective_id"))
            except (TypeError, ValueError):
                continue
            desc = str(obj.get("description", "")).strip()
            raw_idx = obj.get("turn_indices") or []
            indices = [int(i) for i in raw_idx if isinstance(i, int) or
                       (isinstance(i, str) and i.lstrip("-").isdigit())]
            if not desc or not indices:
                continue
            tasks.append(_Task(
                session=session, objective_id=oid, description=desc, turn_indices=indices,
            ))
    return tasks


async def _run_one(
    client: LLMClient,
    task: _Task,
    prompt_template: str,
    model: str,
) -> ObjectiveReport:
    started = time.monotonic()
    rep = ObjectiveReport(
        conversation_id=task.session.uuid,
        objective_id=task.objective_id,
        description=task.description,
        model=model,
    )
    try:
        transcript = format_sub_transcript(task.session, task.turn_indices)
        prompt = (prompt_template
                  .replace("{objective_description}", task.description)
                  .replace("{transcript}", transcript))
        result = await client.complete(prompt, model=model, max_tokens=4096, temperature=0.0)
        rep.input_tokens += result.input_tokens
        rep.output_tokens += result.output_tokens
        rep.cost_usd += result.cost_usd

        parsed = extract_json_object(result.text)
        if parsed is None:
            strict = prompt + "\n\nYour previous response was not valid JSON. Return ONLY the JSON object, no prose."
            retry = await client.complete(strict, model=model, max_tokens=4096, temperature=0.0)
            rep.input_tokens += retry.input_tokens
            rep.output_tokens += retry.output_tokens
            rep.cost_usd += retry.cost_usd
            parsed = extract_json_object(retry.text) or {}

        rep.fields = {k: parsed.get(k) for k in INTERVIEW_FIELDS}
    except Exception as e:
        rep.error = str(e)

    rep.duration_s = round(time.monotonic() - started, 2)
    return rep


async def run_prompt2(
    sessions: list[Session],
    features: list[dict[str, Any]],
    *,
    prompt_template: Optional[str] = None,
    model: str = DEFAULT_MODEL_HEAVY,
    concurrency: int = 5,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> list[ObjectiveReport]:
    if prompt_template is None:
        prompt_template = load_prompt("prompt2.txt")

    sessions_by_id = {s.uuid: s for s in sessions}
    tasks = _collect_tasks(sessions_by_id, features)

    client = LLMClient()
    sem = asyncio.Semaphore(concurrency)
    done = 0
    lock = asyncio.Lock()

    async def one(t: _Task) -> ObjectiveReport:
        nonlocal done
        async with sem:
            out = await _run_one(client, t, prompt_template, model)
        async with lock:
            done += 1
            if progress_cb:
                progress_cb(done, len(tasks))
        return out

    if not tasks:
        if progress_cb:
            progress_cb(0, 0)
        return []

    return await asyncio.gather(*(one(t) for t in tasks))


def save_prompt2_results(results: list[ObjectiveReport], objectives_path: Path,
                         log_path: Path) -> None:
    with objectives_path.open("w") as f:
        for r in results:
            row = {
                "conversation_id": r.conversation_id,
                "objective_id": r.objective_id,
                "description": r.description,
                **(r.fields or {}),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    log = [{
        "conversation_id": r.conversation_id,
        "objective_id": r.objective_id,
        "input_tokens": r.input_tokens,
        "output_tokens": r.output_tokens,
        "cost_usd": round(r.cost_usd, 6),
        "duration_s": r.duration_s,
        "model": r.model,
        "error": r.error,
    } for r in results]
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2))


def load_objective_reports(path: Path) -> Optional[list[dict[str, Any]]]:
    if not path.exists():
        return None
    out: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def load_prompt2_log(log_path: Path) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []
    return json.loads(log_path.read_text())
