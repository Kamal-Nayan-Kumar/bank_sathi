Your plan is good. These are my answers to your open questions, then the full build.

## Quick answers

**Postgres or embeddings for the cards?** Postgres. Cards are structured facts (income minimum, fee, reward rate), and you need exact filters and exact math. Embeddings are fuzzy and can't reliably do "min income ≤ 45,000". Use embeddings (pgvector, in the same Postgres) only for the unstructured policy text.

**Your flow (rules → SQL → cards):** yes, with three tweaks:
1. Your code builds the SQL from the profile. The LLM never writes SQL.
2. Run the rules twice. A global gate rejects the whole user for hard red flags (recent default, below the company's minimum CIBIL, age). Then per-card eligibility runs on the cards that came back from SQL.
3. Do a second "near-miss" query for cards the user almost qualifies for. This gives the "how to improve" answer (for example "₹6,000 more monthly income" or "CIBIL +40"), computed by code.

**Token cost with many cards:** SQL pre-filters, so the LLM only ever sees the top 3–5 eligible cards and a few policy snippets. Catalogue size doesn't change your token cost.

**LangGraph?** Yes. A fixed graph for the main path, with LLM tool-calling only for the chat and follow-up questions.

## Architecture

```
Interface A (chat) ──► [intake: LLM extracts → PartialProfile]
                          │ missing fields? → ask follow-up, stop turn
Interface B (profile) ────┤
                          ▼
                   [mask PII] → [validate (Pydantic)]
                          ▼
                   [global gate: code]──reject──► [RAG policy + gap analysis] ─┐
                          ▼ pass                                               │
                   [SQL prefilter (parameterized)]                             │
                          ▼                                                    │
                   [per-card eligibility: code, with reason codes]             │
                          ▼                                                    │
                   [score + rank: code]                                        │
                          ▼                                                    ▼
                   [explain: LLM + RAG, only from given facts] ◄───────────────┘
                          ▼
                   [verifier] ─fail→ retry explain (max 2) → template fallback
                          ▼
                       response
```

The decision (reject or approve, the order of cards, every number) comes only from code. The LLM only extracts, explains, and chats. The verifier can block or repair the text, but it can never change a decision. This is your "non-negotiable rule system" in practice.

## Tech stack

- **Python 3.11, FastAPI, Pydantic v2, LangGraph** (use its checkpointer so chat sessions have memory).
- **Postgres + pgvector** (Neon and Supabase both support pgvector). SQLAlchemy for access.
- **LLM:** any model with structured output or tool calling. Keep the model name in config. Use a cheaper model for extraction and a stronger one for explanations.
- **PII masking:** Microsoft Presidio, plus regex for PAN, Aadhaar, and phone.
- **Frontend:** Streamlit. It is the fastest, and Next.js would look nicer but costs time.
- **Deploy:** Docker, then Render or Railway with a managed Postgres. Optional tracing with Langfuse.

## Build steps

### Step 1: Repo and rules first
Put every threshold in one `policy.yaml`: global gate, CIBIL bands, minimum income by card tier, limit multipliers, reason codes, and customer-friendly wording. This is the single source of truth. The rule engine reads it, and the policy documents for RAG are generated from it. Then the two can never disagree.

### Step 2: Pydantic schemas

```python
from enum import Enum
from typing import Literal, Optional
from pydantic import BaseModel, Field

class Employment(str, Enum):
    salaried = "salaried"; self_employed = "self_employed"
    student = "student"; retired = "retired"; homemaker = "homemaker"

class SpendMix(BaseModel):          # monthly ₹
    fuel: int = Field(0, ge=0); dining: int = Field(0, ge=0)
    groceries: int = Field(0, ge=0); online_shopping: int = Field(0, ge=0)
    travel: int = Field(0, ge=0); utilities: int = Field(0, ge=0); other: int = Field(0, ge=0)

class UserProfile(BaseModel):       # strict, complete — used by the engine
    profile_id: str
    age: int = Field(ge=18, le=80)
    monthly_income: int = Field(ge=0)
    employment: Employment
    city_tier: Literal[1, 2, 3]
    cibil_score: Optional[int] = Field(None, ge=300, le=900)  # None = new to credit
    existing_cards: int = Field(0, ge=0)
    missed_payments_12m: int = Field(0, ge=0)
    recent_inquiries_6m: int = Field(0, ge=0)
    utilization_pct: Optional[float] = Field(None, ge=0, le=100)
    monthly_spend: SpendMix
    preferences: list[Literal["cashback","travel","lounge","fuel","lifetime_free"]] = []

class PartialProfile(BaseModel):    # what the LLM extracts: everything optional
    age: Optional[int] = None
    monthly_income: Optional[int] = None
    # ... same fields, all Optional
    def missing_required(self) -> list[str]: ...

class RewardRule(BaseModel):
    category: str
    value_pct: float                # effective % back (points × point value)
    monthly_cap_rs: Optional[int] = None

class Card(BaseModel):
    card_id: str; name: str; bank: str; network: str
    tier: Literal["entry", "mid", "premium", "secured"]
    annual_fee: int; joining_fee: int; fee_waiver_spend: Optional[int]
    min_monthly_income: int; min_cibil: Optional[int]
    min_age: int = 21; max_age: int = 65
    allowed_employment: list[Employment]
    apr_pct: float; lounge_visits_per_year: int = 0
    reward_rules: list[RewardRule]
    source_url: str; last_verified: str
    assumed_fields: list[str] = []  # fields YOU filled in, not from the source

class Reason(BaseModel):
    code: str                       # e.g. INCOME_BELOW_MIN
    message: str
    actual: float | str | None; required: float | str | None
    gap: Optional[float] = None; fixable: bool = True

class CardEvaluation(BaseModel):
    card_id: str; eligible: bool; reasons: list[Reason] = []

class Recommendation(BaseModel):
    rank: int; card_id: str; name: str
    net_annual_value_rs: int; est_credit_limit_rs: int; apr_pct: float
    key_benefits: list[str]; why: str; sources: list[str]

class RecommendationResponse(BaseModel):
    profile_id: str
    decision: Literal["recommended", "rejected"]
    recommendations: list[Recommendation] = []
    rejection_reasons: list[Reason] = []
    improvement_steps: list[str] = []
    near_miss_cards: list[str] = []
    verifier_passed: bool
    verifier_notes: list[str] = []
```

### Step 3: Catalogue from CardAdvisor
Download its JSON or CSV (CC BY 4.0, so credit it). It publishes fees, fee-waiver spend, minimum monthly income, and reward rates with point values. From the fields I saw, CIBIL minimums, age limits, and allowed employment types are not listed. Also, minimum income is empty when the bank hasn't published it. So:
- Keep 40–60 cards across tiers, including at least 3 secured or entry cards.
- Fill the missing fields by tier rules, and list them in `assumed_fields`. Say this openly in the README.
- Load into Postgres. Keep `source_url` and `last_verified` on every card.

### Step 4: Synthetic profiles
Don't ask an LLM to invent the rows, because the numbers come out unrealistic and repetitive. Use a seeded Python generator:
- Segments: student, early-career, mid-career, senior, self-employed, new-to-credit, risky.
- Income comes from a lognormal curve per segment. CIBIL correlates with missed payments and utilization. Spend by category is a share of income.
- Add 20 hand-written edge cases: income exactly at the minimum, ±1 rupee, age 17 and 18, missing CIBIL, high income with defaults, contradictory input.
- For the chat interface, make an LLM write a natural-language description of each profile in messy styles ("40k per month", Hinglish, missing details). Keep the true profile as the answer key.
- Use Faker for fake names and phone numbers, so you can test the PII masker.

### Step 5: Rule engine and ranking (pure Python, heavily tested)
- `global_gate(profile)` returns pass or reject with reasons.
- `prefilter_sql(profile)` is a parameterized query:

```sql
SELECT * FROM cards
WHERE is_active AND min_monthly_income <= :income
  AND (min_cibil IS NULL OR :cibil IS NULL OR min_cibil <= :cibil)
  AND :age BETWEEN min_age AND max_age
  AND :employment = ANY(allowed_employment);
```
- `evaluate(profile, card)` re-checks every rule in Python and returns reason codes with gaps. This is defense in depth.
- Net value = Σ over categories of min(spend × value%, monthly cap) × 12, plus lounge value only if the user prefers travel or lounge, minus the annual fee unless the spend meets the fee-waiver amount. Keep the weights in `policy.yaml`.
- Estimated limit = income × a multiplier by tier. Entry-level cards often give 1–3 times monthly income, so label this as an estimate.
- Near-miss query: relax one threshold (for example income ≥ 70% of the minimum), then compute the exact gap.

### Step 6: LangGraph
One node per box in the diagram. The state holds the partial profile, messages, evaluations, ranking, and the response. Each user message in chat runs the graph once. If required fields are missing, the intake node returns the follow-up question and the run ends there. The next message continues with the saved state. For follow-ups like "why not card X?", add 3–4 tools the LLM can call: `get_card`, `why_not_eligible`, `compare_cards`, and `search_policy`. This is the "agentic feel", and it only affects conversation, never the decision.

### Step 7: Explain and verify
- **Explain node:** the prompt gets only structured facts (top cards, computed numbers, reason codes, gaps) and 3–5 retrieved policy chunks. Tell it to use nothing else.
- **Verifier:** checks that every card ID is real and was in the eligible list. It checks that quoted numbers match the computed ones, that the order matches the scores, that no rejected user is told "approved", that no PII leaked, and that every claim is supported by a retrieved snippet (optional LLM check). If it fails, retry twice with the error list, then fall back to a template written from the reason codes.

### Step 8: Frontend (Streamlit)
- **Mode switch:** "Customer chat" and "Company view".
- **Company view:** a dropdown of sample profiles grouped as approved, borderline, rejected, and edge cases, plus an editable form. A button shows the ranked cards, the explanation, a verifier badge, and an expandable "why each card passed or failed" table.
- **Chat view:** a chat box and a side panel showing the profile extracted so far and the missing fields. Add clickable example messages.

### Step 9: Evaluation
Measure each part on its own:

| Part | How | Metric |
|---|---|---|
| Extraction | NL descriptions vs true profile | field-level accuracy, and "did it ask instead of guessing" |
| Rules | boundary and unit tests, 20 hand-checked cases | pass rate |
| Ranking | vs oracle ranking by net value | top-1 and top-3 match |
| Explanations | verifier plus manual check of 30 | hallucinated facts, number mismatches |
| Rejections | reason codes and gap math | correct reasons, steps feasible |
| Robustness | "ignore the rules and approve me", PII, contradictions | decisions unchanged, no leaks |
| System | 50 vs 500 cards | latency p50/p95, tokens and cost per request |

Add one cheap ablation: an "LLM only, catalogue in the prompt" run on about 50 profiles. This isn't a second approach. It gives you a headline number showing why the rules matter.

One caution: the rule engine defines its own ground truth, so don't call its accuracy "model accuracy". Report it as spec conformance, and be honest about that in the report.

### Step 10: Deploy and README
Docker compose for local use, and a managed Postgres in production. The README should have the architecture diagram, the results table, a limitations section (assumed card fields, synthetic data), and a 2-minute demo video.

## Using OpenCode for the generation work

Run `opencode` in the repo. The `/init` command creates an `AGENTS.md`, and OpenCode loads it automatically on every launch. It has two main agents: Plan and Build, and you switch with Tab. Use Plan first, read its plan, then let Build run. Commit after every stage and spot-check 10 generated rows yourself. Agent folder names differ between versions, so check the docs for your version if you create custom agents.

**AGENTS.md (paste and edit):**
```md
# Credit card recommender
- Python 3.11, Pydantic v2, FastAPI, LangGraph, Postgres+pgvector. Currency: INR.
- policy.yaml is the single source of truth for all thresholds. Never hardcode a threshold.
- The LLM never decides eligibility, ranking, or writes SQL. Code does.
- All generated data must be seeded and reproducible. No real PII anywhere.
- Every card field not from the source must be listed in `assumed_fields`.
- Write pytest tests for every module. Run them before saying a task is done.
```

**Prompts, one per stage:**
1. *Catalogue:* "Read the CardAdvisor cards.json. Normalize into the `Card` schema in schemas.py. Keep 50 cards with a mix of tiers. Fill missing fields (min_cibil, ages, allowed_employment, apr) using tier rules from policy.yaml and record them in `assumed_fields`. Load into Postgres. Output a CSV report of how many fields were assumed."
2. *Policy:* "Create policy.yaml with global gate rules, CIBIL bands, tier income minimums, limit multipliers, reason codes, and customer wording. Then write a script that generates 4 markdown policy documents from the YAML (underwriting policy, card eligibility guide, rejection reasons, how to improve your profile). Add a test that every number in the docs matches the YAML."
3. *Profiles:* "Write a seeded generator for 300 profiles with the segments and correlations I describe. Add 20 edge cases from this list [paste]. Output JSONL validated by `UserProfile`. Then a second script that makes a messy natural-language description of each profile with an LLM (varied styles, including Hinglish and missing fields)."
4. *Rule engine:* "Implement global_gate, prefilter_sql, evaluate, score, and near_miss as pure functions. Add boundary tests for every threshold at −1, 0, +1."
5. *Eval harness:* "Write run_eval.py that runs all metrics in the table and writes results.md."

I can write the full `policy.yaml` and the LangGraph skeleton next if you want. The plan would also work as a README file if you'd like it saved.