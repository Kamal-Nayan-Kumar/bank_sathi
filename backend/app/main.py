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
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin

from app import thresholds as T
from app.config import get_settings
from app.schemas import (
    CardQuery,
    Employment,
    NaturalLanguageRequest,
    PartialProfile,
    ProfileRequest,
    RecommendationResponse,
    UserProfile,
)
from app.survey import load_examples
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

logging.basicConfig(level=get_settings().log_level)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        from app.bootstrap import ensure_ready

        info = ensure_ready()
        log.info("startup: %s", info)
    except Exception as exc:  # noqa: BLE001
        # A missing catalogue should surface as a clear API error on first use,
        # not as a crash loop that hides the reason.
        log.error("startup failed: %s", exc)
        yield
        return

    # Warm the expensive lazy paths while nothing is waiting. Measured on a cold
    # start: ~3s for the first Neon connection and ~4s for the first Qdrant
    # round trip plus embedding-model load. Paying that on the first customer's
    # request instead of at boot is the difference between a responsive demo and
    # an apparent hang.
    _warm_up()
    yield


def _warm_up() -> None:
    """Warm the cheap paths. The embedder is deliberately excluded.

    Loading the MiniLM model at boot alongside everything else spiked past the
    512MB free-tier limit and OOM-killed the process. It loads lazily on first
    retrieval instead — one slow request rather than a dead service.
    Set WARM_EMBEDDER=1 to restore boot warming where RAM allows.
    """
    import os as _os
    import time as _time

    started = _time.perf_counter()
    stages: dict[str, float] = {}

    def timed(name: str, fn):
        t0 = _time.perf_counter()
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - never block boot on a warm-up
            log.warning("warm-up step %s failed: %s", name, exc)
        stages[name] = round((_time.perf_counter() - t0) * 1000, 1)

    if _os.environ.get("WARM_EMBEDDER", "0") == "1":
        from app.rag.store import get_embedder

        timed("embedder", lambda: get_embedder().embed(["warm up"]))
    timed("vector_store", lambda: __import__("app.rag.store", fromlist=["x"]).get_store().count())
    timed("catalogue", lambda: __import__("app.db", fromlist=["x"]).get_all_cards())
    log.info(
        "warm-up: %s (%.0fms)",
        ", ".join(f"{k}={v}ms" for k, v in stages.items()),
        (_time.perf_counter() - started) * 1000,
    )


app = FastAPI(
    title="Card Sathi",
    description="Credit card recommendation engine with an LLM interface.",
    version="0.1.0",
    lifespan=lifespan,
)

# Bound at import so `_widget` can compare annotations by identity rather than
# by string, which breaks as soon as Pydantic repr changes.
SpendMixRef = UserProfile.model_fields["monthly_spend"].annotation

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://card-sathi.vercel.app",
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

    clean, _ = mask(req.message)
    # The client sends back what we last extracted, so each turn refines the
    # same profile instead of starting over.
    prior = None
    if req.prior:
        try:
            prior = PartialProfile.model_validate(req.prior)
        except Exception as exc:  # noqa: BLE001
            log.warning("ignoring unusable prior profile: %s", exc)
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


class FollowupRequest(BaseModel):
    """A question about a recommendation that has already been made."""

    message: str = Field(min_length=1, max_length=2000)
    profile: dict[str, Any] | None = None
    history: list[dict[str, str]] = Field(default_factory=list)


@app.post("/api/followup", tags=["chat"])
def followup(req: FollowupRequest) -> dict:
    """Answer a question about an existing recommendation.

    The profile is sent back by the client rather than stored server-side. The
    tools re-run the engine against it, so a question about card X always gets
    the same answer the recommendation did.
    """
    from app.llm_tools import chat_reply
    from app.masking import mask
    from app.schemas import UserProfile

    clean, _ = mask(req.message)
    profile = None
    if req.profile:
        try:
            profile = UserProfile.model_validate(req.profile)
        except Exception:  # noqa: BLE001 - a stale client profile is not fatal
            profile = None
    return {"reply": chat_reply(clean, req.history, profile)}


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
    from app.schemas import SpendMix

    fields = []
    for name, field in UserProfile.model_fields.items():
        annotation = field.annotation
        options = None
        widget = _widget(annotation)
        if annotation is Employment or annotation == Employment:
            options = [e.value for e in Employment]
        elif get_origin(annotation) is Literal:
            # A Literal is a single value, so it is a select. A list of Literals
            # is a multiselect. Checking get_origin rather than `in str(...)`
            # is what stops `city_tier` from being misread as a multiselect and
            # keeps the raw typing repr out of the response.
            options = [str(a) for a in get_args(annotation) if a is not type(None)]
        elif get_origin(annotation) in (list, list):
            inner = get_args(annotation)
            if inner and get_origin(inner[0]) is Literal:
                options = [str(a) for a in get_args(inner[0]) if a is not type(None)]
            widget = "multiselect"
        bounds = _bounds(field)
        fields.append(
            {
                "name": name,
                "type": widget,
                "required": field.is_required(),
                "minimum": bounds.get("ge"),
                "maximum": bounds.get("le"),
                "options": options,
                "help": _HELP.get(name, ""),
            }
        )
    return {
        "fields": fields,
        "spend_categories": list(SpendMix.model_fields),
        "preferences": ["cashback", "travel", "lounge", "fuel", "lifetime_free"],
    }


