"""FastAPI server for the 3-stage conversation extraction pipeline:
PII redaction → Prompt 1 (conversation features + objective segmentation)
→ Prompt 2 (per-objective structured interview).
"""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import io
import json
import os
import uuid as uuid_lib
import zipfile
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, Response
from pydantic import BaseModel

load_dotenv()

from data import parse_conversations, filter_substantive, Session
import pipeline_pii as pii
import pipeline_prompt1 as p1
import pipeline_prompt2 as p2
from llm_client import DEFAULT_MODEL_HEAVY, DEFAULT_MODEL_LIGHT


BASE = Path(__file__).parent
UPLOADS = BASE / "uploads"
RESULTS = BASE / "results"
PROMPTS_DIR = BASE / "prompts"
UPLOADS.mkdir(exist_ok=True)
RESULTS.mkdir(exist_ok=True)

REDACTED_PATH = RESULTS / "redacted_sessions.jsonl"
PII_LOG_PATH = RESULTS / "pii_log.json"
FEATURES_PATH = RESULTS / "conv_features.jsonl"
PROMPT1_LOG_PATH = RESULTS / "prompt1_log.json"
OBJECTIVES_PATH = RESULTS / "objectives.jsonl"
PROMPT2_LOG_PATH = RESULTS / "prompt2_log.json"

CONVERSATIONS_OUT = RESULTS / "conversations.json"
OBJECTIVES_OUT = RESULTS / "objectives.json"


app = FastAPI(title="AI Log Analysis — Stanford 3-stage pipeline")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATE: dict[str, Any] = {
    "dataset_id": None,
    "sessions": [],
    "substantive": [],
    "jobs": {},
}


# --------------------------------- status ------------------------------ #


@app.get("/api/status")
def status() -> dict[str, Any]:
    return {
        "anthropic_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "openai_configured": bool(os.environ.get("OPENAI_API_KEY")),
        "dataset_loaded": STATE["dataset_id"] is not None,
        "session_count": len(STATE["sessions"]),
        "substantive_count": len(STATE["substantive"]),
        "has_redacted": REDACTED_PATH.exists(),
        "has_features": FEATURES_PATH.exists(),
        "has_objectives": OBJECTIVES_PATH.exists(),
    }


# ------------------------------ upload / sessions ---------------------- #


@app.post("/api/upload")
async def upload_conversations(file: UploadFile = File(...)) -> dict[str, Any]:
    try:
        content = await file.read()
        raw = json.loads(content)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"Invalid JSON: {e}")

    if not isinstance(raw, list):
        raise HTTPException(400, "Expected a JSON array of conversations")

    sessions = parse_conversations(raw)
    substantive = filter_substantive(sessions)
    dataset_id = uuid_lib.uuid4().hex[:8]
    (UPLOADS / f"{dataset_id}.json").write_bytes(content)

    STATE["dataset_id"] = dataset_id
    STATE["sessions"] = sessions
    STATE["substantive"] = substantive

    return {
        "dataset_id": dataset_id,
        "total_sessions": len(sessions),
        "substantive_count": len(substantive),
        "preview": [_session_row(s) for s in substantive[:10]],
    }


def _session_row(s: Session) -> dict[str, Any]:
    return {
        "uuid": s.uuid,
        "name": s.name,
        "created_at": s.created_at,
        "message_count": len(s.messages),
        "total_chars": s.total_chars,
    }


@app.get("/api/sessions")
def list_sessions(limit: int = 200) -> dict[str, Any]:
    return {
        "substantive_count": len(STATE["substantive"]),
        "sessions": [_session_row(s) for s in STATE["substantive"][:limit]],
    }


# ------------------------------- prompts ------------------------------- #


class PromptUpdate(BaseModel):
    content: str


@app.get("/api/prompts")
def list_prompts() -> dict[str, Any]:
    files = sorted(PROMPTS_DIR.glob("*.txt"))
    return {"prompts": [{"name": f.stem, "size": f.stat().st_size} for f in files]}


@app.get("/api/prompts/{name}")
def get_prompt(name: str) -> dict[str, str]:
    path = PROMPTS_DIR / f"{name}.txt"
    if not path.exists():
        raise HTTPException(404, f"Prompt not found: {name}")
    return {"name": name, "content": path.read_text()}


@app.put("/api/prompts/{name}")
def put_prompt(name: str, body: PromptUpdate) -> dict[str, str]:
    (PROMPTS_DIR / f"{name}.txt").write_text(body.content)
    return {"name": name, "status": "saved"}


