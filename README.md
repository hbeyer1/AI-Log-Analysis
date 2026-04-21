# AI Log Analysis

Extract and clean user objectives from exported LLM conversation logs. Implements Part 1 (steps 1a and 1b) of the broader taxonomy pipeline — the taxonomy, validation, and knowledge-base update steps are out of scope.

- **1a · Extract (knowledge-free)**: one LLM call per session produces a list of distinct user objectives, each with a four-dimension resolution summary (initial framing · interaction pattern · user effort · outcome), a verbatim source quote, and the user-turn indices involved.
- **1b · Clean (knowledge-informed)**: applies exclusion patterns from `knowledge_base.json` to drop non-substantive items (greetings, test messages, trivial exchanges). Empty-pattern runs apply only a minimal heuristic. Excluded items are logged for audit — nothing is lost; raw extraction is always preserved.

## Input

A JSON array of Claude conversation exports (`conversations.json`). Each entry needs `uuid` and `chat_messages`; `name` and `created_at` are optional. See the main spec for the full schema.

## Outputs

| File | Contents |
|------|----------|
| `backend/results/raw_objectives.jsonl` | Extract output — one objective per line, unfiltered |
| `backend/results/rejected_objectives.jsonl` | Extract rows that failed validation (e.g. quote not in transcript) |
| `backend/results/extract_log.json` | Per-session timing + token/cost breakdown |
| `backend/results/cleaned_objectives.jsonl` | Clean output — objectives that survive filtering |
| `backend/results/excluded_objectives.jsonl` | Excluded items with matched pattern + reason |
| `backend/results/clean_log.json` | Clean-run stats |
| `backend/knowledge_base.json` | Persistent knowledge base (exclusion patterns, + placeholder keys for downstream pipeline) |

## Run

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
uvicorn app:app --reload --port 8000
```

```bash
# Frontend
cd frontend
npm install
npm run dev
```

Open http://localhost:5173.

## UI flow

1. **Data** — drop `conversations.json`. Sessions under 2 messages or under 100 total chars are filtered.
2. **1 · Extract** — pick a limit + model, preview estimated cost, run. Tabs show objectives, per-session log, and validation rejections.
3. **2 · Clean** — edit exclusion patterns (pattern description + reason), run the judge, inspect kept vs. excluded.
4. **Prompts** — edit `extract.txt` or `clean_judge.txt` in place if you want to tweak behaviour.
