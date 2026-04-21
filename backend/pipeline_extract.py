"""Part 1a — Extract objectives from conversation transcripts.

Knowledge-free: the LLM sees only the raw transcript. One LLM call per
conversation (or per chunk, if the session is too long to fit in one call).
Output conforms to §3 of the spec: objective + resolution_summary + source_quote
+ turn_indices. Raw output is preserved as raw_objectives.jsonl.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Callable, Optional

from data import Session, Message
from llm_client import (
    AnthropicClient,
    DEFAULT_MODEL_HEAVY,
    estimate_cost,
    rough_token_count,
)


PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text()


def save_prompt(name: str, content: str) -> None:
    (PROMPTS_DIR / name).write_text(content)


# Rough char budget per chunk (~40k tokens of transcript).
CHUNK_CHAR_BUDGET = 160_000
# How much of the model window we allow before chunking.
SINGLE_CALL_CHAR_LIMIT = 600_000
# Overlap turns carried from chunk N into chunk N+1.
OVERLAP_TURNS = 6


# ------------------------------- dataclasses ------------------------------ #


@dataclass
class Objective:
    id: str = ""
    session_id: str = ""
    objective: str = ""
    resolution_summary: str = ""
    source_quote: str = ""
    timestamp: str = ""
    timestamp_range: list[str] = field(default_factory=list)
    turn_indices: list[int] = field(default_factory=list)


@dataclass
class RejectedObjective:
    session_id: str
    raw: dict[str, Any]
    reason: str


@dataclass
class SessionExtractResult:
    session_id: str
    name: str
    created_at: str
    objectives: list[Objective] = field(default_factory=list)
    rejected: list[RejectedObjective] = field(default_factory=list)
    n_chunks: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""
    duration_s: float = 0.0
    error: Optional[str] = None


# ---------------------------- chunk formatting --------------------------- #


def _session_total_chars(session: Session) -> int:
    return sum(len(m.text) for m in session.messages)


def _role_of(m: Message) -> str:
    return "user" if m.sender == "human" else ("assistant" if m.sender == "assistant" else "system")


def _format_chunk(session: Session, turn_range: tuple[int, int]) -> str:
    lo, hi = turn_range
    ts = session.created_at or ""
    lines: list[str] = []
    for i in range(lo, hi):
        m = session.messages[i]
        role = _role_of(m)
        if role == "system":
            continue
        header = f"[Turn {i} | {role}" + (f" | {ts}" if ts else "") + "]"
        lines.append(header)
        lines.append(m.text.rstrip())
        lines.append("")
    return "\n".join(lines).strip()


def _user_turn_indices_in_chunk(session: Session, turn_range: tuple[int, int]) -> list[int]:
    lo, hi = turn_range
    return [i for i in range(lo, hi) if _role_of(session.messages[i]) == "user"]


def _segment(session: Session) -> list[tuple[int, int]]:
    """One chunk for short sessions; overlapped windows otherwise."""
    n = len(session.messages)
    if n == 0:
        return []
    if _session_total_chars(session) <= SINGLE_CALL_CHAR_LIMIT:
        return [(0, n)]

    chunks: list[tuple[int, int]] = []
    lo = 0
    while lo < n:
        chars = 0
        hi = lo
        while hi < n and chars + len(session.messages[hi].text) <= CHUNK_CHAR_BUDGET:
            chars += len(session.messages[hi].text)
            hi += 1
        if hi == lo:
            hi = lo + 1
        chunks.append((lo, hi))
        if hi >= n:
            break
        lo = max(hi - OVERLAP_TURNS, lo + 1)
    return chunks


# ---------------------------- existing-objective block ------------------- #


def _is_in_progress(obj: Objective, chunk_user_turns: list[int]) -> bool:
    if not obj.turn_indices or not chunk_user_turns:
        return False
    return max(obj.turn_indices) == chunk_user_turns[-1]


def _format_existing(objectives: list[Objective], last_chunk_user_turns: list[int]) -> str:
    if not objectives:
        return "(none — this is the first chunk of this session)"
    lines: list[str] = []
    for idx, o in enumerate(objectives, 1):
        status = "in progress" if _is_in_progress(o, last_chunk_user_turns) else "complete"
        lines.append(f"[existing objective {idx}]")
        lines.append(f"- objective: {o.objective}")
        lines.append(f"- resolution_summary: {o.resolution_summary}")
        lines.append(f"- status: {status}")
        lines.append("")
    return "\n".join(lines).strip()


# ------------------------------- parsing --------------------------------- #


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


# ------------------------------ validation ------------------------------- #


REQUIRED_FIELDS = ("objective", "resolution_summary", "source_quote", "timestamp")


def _normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def _quote_found_in_user_turns(quote: str, session: Session, chunk_user_turns: list[int]) -> bool:
    needle = _normalize_text(quote)
    if len(needle) < 4:
        return False
    for i in chunk_user_turns:
        hay = _normalize_text(session.messages[i].text)
        if needle in hay:
            return True
    return False


def _validate(raw: dict[str, Any], session: Session, chunk_turn_range: tuple[int, int]
              ) -> tuple[Optional[Objective], Optional[str]]:
    lo, hi = chunk_turn_range
    chunk_user_turns = _user_turn_indices_in_chunk(session, chunk_turn_range)

    for f in REQUIRED_FIELDS:
        if not raw.get(f) or not str(raw.get(f)).strip():
            return None, f"missing_or_empty:{f}"

    objective = str(raw["objective"]).strip()
    if len(objective) > 300:
        return None, "objective_too_long"

    resolution = str(raw["resolution_summary"]).strip()
    wc = len(resolution.split())
    if wc < 20 or wc > 200:
        return None, f"resolution_summary_word_count:{wc}"

    source_quote = str(raw["source_quote"]).strip()
    if not _quote_found_in_user_turns(source_quote, session, chunk_user_turns):
        return None, "quote_not_in_user_turns"

    turn_indices_raw = raw.get("turn_indices") or []
    if not isinstance(turn_indices_raw, list) or not turn_indices_raw:
        return None, "turn_indices_missing"
    turn_indices: list[int] = []
    for ti in turn_indices_raw:
        try:
            v = int(ti)
        except (TypeError, ValueError):
            return None, f"turn_index_not_int:{ti}"
        if v < lo or v >= hi:
            return None, f"turn_index_out_of_range:{v}"
        if _role_of(session.messages[v]) != "user":
            return None, f"turn_index_not_user:{v}"
        turn_indices.append(v)

    tr = raw.get("timestamp_range") or []
    if not isinstance(tr, list) or len(tr) != 2:
        tr = [str(raw["timestamp"]).strip(), str(raw["timestamp"]).strip()]
    timestamp_range = [str(x).strip() for x in tr]

    obj = Objective(
        session_id=session.uuid,
        objective=objective,
        resolution_summary=resolution,
        source_quote=source_quote,
        timestamp=str(raw["timestamp"]).strip(),
        timestamp_range=timestamp_range,
        turn_indices=sorted(set(turn_indices)),
    )
    return obj, None


def _assign_id(obj: Objective) -> None:
    key = f"{obj.session_id}|{obj.timestamp}|{obj.objective}".encode("utf-8")
    obj.id = hashlib.sha1(key).hexdigest()[:16]


# ------------------------------- dedup across chunks --------------------- #


def _objective_key(s: str) -> str:
    return re.sub(r"[^\w\s]", "", s.lower()).strip()


def _merge_across_chunks(objectives: list[Objective]) -> list[Objective]:
    kept: list[Objective] = []
    for o in objectives:
        merged = False
        for k in kept:
            turn_overlap = bool(set(o.turn_indices) & set(k.turn_indices))
            sim = _objective_key(o.objective) == _objective_key(k.objective)
            if turn_overlap and sim:
                if len(o.turn_indices) > len(k.turn_indices):
                    k.turn_indices = sorted(set(o.turn_indices) | set(k.turn_indices))
                    k.objective = o.objective
                    k.resolution_summary = o.resolution_summary
                else:
                    k.turn_indices = sorted(set(o.turn_indices) | set(k.turn_indices))
                merged = True
                break
        if not merged:
            kept.append(o)
    return kept


# ------------------------------ cost estimate ---------------------------- #


def estimate_extract_cost(
    sessions: list[Session],
    prompt_template: str,
    model: str = DEFAULT_MODEL_HEAVY,
    avg_output_tokens_per_session: int = 1400,
) -> dict[str, Any]:
    total_in = 0
    total_chunks = 0
    for s in sessions:
        chunks = _segment(s)
        total_chunks += len(chunks)
        for (lo, hi) in chunks:
            transcript = _format_chunk(s, (lo, hi))
            rendered = (prompt_template
                        .replace("{session_id}", s.uuid)
                        .replace("{transcript}", transcript)
                        .replace("{existing_objectives}", "(none)"))
            total_in += rough_token_count(rendered)
    total_out = avg_output_tokens_per_session * len(sessions)
    return {
        "model": model,
        "sessions": len(sessions),
        "chunks": total_chunks,
        "estimated_input_tokens": total_in,
        "estimated_output_tokens": total_out,
        "estimated_cost_usd": round(estimate_cost(model, total_in, total_out), 4),
    }


# -------------------------------- runner --------------------------------- #


async def _extract_one_chunk(
    client: AnthropicClient,
    session: Session,
    chunk_turn_range: tuple[int, int],
    existing: list[Objective],
    prompt_template: str,
    model: str,
) -> tuple[list[Objective], list[RejectedObjective], int, int, float]:
    transcript = _format_chunk(session, chunk_turn_range)
    last_chunk_user_turns = _user_turn_indices_in_chunk(session, chunk_turn_range)
    existing_block = _format_existing(existing, last_chunk_user_turns)

    prompt = (prompt_template
              .replace("{session_id}", session.uuid)
              .replace("{transcript}", transcript)
              .replace("{existing_objectives}", existing_block))

    result = await client.complete(prompt, model=model, max_tokens=8192)
    arr = _extract_json_array(result.text)

    if arr is None:
        strict = prompt + "\n\nYour previous response was not valid JSON. Return ONLY the JSON array, no prose."
        result2 = await client.complete(strict, model=model, max_tokens=8192)
        arr = _extract_json_array(result2.text) or []
        in_tok = result.input_tokens + result2.input_tokens
        out_tok = result.output_tokens + result2.output_tokens
        cost = result.cost_usd + result2.cost_usd
    else:
        in_tok = result.input_tokens
        out_tok = result.output_tokens
        cost = result.cost_usd

    accepted: list[Objective] = []
    rejected: list[RejectedObjective] = []
    for raw in arr:
        if not isinstance(raw, dict):
            rejected.append(RejectedObjective(
                session_id=session.uuid, raw={"value": str(raw)[:500]}, reason="not_an_object",
            ))
            continue
        obj, reason = _validate(raw, session, chunk_turn_range)
        if obj is None:
            rejected.append(RejectedObjective(session_id=session.uuid, raw=raw, reason=reason or "unknown"))
        else:
            accepted.append(obj)
    return accepted, rejected, in_tok, out_tok, cost


async def _extract_session(
    client: AnthropicClient,
    session: Session,
    prompt_template: str,
    model: str,
) -> SessionExtractResult:
    started = time.monotonic()
    res = SessionExtractResult(
        session_id=session.uuid,
        name=session.name,
        created_at=session.created_at,
        model=model,
    )
    try:
        chunks = _segment(session)
        res.n_chunks = len(chunks)
        for chunk_range in chunks:
            accepted, rejected, ti, to, cost = await _extract_one_chunk(
                client, session, chunk_range, res.objectives, prompt_template, model,
            )
            res.objectives.extend(accepted)
            res.rejected.extend(rejected)
            res.input_tokens += ti
            res.output_tokens += to
            res.cost_usd += cost
        res.objectives = _merge_across_chunks(res.objectives)
        for o in res.objectives:
            _assign_id(o)
    except Exception as e:
        res.error = str(e)
    res.duration_s = round(time.monotonic() - started, 2)
    return res


async def run_extract(
    sessions: list[Session],
    *,
    prompt_template: Optional[str] = None,
    model: str = DEFAULT_MODEL_HEAVY,
    concurrency: int = 5,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> list[SessionExtractResult]:
    if prompt_template is None:
        prompt_template = load_prompt("extract.txt")

    client = AnthropicClient()
    sem = asyncio.Semaphore(concurrency)
    done = 0
    lock = asyncio.Lock()

    async def one(s: Session) -> SessionExtractResult:
        nonlocal done
        async with sem:
            out = await _extract_session(client, s, prompt_template, model)
        async with lock:
            done += 1
            if progress_cb:
                progress_cb(done, len(sessions))
        return out

    return await asyncio.gather(*(one(s) for s in sessions))


# -------------------------- persistence helpers -------------------------- #


def save_extract_results(results: list[SessionExtractResult], raw_path: Path,
                         rejected_path: Path, log_path: Path) -> None:
    with raw_path.open("w") as f:
        for r in results:
            for o in r.objectives:
                f.write(json.dumps(asdict(o), ensure_ascii=False) + "\n")

    with rejected_path.open("w") as f:
        for r in results:
            for rej in r.rejected:
                f.write(json.dumps({
                    "session_id": rej.session_id,
                    "reason": rej.reason,
                    "raw": rej.raw,
                }, ensure_ascii=False) + "\n")

    log = [{
        "session_id": r.session_id,
        "name": r.name,
        "created_at": r.created_at,
        "n_chunks": r.n_chunks,
        "n_objectives": len(r.objectives),
        "n_rejected": len(r.rejected),
        "input_tokens": r.input_tokens,
        "output_tokens": r.output_tokens,
        "cost_usd": round(r.cost_usd, 6),
        "duration_s": r.duration_s,
        "model": r.model,
        "error": r.error,
    } for r in results]
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2))


def load_objectives(path: Path) -> Optional[list[dict[str, Any]]]:
    if not path.exists():
        return None
    out: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def load_rejected(rejected_path: Path) -> list[dict[str, Any]]:
    if not rejected_path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in rejected_path.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def load_log(log_path: Path) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []
    return json.loads(log_path.read_text())
