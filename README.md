# Card Sathi

A credit-card recommendation engine for the Indian market that **shows its working**.
Every number it gives you is traceable to the rule that produced it, and every
card it does not show comes with the exact requirement you missed.

The point is not the recommendation. The point is that you can check it.

**Live:** [card-sathi.vercel.app](https://card-sathi.vercel.app) ·
**API:** `card-sathi-api.onrender.com` (Render blueprint in `render.yaml`) ·
**Report:** [`report/Card_Sathi_Report.pdf`](report/Card_Sathi_Report.pdf)

---

## Why this exists

Almost every recommender ends with a number and no argument. "Card A: 92%
match." Match to what, by how much, on whose rules?

This one is built the other way round. The rules run first and in public:

```
                  TRUST LEVEL

          ┌────────────────────┐
          │   Business rules   │  ← highest. Plain Python, no model.
          └─────────┬──────────┘
                    ↓
          ┌────────────────────┐
          │  Catalogue (Neon)  │  structured facts: income floors, fee, rates
          └─────────┬──────────┘
                    ↓
          ┌────────────────────┐
          │  Policy (Qdrant)   │  why a rule exists, in prose
          └─────────┬──────────┘
                    ↓
          ┌────────────────────┐
          │    LLM (Groq)      │  reads what you type, writes the sentence
          └────────────────────┘   never decides anything
```

A language model cannot make someone eligible for a card it does not own, because
it never gets asked. It reads your message, and it writes the paragraph under the
answer.

---

## Quick start

```bash
git clone <repo> && cd card_sathi
cp .env.example .env          # fill in whatever you have; all of it is optional
make setup                    # venv + backend deps + npm install
make data                     # generate the catalogue, profiles, ground truth
make dev                      # API on :8000, UI on :5173
```

Without a single API key it still runs: SQLite instead of Neon, an in-process
store instead of Qdrant, a regex extractor instead of an LLM, and a deterministic
template instead of generated prose. That is deliberate — it is what makes
`make test` meaningful in CI.

| Target | What it does |
|---|---|
| `make setup` | venv, backend deps, frontend deps |
| `make data` | 120 cards, 322 profiles, ground truth, ingest into Qdrant |
| `make dev` | both servers, Ctrl-C stops both |
| `make test` | 161 tests |
| `make lint` | ruff |
| `make eval` | evaluation harness → `evaluation/results.md` |
| `make eval-fast` | same, no API calls (CI) |

---

## The two entry points

Both converge on the same decision path, so they cannot disagree.

**Describe yourself** — the chat route. Natural language in, one follow-up
question at a time until enough is known, then the answer. PII is stripped
before anything reaches the model.

**Fill in details** — the form route. A validated profile straight to the engine.
No model involved in the decision at all, which makes it the strongest available
test of whether the architecture holds.

---

## How the pipeline works

A LangGraph workflow with eight nodes. The interesting part is which of them can
be wrong in which direction.

```
  START ─┬─► intake ──(incomplete)──► followup ──► END
         │      │
         │      └─(complete)──► build_profile
         │
         └─► build_profile ──► gate ──┬─(pass)──► prefilter ──► evaluate ──┬─► rank ──► evidence ──► explain ──► respond
                                       └─(reject)───────────────────────────────► evidence ──► explain ──► respond
```

- **intake** — LLM extracts a partial profile. Told to leave fields null rather
  than guess, because a wrong income produces a confident wrong answer.
- **followup** — one question, then end the turn. Looping back would re-run
  extraction for no new information.
- **gate** — company-wide screening. No LLM, no catalogue scan.
- **prefilter** — parameterised SQL narrows the catalogue.
- **evaluate** — every rule, in Python, for every surviving card. Re-checks what
  SQL already filtered: cheap, and it means a SQL bug cannot approve anyone.
- **rank** — deterministic scoring. Every score is a number the UI can show.
- **evidence** — RAG for the top cards only. Retrieving for all 60 eligible cards
  would spend latency on cards nobody sees.
- **explain** — LLM writes prose from computed facts, then the verifier checks it.

---

## Three decisions worth defending

### Eligibility is a rule engine, not a classifier

```
net annual value = (spend × rate, capped per category) × 12
                 + lounge credit   (only if you asked for it)
                 − annual fee      (only if you'd actually pay it)
```

Every card gets the same treatment, so `CARD_032` ranks the same way for every
customer with the same profile. "The model thinks Card A is better" is not an
answer you can check.

### Near misses are computed, not written

When a card fails by a small margin, the same function that rejected it also
returns the gap: "Rs 6,000 more a month" or "25 credit-score points short". The
advice is arithmetic on the number that failed, not a paragraph about credit
building.

### The verifier can block, never override

Twelve deterministic checks. If the prose contradicts the engine — recommends a
card that failed eligibility, cites a figure that is not in the computed facts,
invents a card, leaks a PAN, exposes an internal reason code — it fails. Two
retries with the complaints, then a deterministic template.

The UI shows the badge whether it passed or failed, and says when the text was
template-generated rather than model-written. A check that can only be green is
decoration.

---

## Results

`make eval`, 120 cards, 322 profiles. Full output in
[`evaluation/results.md`](evaluation/results.md).

| Measurement | Result | Note |
|---|---|---|
| Ground truth vs. engine, card level | **100.0%** | 1.0 by construction; guards pipeline drift |
| Gate conformance | **100.0%** | 322 profiles |
| Eligibility conformance | **100.0%** | 37,352 card evaluations |
| **False eligible rate** | **0.0** | never approved a card the rules reject |
| Ranking top-1 accuracy | **98.2%** | vs. deterministic oracle |
| NDCG@3 | **98.4%** | |
| Verifier pass rate | **100%** | 0 retries, 0 template fallbacks |
| Robustness invariants | **7/7** | injection, PII, determinism |
| Latency p50 / p95 (no prose) | **161ms / 2.3s** | against remote Neon + Qdrant |

Two numbers need explaining rather than celebrating:

- **Ground-truth agreement is 100% because the ground truth is produced by the
  same engine.** That makes it a check for pipeline drift and SQL/rule
  disagreement — not a measure of policy quality. Reported as spec conformance,
  not accuracy.
- **The ablation did not run.** All 12 attempts hit rate limits on the shared
  free tier, and the harness counts and excludes failed calls rather than
  silently scoring them as correct. Re-run when the tier is not saturated; the
  section will then say how often an LLM asked to decide alone recommends a card
  the rules reject.

### Where the latency goes

Two reasoning-model calls dominate. Measured on the request path:

| Stage | Time |
|---|---|
| Intake (extraction) | 3–6s, occasionally 15s+ on rate-limit failover |
| Prefilter + candidate load | ~1.7s (3 round trips to Neon at ~250ms each) |
| Evidence retrieval | ~4s (Qdrant round trips + embedding) |
| Explain | 5–15s |
| **Total** | **~25s typical, ~45s+ when rate limited** |

What was fixed along the way: rate limits now fail over immediately instead of
retrying (retrying a saturated free tier added ~40s without a single success);
the prefilter stopped re-reading the whole catalogue; both prefilter queries
share one session; retrieval batches its embeddings; and boot warms the
embedder, vector store and catalogue so the first request does not pay for
model load.

---

## Tech

| | |
|---|---|
| Backend | FastAPI, Pydantic v2, LangGraph |
| Catalogue | PostgreSQL (Neon) — structured facts, exact filters |
| Policy retrieval | Qdrant + `all-MiniLM-L6-v2` via fastembed, running locally |
| LLM | Groq (`openai/gpt-oss-120b`), OpenRouter free fallback |
| Frontend | Vite, React, TypeScript, Tailwind v4 |
| Tests | pytest, 161 tests |

Embeddings run locally. No embedding API key, no per-request cost, and retrieval
quality does not depend on a third-party key being present in production.

### Deployment

- **Frontend → Vercel.** Live at
  [card-sathi.vercel.app](https://card-sathi.vercel.app). Root directory
  `frontend`, output `dist`, `VITE_API_URL` set to the Render origin.
  `frontend/vercel.json` pins the same settings for CLI deploys.
- **Backend → Render.** `render.yaml` is a blueprint (New → Blueprint → point at
  this repo, region Singapore). Set the four `sync: false` vars (Groq key,
  OpenRouter key, Neon URL, Qdrant URL + key) and deploy. Health check is
  `/api/health`.
- **Catalogue → Neon Postgres.** **Policy → Qdrant Cloud.**
- **Once the API is up**, point the frontend at it:
  `vercel env add VITE_API_URL production` (value: the Render origin), then
  `vercel --prod`.

Both fall back gracefully, so a partial configuration still deploys.

---

## Design of the interface

The subject is a bank ledger, so the interface is one: passbook paper, ink, the
green of a printed account column, and stamp-red reserved strictly for failures.
One superfamily in three voices — IBM Plex Sans Condensed for form furniture,
IBM Plex Sans for prose, IBM Plex Mono for every figure, because these numbers
come out of a calculation and should align in columns.

The one memorable element is the **clearance block** under each card: the four
weighted scoring components as a proportional bar. The product's claim is that a
ranking should be derivable rather than asserted, so the score is never shown as
a bare number. If a card looks wrong in the list, you can see which component is
responsible.

---

## Limitations

Stated plainly, because a project that hides these is asking to be trusted with
the parts it does not deserve trust on.

- **The catalogue is synthetic.** 120 fictional banks and cards, generated from a
  seed. `apr_pct`, `lounge_visits_per_year` and `reward_caps` have no published
  analogue and are disclosed per card in `assumed_fields`.
- **Ground truth is self-generated.** See above. It proves the pipeline is
  faithful, not that the policy is good.
- **Extraction is the weakest stage.** `monthly_spend` is only 10% accurate
  because people say "about 40k" rather than a category breakdown. The system
  handles this by asking, which is the right behaviour, not by guessing.
- **Latency is dominated by free-tier reasoning models.** Production use should
  budget for it or pick a smaller model for extraction.
- **Two providers, both free tiers.** The fallback handles rate limits; it is not
  a redundancy guarantee.
- **No rate limiting, auth or persistence on the API.** The chat session lives in
  the client by design, which keeps it stateless but means nothing is stored.
- **The verifier is a set of string and number comparisons**, not a judge of
  reasoning. The optional LLM grounding check is off by default and can only add
  a failure, never remove one.

---

## Repository layout

```
backend/app/
  thresholds.py     every number the engine enforces. One file, no duplicates.
  schemas.py        the contracts that cross module boundaries
  rules/engine.py   global gate, per-card eligibility, near-miss gaps
  rules/scoring.py  net value and the ranking weights
  graph/            LangGraph state, nodes, construction
  rag/              Qdrant store, chunking, retrieval
  intake.py         LLM and regex extractors
  explain.py        prompt assembly and the template fallback
  verify.py         the twelve checks
  llm_tools.py      read-only tools for chat follow-ups
data/policies/      hand-written markdown, ingested into Qdrant
frontend/src/       the interface
evaluation/         the harness and its results
scripts/            build_data.py, dev_run.py
```

`data/policies/*.md` is source content and contains **no thresholds** — a test
enforces it. That is what makes a policy edit safe: the prose a person edits and
the numbers the engine enforces are in different places, so they cannot drift.

---

## Licence

Demonstration project. Cards, banks, rates and customers are fictional. Nothing
here is financial advice, an approval, or an offer of credit.