# --------------------------------- jobs -------------------------------- #


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    job = STATE["jobs"].get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


def _new_job(total: int) -> str:
    job_id = uuid_lib.uuid4().hex[:8]
    STATE["jobs"][job_id] = {"status": "running", "done": 0, "total": total}
    return job_id


def _progress(job_id: str):
    def cb(done: int, total: int) -> None:
        STATE["jobs"][job_id]["done"] = done
        STATE["jobs"][job_id]["total"] = total
    return cb


def _selected_sessions(limit: int | None) -> list[Session]:
    sessions = STATE["substantive"]
    if limit:
        sessions = sessions[:limit]
    return sessions


# ---------------------------- Stage 1 — PII ---------------------------- #


class PIIRunRequest(BaseModel):
    limit: int | None = None
    enabled: bool = True
    model: str = DEFAULT_MODEL_LIGHT
    concurrency: int = 5


@app.post("/api/pii/run")
async def pii_run(body: PIIRunRequest) -> dict[str, Any]:
    sessions = _selected_sessions(body.limit)
    if not sessions:
        raise HTTPException(400, "No dataset loaded")

    job_id = _new_job(len(sessions))

    async def run() -> None:
        try:
            prompt = pii.load_prompt("pii_redact.txt") if body.enabled else None
            results = await pii.run_pii(
                sessions, enabled=body.enabled,
                prompt_template=prompt, model=body.model,
                concurrency=body.concurrency, progress_cb=_progress(job_id),
            )
            pii.save_pii_results(results, REDACTED_PATH, PII_LOG_PATH)
            STATE["jobs"][job_id]["status"] = "done"
        except Exception as e:
            STATE["jobs"][job_id]["status"] = "error"
            STATE["jobs"][job_id]["error"] = str(e)

    asyncio.create_task(run())
    return {"job_id": job_id, "total": len(sessions)}


@app.get("/api/pii/result")
def pii_result() -> JSONResponse:
    rows = pii.load_redacted_sessions(REDACTED_PATH)
    if rows is None:
        raise HTTPException(404, "No PII stage output yet.")
    log = pii.load_pii_log(PII_LOG_PATH)
    n_verified = sum(1 for r in rows if r.get("pii_verified"))
    n_failed = sum(1 for r in rows if r.get("pii_failed_reason"))
    n_skipped = sum(1 for r in rows if r.get("pii_skipped"))
    preview = [{
        "uuid": r["uuid"], "name": r["name"],
        "verified": r.get("pii_verified"), "skipped": r.get("pii_skipped"),
        "failed_reason": r.get("pii_failed_reason"),
        "message_count": len(r.get("messages", [])),
    } for r in rows]
    return JSONResponse({
        "n_total": len(rows), "n_verified": n_verified,
        "n_failed": n_failed, "n_skipped": n_skipped,
        "log": log, "rows": preview,
    })


# --------------------------- Stage 2 — Prompt 1 ------------------------ #


class Prompt1RunRequest(BaseModel):
    model: str = DEFAULT_MODEL_HEAVY
    concurrency: int = 5


def _sessions_for_prompt1() -> list[Session]:
    rows = pii.load_redacted_sessions(REDACTED_PATH)
    if rows is None:
        raise HTTPException(400, "Run Stage 1 (PII) first.")
    return pii.sessions_from_redacted(rows)


@app.post("/api/prompt1/run")
async def prompt1_run(body: Prompt1RunRequest) -> dict[str, Any]:
    sessions = _sessions_for_prompt1()
    job_id = _new_job(len(sessions))

    async def run() -> None:
        try:
            prompt = p1.load_prompt("prompt1.txt")
            results = await p1.run_prompt1(
                sessions, prompt_template=prompt, model=body.model,
                concurrency=body.concurrency, progress_cb=_progress(job_id),
            )
            p1.save_prompt1_results(results, FEATURES_PATH, PROMPT1_LOG_PATH)
            _write_conversations_json()
            STATE["jobs"][job_id]["status"] = "done"
        except Exception as e:
            STATE["jobs"][job_id]["status"] = "error"
            STATE["jobs"][job_id]["error"] = str(e)

    asyncio.create_task(run())
    return {"job_id": job_id, "total": len(sessions)}


