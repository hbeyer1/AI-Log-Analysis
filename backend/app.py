"""FastAPI server: Part 1 (Extract + Clean) of the AI Log Analysis pipeline."""
from __future__ import annotations

import asyncio
import json
import os
import uuid as uuid_lib
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

load_dotenv()

from data import parse_conversations, filter_substantive, Session
import pipeline_extract as pe
import pipeline_clean as pcl
from llm_client import DEFAULT_MODEL_HEAVY


BASE = Path(__file__).parent
UPLOADS = BASE / "uploads"
RESULTS = BASE / "results"
PROMPTS_DIR = BASE / "prompts"
UPLOADS.mkdir(exist_ok=True)
RESULTS.mkdir(exist_ok=True)

KB_PATH = BASE / "knowledge_base.json"
RAW_OBJECTIVES_PATH = RESULTS / "raw_objectives.jsonl"
REJECTED_PATH = RESULTS / "rejected_objectives.jsonl"
EXTRACT_LOG_PATH = RESULTS / "extract_log.json"
CLEANED_PATH = RESULTS / "cleaned_objectives.jsonl"
EXCLUDED_PATH = RESULTS / "excluded_objectives.jsonl"
CLEAN_LOG_PATH = RESULTS / "clean_log.json"


app = FastAPI(title="AI Log Analysis")

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


# ---------------- Status ---------------- #


@app.get("/api/status")
def status() -> dict[str, Any]:
    return {
        "anthropic_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "dataset_loaded": STATE["dataset_id"] is not None,
        "session_count": len(STATE["sessions"]),
        "substantive_count": len(STATE["substantive"]),
        "has_raw_objectives": RAW_OBJECTIVES_PATH.exists(),
        "has_cleaned_objectives": CLEANED_PATH.exists(),
    }


# ---------------- Upload / sessions ---------------- #


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


# ---------------- Prompts ---------------- #


class PromptUpdate(BaseModel):
    content: str


@app.get("/api/prompts")
def list_prompts() -> dict[str, Any]:
    files = sorted(PROMPTS_DIR.glob("*.txt"))
    return {"prompts": [{"name": f.stem, "size": f.stat().st_size} for f in files]}


@app.get("/api/prompts/{name}")
def get_prompt(name: str) -> dict[str, str]:
    try:
        return {"name": name, "content": pe.load_prompt(f"{name}.txt")}
    except FileNotFoundError:
        raise HTTPException(404, f"Prompt not found: {name}")


@app.put("/api/prompts/{name}")
def put_prompt(name: str, body: PromptUpdate) -> dict[str, str]:
    pe.save_prompt(f"{name}.txt", body.content)
    return {"name": name, "status": "saved"}


# ---------------- Jobs ---------------- #


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


# ---------------- Extract (Part 1a) ---------------- #


class ExtractRunRequest(BaseModel):
    limit: int | None = None
    model: str = DEFAULT_MODEL_HEAVY
    concurrency: int = 5


def _selected_sessions(limit: int | None) -> list[Session]:
    sessions = STATE["substantive"]
    if limit:
        sessions = sessions[:limit]
    return sessions


@app.post("/api/extract/estimate")
def extract_estimate(body: ExtractRunRequest) -> dict[str, Any]:
    sessions = _selected_sessions(body.limit)
    if not sessions:
        raise HTTPException(400, "No dataset loaded")
    prompt = pe.load_prompt("extract.txt")
    return pe.estimate_extract_cost(sessions, prompt, model=body.model)


@app.post("/api/extract/run")
async def extract_run(body: ExtractRunRequest) -> dict[str, Any]:
    sessions = _selected_sessions(body.limit)
    if not sessions:
        raise HTTPException(400, "No dataset loaded")

    job_id = _new_job(len(sessions))

    async def run() -> None:
        try:
            prompt = pe.load_prompt("extract.txt")
            results = await pe.run_extract(
                sessions, prompt_template=prompt,
                model=body.model, concurrency=body.concurrency,
                progress_cb=_progress(job_id),
            )
            pe.save_extract_results(results, RAW_OBJECTIVES_PATH, REJECTED_PATH, EXTRACT_LOG_PATH)
            STATE["jobs"][job_id]["status"] = "done"
        except Exception as e:
            STATE["jobs"][job_id]["status"] = "error"
            STATE["jobs"][job_id]["error"] = str(e)

    asyncio.create_task(run())
    return {"job_id": job_id, "total": len(sessions)}


