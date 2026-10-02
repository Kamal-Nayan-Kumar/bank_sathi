# AGENTS.md

Project: **Bank Sathi** — an agentic credit-card recommendation engine (India, INR).
Read this file before writing any code in this repo.

## Stack
Python 3.11 · FastAPI · Pydantic v2 · LangGraph · SQLAlchemy 2 · PostgreSQL/Neon
(structured catalogue) + Qdrant (policy text) · Groq (LLM) · pytest · Vite +
React + TS + Tailwind (frontend). Currency is **INR**; amounts are whole rupees.

## Non-negotiable rules
1. **`policy.yaml` is the single source of truth for every threshold.** Never
   hardcode a number (income minimum, CIBIL cutoff, fee, multiplier, cap) in
   Python, SQL, prompts or docs. Policy markdown for RAG is *generated from*
   `policy.yaml` so the DB and the RAG layer can never disagree.
2. **The LLM never decides anything.** It only extracts a profile from natural
   language, asks follow-ups, and writes prose. Eligibility, ranking, near-miss
   gaps and every number come from code. The LLM never writes SQL.
3. **The verifier may block or repair text, never change a decision.**
4. **All synthetic data is seeded and reproducible** (`SYNTHETIC_SEED`). Banks,
   card names and customers are fictional. No real PII anywhere.
5. **Any catalogue field not present in the source is recorded in
   `assumed_fields`** and reported in the README.
6. **Every module needs pytest tests.** Run `make test` before claiming a task
   is done. Boundary tests at threshold−1 / threshold / threshold+1 are required
   for anything comparing against a threshold.

## Layout
```
backend/app/          schemas.py  config.py  policy.py  db.py  llm.py  masking.py
backend/app/rules/    engine.py (gate, eligibility, near-miss)  scoring.py (rank)
backend/app/graph/    state.py  nodes.py  build.py
backend/app/rag/      vectorstore.py  ingest.py  retriever.py
data/                 cards.json  profiles.jsonl  policies/*.md (generated)
scripts/              generate_cards.py  generate_policies.py  generate_profiles.py  ingest.py
evaluation/           run_eval.py  metrics.py
frontend/src/         App.tsx  components/  lib/
```

## Local commands
```
make setup     # venv + deps + node modules
make data      # generate cards, policies, profiles, ground truth, then ingest
make test      # pytest
make dev       # backend :8000 + frontend :5173 (two shells)
make eval      # run the evaluation harness -> evaluation/results.md
```

## Conventions
- Reason codes are stable strings (`INCOME_BELOW_MIN`, `CIBIL_BELOW_MIN`, …) and
  their customer-facing wording lives in `policy.yaml`, never in the rule engine.
- DB access is parameterized only. Migrations are plain SQL in
  `backend/app/db/schema.sql`.
- Prefer small pure functions over services so they are trivially testable.
- Docstrings explain *why*, not *what*.