@app.get("/api/prompt1/result")
def prompt1_result() -> JSONResponse:
    rows = p1.load_features(FEATURES_PATH)
    if rows is None:
        raise HTTPException(404, "No Prompt 1 output yet.")
    log = p1.load_prompt1_log(PROMPT1_LOG_PATH)
    return JSONResponse({
        "n_conversations": len(rows),
        "n_objectives": sum(len(r.get("objectives", [])) for r in rows),
        "rows": rows,
        "log": log,
    })


# --------------------------- Stage 3 — Prompt 2 ------------------------ #


class Prompt2RunRequest(BaseModel):
    model: str = DEFAULT_MODEL_HEAVY
    concurrency: int = 5


@app.post("/api/prompt2/run")
async def prompt2_run(body: Prompt2RunRequest) -> dict[str, Any]:
    sessions = _sessions_for_prompt1()
    features = p1.load_features(FEATURES_PATH)
    if features is None:
        raise HTTPException(400, "Run Stage 2 (Prompt 1) first.")

    total_objectives = sum(len(r.get("objectives", []) or []) for r in features)
    job_id = _new_job(max(1, total_objectives))

    async def run() -> None:
        try:
            prompt = p2.load_prompt("prompt2.txt")
            results = await p2.run_prompt2(
                sessions, features, prompt_template=prompt, model=body.model,
                concurrency=body.concurrency, progress_cb=_progress(job_id),
            )
            p2.save_prompt2_results(results, OBJECTIVES_PATH, PROMPT2_LOG_PATH)
            _write_objectives_json()
            STATE["jobs"][job_id]["status"] = "done"
        except Exception as e:
            STATE["jobs"][job_id]["status"] = "error"
            STATE["jobs"][job_id]["error"] = str(e)

    asyncio.create_task(run())
    return {"job_id": job_id, "total": total_objectives}


@app.get("/api/prompt2/result")
def prompt2_result() -> JSONResponse:
    rows = p2.load_objective_reports(OBJECTIVES_PATH)
    if rows is None:
        raise HTTPException(404, "No Prompt 2 output yet.")
    log = p2.load_prompt2_log(PROMPT2_LOG_PATH)
    return JSONResponse({"n_objectives": len(rows), "rows": rows, "log": log})


# -------------------- final datasets (conversations.json, objectives.json) -- #


def _structured_tools_by_conv() -> dict[str, list[str]]:
    """Map conversation_id → deduped list of structured tool names from
    the redacted_sessions.jsonl. Returns {} if the file or the tool_calls
    field is missing — we just won't override in that case."""
    rows = pii.load_redacted_sessions(REDACTED_PATH) or []
    out: dict[str, list[str]] = {}
    for row in rows:
        seen: list[str] = []
        for m in row.get("messages") or []:
            for name in (m.get("tool_calls") or []):
                if isinstance(name, str) and name and name not in seen:
                    seen.append(name)
        out[row.get("uuid", "")] = seen
    return out


def _write_conversations_json() -> None:
    rows = p1.load_features(FEATURES_PATH) or []
    structured = _structured_tools_by_conv()
    out = []
    for r in rows:
        features = dict(r.get("conversation_features") or {})
        structured_tools = structured.get(r.get("conversation_id", ""), [])
        if structured_tools:
            features["tools_used"] = structured_tools
            features["tools_used_source"] = "structured"
        else:
            features["tools_used_source"] = "inferred"
        out.append({
            "conversation_id": r.get("conversation_id"),
            "timestamp": r.get("created_at"),
            "models_used": r.get("models_used", []),
            **features,
            "objectives": r.get("objectives", []),
        })
    CONVERSATIONS_OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2))


def _write_objectives_json() -> None:
    rows = p2.load_objective_reports(OBJECTIVES_PATH) or []
    OBJECTIVES_OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2))


@app.get("/api/final/conversations")
def final_conversations() -> JSONResponse:
    if not CONVERSATIONS_OUT.exists():
        raise HTTPException(404, "No conversations.json yet.")
    return JSONResponse(json.loads(CONVERSATIONS_OUT.read_text()))


@app.get("/api/final/objectives")
def final_objectives() -> JSONResponse:
    if not OBJECTIVES_OUT.exists():
        raise HTTPException(404, "No objectives.json yet.")
    return JSONResponse(json.loads(OBJECTIVES_OUT.read_text()))


@app.get("/api/final/conversations/download")
def download_conversations() -> FileResponse:
    if not CONVERSATIONS_OUT.exists():
        raise HTTPException(404, "No conversations.json yet.")
    return FileResponse(
        CONVERSATIONS_OUT, media_type="application/json",
        filename="conversations.json",
    )


