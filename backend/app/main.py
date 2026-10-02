"""FastAPI application.

Endpoints are thin. They validate, call the graph, and serialise. Any logic
that decides anything lives in the engine, and any test asserting a decision
belongs in test_rules.py rather than here.

Routes:
  POST /api/chat           natural language in, recommendation + prose out
  POST /api/recommend      a validated profile in, the same response out
  GET  /api/cards          browse the catalogue
  GET  /api/cards/{id}     one card's published terms
  GET  /api/profile/fields the form schema, so the UI cannot drift from Pydantic
  GET  /api/examples       ready-made profiles for the demo
  GET  /api/health         dependency status
  GET  /api/eval/results   the last evaluation run
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.config import get_settings
from app.policy import get_policy
from app.schemas import (
    CardQuery,
    NaturalLanguageRequest,
    ProfileRequest,
    RecommendationResponse,
)
from app.survey import load_examples

logging.basicConfig(level=get_settings().log_level)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    try:
        from app.bootstrap import ensure_ready

        info = ensure_ready()
        log.info("startup: %s", info)
    except Exception as exc:  # noqa: BLE001
        # A missing catalogue should surface as a clear API error on first use,
        # not as a crash loop that hides the reason.
        log.error("startup failed: %s", exc)
    yield


app = FastAPI(
    title="Bank Sathi",
    description="Credit card recommendation engine with an LLM interface.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://bank-sathi.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------- chat
class ChatReply(BaseModel):
    """Chat needs to carry the session's memory back to the client.

    The client holds the partial profile rather than the server holding a
    session table: for a single-turn-each-way demo this keeps the API
    stateless, horizontally scalable, and trivially inspectable.
    """

    response: RecommendationResponse
    session_id: str
    partial: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)


@app.post("/api/chat", response_model=ChatReply, tags=["chat"])
def chat(req: NaturalLanguageRequest) -> ChatReply:
    from app.graph.run import run_chat
    from app.intake import get_extractor
    from app.masking import mask
    from app.schemas import PartialProfile

    clean, _ = mask(req.message)
    prior = None
    response = run_chat(req, prior)

    # Re-extract on the masked text so the stored partial matches what the
    # engine saw, never what the customer typed.
    partial = get_extractor().extract(clean, prior)
    return ChatReply(
        response=response,
        session_id=req.session_id or "guest",
        partial=partial.model_dump(mode="json", exclude_none=True),
        missing_fields=response.missing_fields,
    )


# ---------------------------------------------------------------- recommend
@app.post("/api/recommend", response_model=RecommendationResponse, tags=["engine"])
def recommend(req: ProfileRequest) -> RecommendationResponse:
    from app.graph.run import run_profile

    return run_profile(req)


# ------------------------------------------------------------------- cards
@app.get("/api/cards", tags=["catalogue"])
def list_cards(query: CardQuery | None = None) -> dict:
    from app.db import get_all_cards

    query = query or CardQuery()
    cards = get_all_cards(include_inactive=True)
    if query.card_ids:
        wanted = set(query.card_ids)
        cards = [c for c in cards if c.card_id in wanted]
    return {
        "count": len(cards),
        "cards": [c.model_dump(mode="json") for c in cards[: query.limit]],
    }


@app.get("/api/cards/{card_id}", tags=["catalogue"])
def get_one_card(card_id: str) -> dict:
    from app.db import get_card

    card = get_card(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"No card with id {card_id}.")
    return card.model_dump(mode="json")


# -------------------------------------------------------------------- meta
@app.get("/api/profile/fields", tags=["meta"])
def profile_fields() -> dict:
    """The form is generated from this, so the UI cannot ask for a field
    the engine does not accept."""
    from app.schemas import SpendMix, UserProfile

    return {
        "fields": [
            {
                "name": name,
                "type": _widget(field.annotation),
                "required": field.is_required(),
                "minimum": field.metadata.get("ge"),
                "maximum": field.metadata.get("le"),
                "enum": (
                    [e.value for e in field.annotation]
                    if hasattr(field.annotation, "__members__")
                    else None
                ),
                "help": _HELP.get(name, ""),
            }
            for name, field in UserProfile.model_fields.items()
        ],
        "spend_categories": list(SpendMix.model_fields),
        "preferences": ["cashback", "travel", "lounge", "fuel", "lifetime_free"],
    }


def _widget(annotation: Any) -> str:
    text = str(annotation)
    if "Literal" in text:
        return "select"
    if "Employment" in text:
        return "select"
    if "SpendMix" in text:
        return "spend"
    if "int" in text:
        return "number"
    if "float" in text:
        return "number"
    return "text"


_HELP = {
    "monthly_income": "Take-home or gross, whichever you are comfortable sharing. We use it only to check minimum income.",
    "cibil_score": "Leave blank if you have never used credit. We will only suggest starter cards.",
    "utilization_pct": "Card balance as a percentage of your limit.",
    "monthly_spend": "Rough figures are fine. We reward what you actually spend on.",
}


@app.get("/api/examples", tags=["meta"])
def examples() -> dict:
    return load_examples()


@app.get("/api/policy", tags=["meta"])
def policy_summary() -> dict:
    """What the engine will actually check, so the UI can state it plainly."""
    p = get_policy()
    return {
        "product": p.product,
        "global_gate": p.global_gate,
        "tiers": p.tiers,
        "required_fields": p.required_fields,
        "disclaimer": p.product["disclaimer"],
    }


@app.get("/api/health", tags=["meta"])
def health() -> dict:
    s = get_settings()
    status = {
        "database": "postgres" if s.has_postgres else "sqlite-fallback",
        "vector_store": "qdrant" if s.has_qdrant else "in-process-fallback",
        "embeddings": "openai" if s.has_remote_embeddings else "hash-fallback",
        "llm": "groq" if s.has_llm else "template-fallback",
        "cards_loaded": 0,
        "policy_chunks": 0,
    }
    try:
        from app.db import get_all_cards
        from app.rag.store import get_store

        status["cards_loaded"] = len(get_all_cards(include_inactive=True))
        status["policy_chunks"] = get_store().count()
    except Exception as exc:  # noqa: BLE001
        status["error"] = str(exc)
    return status


@app.get("/api/eval/results", tags=["meta"])
def eval_results() -> dict:
    from app.config import get_settings as gs
    from pathlib import Path

    path = Path(gs().resolve("evaluation/results.json"))
    if not path.exists():
        return {"available": False, "message": "Run `make eval` first."}
    import json

    return {"available": True, **json.loads(path.read_text())}
