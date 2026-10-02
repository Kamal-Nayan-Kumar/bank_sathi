# Bank Sathi — combined build plan

Merged from `plan1.md` (architecture-led) and `plan2.md` (rules-led), resolving
their conflicts in favour of whichever keeps the LLM furthest away from a
business decision.

## Conflict resolutions

| Question | plan1 | plan2 | Decision | Why |
|---|---|---|---|---|
| Vector store | Qdrant | pgvector in Postgres | **Qdrant** | You asked for Qdrant; keeps the RAG layer swappable and independent of the catalogue DB. |
| Frontend | Next.js | Streamlit | **Vite + React + TS + Tailwind** | You want it beautiful; Streamlit cannot carry the design direction. Static SPA, no SSR needed. |
| Eligibility shape | one rule list per card | global gate *then* per-card | **Both** | Global gate rejects hard red flags once; per-card rules run on whatever SQL returns (defense in depth). |
| Workflow | LangGraph for everything | fixed graph + LLM tools for chat | **Both** | Fixed graph for the decision path; tool-calling only for chat follow-ups. |
| Missing card fields | fill in, disclose | fill in, record `assumed_fields` | **Both** | `assumed_fields` per card + an assumed-fields report. |

## What the merged plan adds over each source
- `policy.yaml` (plan2) makes plan1's "never let DB and policy docs disagree" checkable by a test.
- Reason codes with `gap` (plan2) let one engine produce both the rejection
  explanation and the near-miss "₹6,000 more income / CIBIL +40" advice that
  plan1 asked for but never specified how to compute.
- A real verifier checklist (plan1 §19) plus plan2's repair→retry→template
  fallback ladder, and the constraint that it cannot alter a decision.
- Local fallbacks (SQLite + in-process vector store + template explanations) so
  the whole thing is runnable and testable before any API key exists.

## Trust hierarchy (enforced in code)
```
business rules (policy.yaml + engine)   ← highest
   ↓
structured catalogue (PostgreSQL/Neon)
   ↓
policy evidence (Qdrant RAG)
   ↓
LLM (Groq)                              ← prose only, never a decision
```

## Pipeline (LangGraph)
```
intake (LLM extract / form) → mask PII → validate (Pydantic)
  → missing fields? ask one follow-up and end the turn
  → global_gate (code) ──reject──► near-miss + improvement advice ─┐
  → prefilter (parameterized SQL)                                │
  → evaluate per card (code, reason codes + gaps)                │
  → score & rank (code) → top N                                  │
  → retrieve policy chunks (Qdrant) ─────────────────────────────┤
  → explain (LLM, facts + retrieved chunks only) ◄───────────────┘
  → verifier ──fail──► explain(retry ≤2) ──fail──► template fallback
  → RecommendationResponse
```

## Build order (executed in this order, each step tested)
1. Scaffolding, `.gitignore`, `.env.example`, `AGENTS.md`.
2. `policy.yaml` + Pydantic schemas (`schemas.py`, `config.py`, `policy.py`).
3. Seeded card-catalogue generator → `data/cards.json`.
4. Policy-document generator → `data/policies/*.md`, generated from `policy.yaml`.
5. Postgres schema + SQLAlchemy models + seed (`db/`, `schema.sql`).
6. Vector store abstraction (Qdrant ⇄ in-process fallback) + ingest.
7. Rule engine (`global_gate`, `prefilter_sql`, `evaluate`, `near_miss`) + tests.
8. Scoring/ranking (`scoring.py`) + oracle ranking for ground truth.
9. LLM client (Groq) + PII masking + template fallback.
10. LangGraph nodes/state/build.
11. FastAPI: chat + profile + policy-search endpoints.
12. Seeded profile generator + ground-truth evaluation set.
13. Evaluation harness → `evaluation/results.md`.
14. Frontend (design pass with the frontend-design skill).
15. README, Docker, final test run.

## Definition of done
`make test` green; `make data` reproducible from seed; the API answers a
natural-language request end to end with no keys set; the UI renders the ranked
list, per-card pass/fail reasons and the improvement advice; README documents
architecture, trust hierarchy, results and limitations.
