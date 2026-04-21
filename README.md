# AI Log Analysis

Conversation feature extraction from exported LLM chat logs. Implements the Stanford Digital Economy Lab three-stage pipeline: PII redaction → conversation-level features with objective segmentation → per-objective structured interview.

- **Stage 1 · PII redact**: one LLM call per conversation replaces identifiers with typed, indexed placeholders (`[PERSON_1]`, `[ORG_2]`, …). Structural diff verifies no turns were dropped or reordered.
- **Stage 2 · Prompt 1**: one call per (redacted) conversation returns conversation-level features (`work_related`, `num_turns`, `duration`, `tools_used`, attachments, artifacts) plus an objective segmentation with turn indices.
- **Stage 3 · Prompt 2**: one call per objective on the sliced sub-transcript. Returns nine prose interview fields — `underlying_intent`, `domain`, `topic`, `deliverable`, `workflow_and_resolution`, `user_approach`, `user_signals`, `language_and_tone`, `additional_notes`.

Downstream taxonomy, classification, and aggregation are out of scope — this tool produces the structured feature dataset the Stanford team consumes.

## Input

A JSON array of conversation exports (`conversations.json`). Each entry needs `uuid` and `chat_messages`; `name`, `created_at`, and per-message `created_at` / `model` are used when present.

## Outputs

| File | Contents |
|------|----------|
| `backend/results/redacted_sessions.jsonl` | Stage 1 output — one row per conversation with placeholder-substituted text |
| `backend/results/pii_log.json` | Per-conversation verification status, tokens, cost |
| `backend/results/conv_features.jsonl` | Stage 2 output — conversation features + objective segmentation |
| `backend/results/prompt1_log.json` | Per-conversation timing/cost |
| `backend/results/objectives.jsonl` | Stage 3 output — one row per objective with the nine interview fields |
| `backend/results/prompt2_log.json` | Per-objective timing/cost |
| `backend/results/conversations.json` | **Published dataset 1** — one row per conversation (features + objective list) |
| `backend/results/objectives.json` | **Published dataset 2** — one row per objective (interview report) |

## Run

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# Set at least one. Both can coexist; provider is picked per-model in the UI.
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
uvicorn app:app --reload --port 8000
```

Supported models (selectable per stage in the UI):

| Provider | Model | Typical use |
|---|---|---|
| Anthropic | `claude-sonnet-4-6` | Stage 2 & 3 default |
| Anthropic | `claude-haiku-4-5-20251001` | Stage 1 default |
| Anthropic | `claude-opus-4-7` | Heaviest option |
| OpenAI | `gpt-5` | Sonnet-equivalent |
| OpenAI | `gpt-5-mini` | Haiku-equivalent |
| OpenAI | `gpt-5-nano` | Cheapest, for Stage 1 bulk |
| OpenAI | `gpt-4.1` | Sonnet-equivalent fallback |

```bash
# Frontend
cd frontend
npm install
npm run dev
```

Open http://localhost:5173.

## UI flow

1. **Data** — drop `conversations.json`. Sessions under 2 messages or 100 chars are filtered.
2. **Pipeline** — three stacked stage cards, each with its own estimate, run button, and output preview:
   - Stage 1 · PII redaction (Haiku by default; uncheck to skip for internal data).
   - Stage 2 · Prompt 1 (Sonnet by default).
   - Stage 3 · Prompt 2 (Sonnet by default).
   Each stage is gated on the previous stage's output.
3. **Prompts** — edit `pii_redact.txt`, `prompt1.txt`, or `prompt2.txt` in place.
