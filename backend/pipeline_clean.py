"""Part 1b — Clean extracted objectives.

Loads raw_objectives.jsonl, applies exclusion patterns from the knowledge base
(one LLM judge call per batch), emits cleaned_objectives.jsonl plus
excluded_objectives.jsonl with the matching pattern + reason.

If the knowledge base has NO exclusion patterns yet (first run), only a
minimal heuristic is applied: objectives whose resolution_summary is empty
or whose objective description is trivially short are logged as excluded
with reason "heuristic:minimal_content". This matches the spec's first-run
behaviour in §3 ("minimal filtering — only obviously non-substantive items").
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from llm_client import AnthropicClient, DEFAULT_MODEL_HEAVY, estimate_cost, rough_token_count
from pipeline_extract import _extract_json_array, load_prompt


BATCH_SIZE = 30  # objectives per judge call


# --------------------------- knowledge base ----------------------------- #


def default_knowledge_base() -> dict[str, Any]:
    return {
        "exclusion_patterns": [],
        "category_seeds": [],
        "merge_rules": [],
        "boundary_clarifications": [],
    }


def load_knowledge_base(path: Path) -> dict[str, Any]:
    if not path.exists():
        return default_knowledge_base()
    try:
        kb = json.loads(path.read_text())
    except json.JSONDecodeError:
        return default_knowledge_base()
    merged = default_knowledge_base()
    merged.update(kb or {})
    # Ensure list-shaped keys are lists
    for k in ("exclusion_patterns", "category_seeds", "merge_rules", "boundary_clarifications"):
        if not isinstance(merged.get(k), list):
            merged[k] = []
    return merged


def save_knowledge_base(path: Path, kb: dict[str, Any]) -> None:
    path.write_text(json.dumps(kb, ensure_ascii=False, indent=2))


# ---------------------------- heuristic gate --------------------------- #


def _minimal_heuristic_exclude(objective: dict[str, Any]) -> Optional[str]:
    """Return a short reason if the objective is obviously non-substantive."""
    desc = str(objective.get("objective", "")).strip()
    res = str(objective.get("resolution_summary", "")).strip()
    if not desc or len(desc) < 5:
        return "objective description too short"
    if not res or len(res.split()) < 8:
        return "resolution summary empty or trivial"
    return None


# --------------------------- prompt formatting ------------------------- #


def _patterns_block(patterns: list[dict[str, Any]]) -> str:
    if not patterns:
        return "(no patterns defined — do not exclude anything based on patterns)"
    lines: list[str] = []
    for i, p in enumerate(patterns, 1):
        pid = str(p.get("id") or f"p{i}")
        desc = str(p.get("pattern") or "").strip()
        reason = str(p.get("reason") or "").strip()
        lines.append(f"[{pid}] {desc}" + (f" — {reason}" if reason else ""))
    return "\n".join(lines)


def _objectives_block(objectives: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for i, o in enumerate(objectives, 1):
        desc = str(o.get("objective", "")).strip()
        res = str(o.get("resolution_summary", "")).strip()
        lines.append(f"{i}. objective: {desc}")
        lines.append(f"   resolution_summary: {res}")
    return "\n".join(lines)


# ------------------------------ judge call ----------------------------- #


async def _judge_batch(
    client: AnthropicClient,
    prompt_template: str,
    patterns: list[dict[str, Any]],
    objectives: list[dict[str, Any]],
    model: str,
) -> tuple[list[dict[str, Any]], int, int, float]:
    rendered = (prompt_template
                .replace("{patterns_block}", _patterns_block(patterns))
                .replace("{objectives_block}", _objectives_block(objectives)))
    result = await client.complete(rendered, model=model, max_tokens=4096, temperature=0.0)
    arr = _extract_json_array(result.text)
    if arr is None:
        # Fall back to "keep everything" rather than dropping on parse failure.
        arr = [{"index": i + 1, "decision": "keep", "matched_pattern": "",
                "reason": "judge response unparseable; defaulted to keep"}
               for i in range(len(objectives))]
    return arr, result.input_tokens, result.output_tokens, result.cost_usd


# --------------------------------- runner ----------------------------- #


@dataclass
class CleanResult:
    kept: list[dict[str, Any]] = field(default_factory=list)
    excluded: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_s: float = 0.0
    model: str = ""
    n_patterns: int = 0
    n_total: int = 0


async def run_clean(
    objectives: list[dict[str, Any]],
    *,
    knowledge_base: dict[str, Any],
    prompt_template: Optional[str] = None,
    model: str = DEFAULT_MODEL_HEAVY,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> CleanResult:
    started = time.monotonic()
    res = CleanResult(model=model, n_total=len(objectives))
    patterns = list(knowledge_base.get("exclusion_patterns") or [])
    res.n_patterns = len(patterns)

    # Step 1: minimal heuristic gate (always applied).
    gated: list[dict[str, Any]] = []
    for o in objectives:
        reason = _minimal_heuristic_exclude(o)
        if reason:
            res.excluded.append({
                "objective": o,
                "matched_pattern": "",
                "reason": f"heuristic: {reason}",
                "stage": "heuristic",
            })
        else:
            gated.append(o)

    # Step 2: LLM judge against KB patterns (only if patterns exist).
    if not patterns or not gated:
        res.kept = gated
        res.duration_s = round(time.monotonic() - started, 2)
        if progress_cb:
            progress_cb(len(objectives), len(objectives))
        return res

    if prompt_template is None:
        prompt_template = load_prompt("clean_judge.txt")

    client = AnthropicClient()
    processed = len(res.excluded)  # heuristically excluded count toward progress
    if progress_cb:
        progress_cb(processed, len(objectives))

    for start in range(0, len(gated), BATCH_SIZE):
        batch = gated[start:start + BATCH_SIZE]
        decisions, ti, to, cost = await _judge_batch(
            client, prompt_template, patterns, batch, model,
        )
        res.input_tokens += ti
        res.output_tokens += to
        res.cost_usd += cost

        # Index decisions by `index` field; fall back to positional.
        by_index: dict[int, dict[str, Any]] = {}
        for d in decisions:
            try:
                by_index[int(d.get("index", 0))] = d
            except (TypeError, ValueError):
                continue

        for i, obj in enumerate(batch, 1):
            d = by_index.get(i) or {"decision": "keep", "matched_pattern": "",
                                     "reason": "missing decision; defaulted to keep"}
            if str(d.get("decision", "")).strip().lower() == "exclude":
                res.excluded.append({
                    "objective": obj,
                    "matched_pattern": str(d.get("matched_pattern", "") or ""),
                    "reason": str(d.get("reason", "") or "").strip() or "no reason given",
                    "stage": "llm_judge",
                })
            else:
                res.kept.append(obj)

        processed += len(batch)
        if progress_cb:
            progress_cb(processed, len(objectives))

    res.duration_s = round(time.monotonic() - started, 2)
    return res


# --------------------------- persistence -------------------------------- #


def save_clean_results(res: CleanResult, cleaned_path: Path, excluded_path: Path,
                       log_path: Path) -> None:
    with cleaned_path.open("w") as f:
        for o in res.kept:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")

    with excluded_path.open("w") as f:
        for e in res.excluded:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    log_path.write_text(json.dumps({
        "n_total": res.n_total,
        "n_kept": len(res.kept),
        "n_excluded": len(res.excluded),
        "n_patterns": res.n_patterns,
        "input_tokens": res.input_tokens,
        "output_tokens": res.output_tokens,
        "cost_usd": round(res.cost_usd, 6),
        "duration_s": res.duration_s,
        "model": res.model,
    }, indent=2))


def load_cleaned(path: Path) -> Optional[list[dict[str, Any]]]:
    if not path.exists():
        return None
    out: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def load_excluded(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def load_clean_log(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    return json.loads(path.read_text())


# --------------------------- cost estimate ------------------------------ #


def estimate_clean_cost(
    objectives: list[dict[str, Any]],
    knowledge_base: dict[str, Any],
    prompt_template: str,
    model: str = DEFAULT_MODEL_HEAVY,
) -> dict[str, Any]:
    patterns = list(knowledge_base.get("exclusion_patterns") or [])
    if not patterns:
        return {
            "model": model,
            "n_objectives": len(objectives),
            "n_patterns": 0,
            "n_batches": 0,
            "estimated_input_tokens": 0,
            "estimated_output_tokens": 0,
            "estimated_cost_usd": 0.0,
            "note": "no patterns — only minimal heuristic runs (free)",
        }
    n_batches = (len(objectives) + BATCH_SIZE - 1) // BATCH_SIZE
    total_in = 0
    for start in range(0, len(objectives), BATCH_SIZE):
        batch = objectives[start:start + BATCH_SIZE]
        rendered = (prompt_template
                    .replace("{patterns_block}", _patterns_block(patterns))
                    .replace("{objectives_block}", _objectives_block(batch)))
        total_in += rough_token_count(rendered)
    total_out = n_batches * 60 * BATCH_SIZE // 10
    return {
        "model": model,
        "n_objectives": len(objectives),
        "n_patterns": len(patterns),
        "n_batches": n_batches,
        "estimated_input_tokens": total_in,
        "estimated_output_tokens": total_out,
        "estimated_cost_usd": round(estimate_cost(model, total_in, total_out), 4),
    }