_BOUND_KEYS = ("ge", "le", "gt", "lt")


_BOUND_KEYS = ("ge", "le", "gt", "lt")


def _bounds(field) -> dict:
    """Extract numeric constraints from a Pydantic field.

    In Pydantic v2 the constraints live as annotated_types markers
    (`Ge(ge=18)`) in `field.metadata`, not on the FieldInfo. Reading
    `getattr(field, "ge")` returns None for every field, which is how an input
    ends up accepting a credit score of 2500 and having the API reject it
    instead of the form.
    """
    out: dict[str, Any] = {}
    for entry in field.metadata or []:
        inner = getattr(entry, "metadata", None)
        if isinstance(inner, dict):
            out.update({k: v for k, v in inner.items() if k in _BOUND_KEYS})
        elif isinstance(entry, dict):
            out.update({k: v for k, v in entry.items() if k in _BOUND_KEYS})
        else:
            value = getattr(entry, entry.__class__.__name__.lower(), None)
            if isinstance(value, (int, float)):
                out[entry.__class__.__name__.lower()] = value
    return out


def _widget(annotation: Any) -> str:
    """Control type for a Pydantic field, derived from the annotation.

    Optional[int] must still render as a number input, so the union is
    unwrapped before deciding. Getting this wrong is how an optional credit
    score ends up as a free-text box that the engine then rejects.
    """
    if annotation is SpendMixRef:
        return "spend"
    if annotation is Employment:
        return "select"

    origin = get_origin(annotation)
    if origin is Literal:
        return "select"
    if origin in (list, list):
        return "multiselect"
    if origin in (Union, UnionType):
        for arg in get_args(annotation):
            if arg is type(None):
                continue
            return _widget(arg)
        return "text"

    if annotation is bool:
        return "checkbox"
    if annotation in (int, float):
        return "number"
    return "text"


_HELP = T.FIELD_HELP


@app.get("/api/examples", tags=["meta"])
def examples() -> dict:
    return load_examples()


@app.get("/api/policy", tags=["meta"])
def policy_summary() -> dict:
    """What the engine will actually check, so the UI can state it plainly.

    Exposed deliberately: a customer who is about to be turned down is owed the
    rule, not a vague "you don't qualify".
    """
    return {
        "product": {
            "name": T.PRODUCT_NAME,
            "tagline": T.PRODUCT_TAGLINE,
        },
        "global_gate": {
            "min_age": T.MIN_AGE,
            "max_age": T.MAX_AGE,
            "min_cibil": T.MIN_CIBIL,
            "new_to_credit_cibil": T.NEW_TO_CREDIT_CIBIL,
            "max_missed_payments_12m": T.MAX_MISSED_PAYMENTS_12M,
            "max_recent_inquiries_6m": T.MAX_RECENT_INQUIRIES_6M,
            "max_utilization_pct": T.MAX_UTILIZATION_PCT,
        },
        "tiers": {
            name: {
                "min_monthly_income": tier.min_monthly_income,
                "min_cibil": tier.min_cibil,
                "min_age": tier.min_age,
                "max_age": tier.max_age,
                "apr_pct": tier.apr_pct,
                "allowed_employment": list(tier.allowed_employment),
            }
            for name, tier in T.TIERS.items()
        },
        "required_fields": T.REQUIRED_PROFILE_FIELDS,
        "disclaimer": T.DISCLAIMER,
    }


@app.get("/api/health", tags=["meta"])
def health() -> dict:
    """What is actually running, not what is configured.

    Reported from live objects rather than from settings, because a degraded
    fallback that reports itself as the real service is worse than an outage:
    it hides the difference between a demo and a deployment.
    """
    from app import llm
    from app.db import get_all_cards
    from app.rag.store import get_store

    status = {
        "database": "postgres" if get_settings().has_postgres else "sqlite",
        "cards_loaded": 0,
        "policy_chunks": 0,
    }
    try:
        status["cards_loaded"] = len(get_all_cards(include_inactive=True))
    except Exception as exc:  # noqa: BLE001
        status["database_error"] = str(exc)[:200]

    try:
        store = get_store()
        on_qdrant = store.name == "qdrant"
        status["vector_store"] = "qdrant" if on_qdrant else "in-process"
        status["policy_chunks"] = store.count()
    except Exception as exc:  # noqa: BLE001
        on_qdrant = False
        status["vector_store"] = "unavailable"
        status["vector_store_error"] = str(exc)[:200]

    # When Qdrant embeds server-side, no local embedder is ever constructed —
    # reporting get_embedder() here would load a model just to name it.
    if on_qdrant:
        status["embeddings"] = "qdrant-inference"
    else:
        try:
            from app.rag.store import get_embedder

            embedder = get_embedder()
            status["embeddings"] = type(embedder).__name__.replace("Embedder", "").lower()
        except Exception as exc:  # noqa: BLE001
            status["embeddings"] = "unavailable"
            status["embeddings_error"] = str(exc)[:200]

    status["llm"] = " + ".join(name for name, _, _ in llm.providers()) or "template"
    return status


@app.get("/api/eval/results", tags=["meta"])
def eval_results() -> dict:
    from pathlib import Path

    from app.config import get_settings as gs

    path = Path(gs().resolve("evaluation/results.json"))
    if not path.exists():
        return {"available": False, "message": "Run `make eval` first."}
    import json

    return {"available": True, **json.loads(path.read_text())}
