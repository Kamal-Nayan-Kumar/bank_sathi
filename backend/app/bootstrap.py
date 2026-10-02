"""Startup wiring.

`ensure_ready` makes a fresh clone work with one command: load the catalogue
into the database and ingest the policy corpus. It is idempotent, so it is safe
to call on every boot.

It deliberately does nothing that requires an API key. A missing key degrades
the embedder or the explainer, never the ability to serve.

The policy markdown is hand-written and committed. This function does not
generate it, and deliberately cannot: generated docs are only correct while
their generator and its data are in lockstep, and a hand-edited policy page is
the normal case for a real product.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.catalogue import generate_cards
from app.config import get_settings
from app.db import get_all_cards, init_db, upsert_cards
from app.rag.ingest import ingest
from app.rag.store import get_store

log = logging.getLogger(__name__)

DEFAULT_CARD_COUNT = 120

# Fail fast on a deploy that shipped without the corpus, rather than serving
# recommendations that have no policy evidence behind them.
REQUIRED_POLICY_DOCS = (
    "underwriting_policy",
    "reward_valuation",
    "rejection_reasons",
    "improving_profile",
)


def ensure_ready(card_count: int | None = None) -> dict[str, int | str]:
    settings = get_settings()
    init_db()

    existing = get_all_cards(include_inactive=True)
    if existing and card_count is None:
        cards = existing
        source = "existing"
    else:
        cards = generate_cards(card_count or DEFAULT_CARD_COUNT, settings.synthetic_seed)
        upsert_cards(
            cards,
            {
                c.card_id: [r.model_dump(mode="json") for r in c.reward_rules]
                for c in cards
            },
        )
        source = "generated"

    docs_dir = settings.resolve(settings.policy_docs_dir)
    missing = [n for n in REQUIRED_POLICY_DOCS if not (docs_dir / f"{n}.md").exists()]
    if missing:
        raise RuntimeError(
            f"policy documents missing from {docs_dir}: {missing}. "
            "They are committed to the repo; restore them before serving."
        )

    stats = ingest(docs_dir, cards)
    return {
        "cards": len(cards),
        "cards_source": source,
        "policy_docs": len(REQUIRED_POLICY_DOCS),
        "chunks": stats["chunks"],
        "store": get_store().name,
    }


def policy_dir() -> Path:
    return get_settings().resolve(get_settings().policy_docs_dir)


def force_rebuild(card_count: int | None = None) -> dict:
    """Regenerate the catalogue from the seed and re-ingest. Used by `make data`."""
    return ensure_ready(card_count or DEFAULT_CARD_COUNT)