@app.get("/api/extract/result")
def extract_result() -> JSONResponse:
    objectives = pe.load_objectives(RAW_OBJECTIVES_PATH)
    if objectives is None:
        raise HTTPException(404, "No objectives yet.")
    log = pe.load_log(EXTRACT_LOG_PATH)
    return JSONResponse({
        "objectives": objectives,
        "log": log,
        "n_objectives": len(objectives),
        "n_sessions": len(log),
    })


@app.get("/api/extract/rejected")
def extract_rejected() -> JSONResponse:
    return JSONResponse({"rejected": pe.load_rejected(REJECTED_PATH)})


# ---------------- Knowledge base ---------------- #


@app.get("/api/knowledge-base")
def get_kb() -> JSONResponse:
    return JSONResponse(pcl.load_knowledge_base(KB_PATH))


class KBUpdate(BaseModel):
    exclusion_patterns: list[dict[str, Any]] | None = None
    category_seeds: list[dict[str, Any]] | None = None
    merge_rules: list[dict[str, Any]] | None = None
    boundary_clarifications: list[dict[str, Any]] | None = None


@app.put("/api/knowledge-base")
def put_kb(body: KBUpdate) -> JSONResponse:
    current = pcl.load_knowledge_base(KB_PATH)
    payload = body.model_dump(exclude_none=True)
    current.update(payload)
    pcl.save_knowledge_base(KB_PATH, current)
    return JSONResponse(current)


# ---------------- Clean (Part 1b) ---------------- #


class CleanRunRequest(BaseModel):
    model: str = DEFAULT_MODEL_HEAVY


@app.post("/api/clean/estimate")
def clean_estimate(body: CleanRunRequest) -> dict[str, Any]:
    objectives = pe.load_objectives(RAW_OBJECTIVES_PATH) or []
    if not objectives:
        raise HTTPException(400, "Run extraction first.")
    kb = pcl.load_knowledge_base(KB_PATH)
    prompt = pe.load_prompt("clean_judge.txt")
    return pcl.estimate_clean_cost(objectives, kb, prompt, model=body.model)


@app.post("/api/clean/run")
async def clean_run(body: CleanRunRequest) -> dict[str, Any]:
    objectives = pe.load_objectives(RAW_OBJECTIVES_PATH) or []
    if not objectives:
        raise HTTPException(400, "Run extraction first.")

    kb = pcl.load_knowledge_base(KB_PATH)
    job_id = _new_job(len(objectives))

    async def run() -> None:
        try:
            prompt = pe.load_prompt("clean_judge.txt")
            res = await pcl.run_clean(
                objectives, knowledge_base=kb,
                prompt_template=prompt, model=body.model,
                progress_cb=_progress(job_id),
            )
            pcl.save_clean_results(res, CLEANED_PATH, EXCLUDED_PATH, CLEAN_LOG_PATH)
            STATE["jobs"][job_id]["status"] = "done"
        except Exception as e:
            STATE["jobs"][job_id]["status"] = "error"
            STATE["jobs"][job_id]["error"] = str(e)

    asyncio.create_task(run())
    return {"job_id": job_id, "total": len(objectives)}


@app.get("/api/clean/result")
def clean_result() -> JSONResponse:
    cleaned = pcl.load_cleaned(CLEANED_PATH)
    if cleaned is None:
        raise HTTPException(404, "No clean run yet.")
    return JSONResponse({
        "kept": cleaned,
        "log": pcl.load_clean_log(CLEAN_LOG_PATH),
        "n_kept": len(cleaned),
    })


@app.get("/api/clean/excluded")
def clean_excluded() -> JSONResponse:
    return JSONResponse({"excluded": pcl.load_excluded(EXCLUDED_PATH)})