@app.get("/api/final/objectives/download")
def download_objectives() -> FileResponse:
    if not OBJECTIVES_OUT.exists():
        raise HTTPException(404, "No objectives.json yet.")
    return FileResponse(
        OBJECTIVES_OUT, media_type="application/json",
        filename="objectives.json",
    )


# ----------------------------- CSV exports ---------------------------- #


CONVERSATION_CSV_COLUMNS = [
    "conversation_id", "timestamp", "models_used",
    "work_related", "num_objectives", "num_turns", "conversation_duration_sec",
    "initial_prompt_length", "avg_message_length_user", "avg_message_length_assistant",
    "tools_used",
    "attachments_count", "attachments_types",
    "artifacts_count", "artifacts_types",
    "objective_descriptions",
]


def _flatten_conversation(row: dict[str, Any]) -> dict[str, Any]:
    attachments = row.get("attachments") or {}
    artifacts = row.get("artifacts_created") or {}
    objectives = row.get("objectives") or []
    return {
        "conversation_id": row.get("conversation_id"),
        "timestamp": row.get("timestamp"),
        "models_used": "; ".join(row.get("models_used") or []),
        "work_related": row.get("work_related"),
        "num_objectives": row.get("num_objectives"),
        "num_turns": row.get("num_turns"),
        "conversation_duration_sec": row.get("conversation_duration_sec"),
        "initial_prompt_length": row.get("initial_prompt_length"),
        "avg_message_length_user": row.get("avg_message_length_user"),
        "avg_message_length_assistant": row.get("avg_message_length_assistant"),
        "tools_used": "; ".join(row.get("tools_used") or []),
        "attachments_count": attachments.get("count"),
        "attachments_types": "; ".join(attachments.get("types") or []),
        "artifacts_count": artifacts.get("count"),
        "artifacts_types": "; ".join(artifacts.get("types") or []),
        "objective_descriptions": " | ".join(
            f"#{o.get('objective_id')}: {o.get('description', '')}" for o in objectives
        ),
    }


OBJECTIVE_CSV_COLUMNS = [
    "conversation_id", "objective_id", "description",
    "domain", "topic", "deliverable", "underlying_intent",
    "workflow_and_resolution", "user_approach", "user_signals",
    "language_and_tone", "additional_notes",
]


def _csv_response(rows: list[dict[str, Any]], columns: list[str], filename: str) -> Response:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({c: r.get(c) for c in columns})
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ----------------------------- cost report ---------------------------- #


def _aggregate_log(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum tokens, cost, duration across a per-call log. Handles both
    Stage 1's skipped/verified/failed status and Stages 2/3's error field."""
    total = {
        "n_calls": len(entries),
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "total_duration_s": 0.0,
        "n_errors": 0,
        "models": {},
    }
    for e in entries:
        total["input_tokens"] += int(e.get("input_tokens") or 0)
        total["output_tokens"] += int(e.get("output_tokens") or 0)
        total["cost_usd"] += float(e.get("cost_usd") or 0.0)
        total["total_duration_s"] += float(e.get("duration_s") or 0.0)
        if e.get("error") or e.get("failed_reason"):
            total["n_errors"] += 1
        model = e.get("model")
        if model:
            total["models"][model] = total["models"].get(model, 0) + 1
    if total["n_calls"]:
        total["avg_duration_s"] = round(total["total_duration_s"] / total["n_calls"], 3)
    total["cost_usd"] = round(total["cost_usd"], 6)
    total["total_duration_s"] = round(total["total_duration_s"], 2)
    return total


def _build_cost_report() -> dict[str, Any]:
    report: dict[str, Any] = {
        "generated_at": dt.datetime.utcnow().isoformat() + "Z",
        "stages": {},
        "totals": {"n_calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
    }
    stage_files = [
        ("pii", PII_LOG_PATH),
        ("prompt1", PROMPT1_LOG_PATH),
        ("prompt2", PROMPT2_LOG_PATH),
    ]
    for stage, path in stage_files:
        if not path.exists():
            continue
        try:
            entries = json.loads(path.read_text())
        except Exception:
            continue
        if not isinstance(entries, list):
            continue
        stage_agg = _aggregate_log(entries)
        report["stages"][stage] = stage_agg
        for k in ("n_calls", "input_tokens", "output_tokens"):
            report["totals"][k] += stage_agg[k]
        report["totals"]["cost_usd"] += stage_agg["cost_usd"]
    report["totals"]["cost_usd"] = round(report["totals"]["cost_usd"], 6)
    return report


# -------------------------- bundle download --------------------------- #


BUNDLE_README = """AI Log Analysis — Results Bundle
=================================

This archive contains the full output of one run of the 3-stage pipeline
(PII redaction, Prompt 1 conversation features + objective segmentation,
Prompt 2 per-objective structured interview), together with the prompts
that produced it and an aggregated cost report.

Files
-----
  conversations.json       Published dataset 1. One row per conversation.
  conversations.csv        Same, flattened for spreadsheet tools.
  objectives.json          Published dataset 2. One row per objective.
  objectives.csv           Same, flattened.
  cost_report.json         Aggregated tokens / cost / duration per stage.
  logs/                    Per-call timing, token, and cost breakdowns.
    pii_log.json             Stage 1 (also has PII verification status).
    prompt1_log.json         Stage 2.
    prompt2_log.json         Stage 3.
  prompts/                 Exact prompt texts used for this run.
    pii_redact.txt
    prompt1.txt
    prompt2.txt

Join key
--------
conversations.json and objectives.json link via (conversation_id,
objective_id). One conversation has 1+ objectives; one objective row has
all 9 interview fields.
"""


@app.get("/api/final/bundle")
def download_bundle() -> Response:
    if not CONVERSATIONS_OUT.exists() or not OBJECTIVES_OUT.exists():
        raise HTTPException(404, "Pipeline has not produced final datasets yet.")

    # Rebuild CSVs in-memory from the current JSON (source of truth).
    conv_rows = json.loads(CONVERSATIONS_OUT.read_text())
    obj_rows = json.loads(OBJECTIVES_OUT.read_text())

    conv_csv_buf = io.StringIO()
    writer = csv.DictWriter(conv_csv_buf, fieldnames=CONVERSATION_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for r in conv_rows:
        writer.writerow({c: _flatten_conversation(r).get(c) for c in CONVERSATION_CSV_COLUMNS})

    obj_csv_buf = io.StringIO()
    writer = csv.DictWriter(obj_csv_buf, fieldnames=OBJECTIVE_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for r in obj_rows:
        writer.writerow({c: r.get(c) for c in OBJECTIVE_CSV_COLUMNS})

    cost_report = _build_cost_report()

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", BUNDLE_README)
        zf.writestr("conversations.json", CONVERSATIONS_OUT.read_text())
        zf.writestr("conversations.csv", conv_csv_buf.getvalue())
        zf.writestr("objectives.json", OBJECTIVES_OUT.read_text())
        zf.writestr("objectives.csv", obj_csv_buf.getvalue())
        zf.writestr("cost_report.json", json.dumps(cost_report, ensure_ascii=False, indent=2))
        for stage_name, path in (("pii", PII_LOG_PATH), ("prompt1", PROMPT1_LOG_PATH),
                                 ("prompt2", PROMPT2_LOG_PATH)):
            if path.exists():
                zf.writestr(f"logs/{stage_name}_log.json", path.read_text())
        for prompt_name in ("pii_redact", "prompt1", "prompt2"):
            prompt_path = PROMPTS_DIR / f"{prompt_name}.txt"
            if prompt_path.exists():
                zf.writestr(f"prompts/{prompt_name}.txt", prompt_path.read_text())

    stamp = dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    filename = f"results_bundle_{stamp}.zip"
    return Response(
        content=zip_buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/final/cost_report")
def final_cost_report() -> JSONResponse:
    return JSONResponse(_build_cost_report())


@app.get("/api/final/conversations/download.csv")
def download_conversations_csv() -> Response:
    if not CONVERSATIONS_OUT.exists():
        raise HTTPException(404, "No conversations.json yet.")
    data = json.loads(CONVERSATIONS_OUT.read_text())
    flat = [_flatten_conversation(r) for r in data]
    return _csv_response(flat, CONVERSATION_CSV_COLUMNS, "conversations.csv")


@app.get("/api/final/objectives/download.csv")
def download_objectives_csv() -> Response:
    if not OBJECTIVES_OUT.exists():
        raise HTTPException(404, "No objectives.json yet.")
    data = json.loads(OBJECTIVES_OUT.read_text())
    return _csv_response(data, OBJECTIVE_CSV_COLUMNS, "objectives.csv")
