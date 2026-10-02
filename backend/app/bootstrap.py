"""Startup wiring.

`ensure_ready` makes a fresh clone work with one command: generate the
catalogue, load the database, ingest the policy corpus. It is idempotent, so it
is safe to call on every boot.

It deliberately does nothing that requires an API key. A missing key degrades
the vector store or the explainer, never the ability to serve.
"""

from __future__ import annotations

import logging

from app.catalogue import generate_cards
from app.config import get_settings
from app.db import get_all_cards, init_db, upsert_cards
from app.docs_gen import assert_docs_consistent, generate_policy_docs, write_policy_docs
from app.rag.ingest import ingest
from app.rag.store import get_store

log = logging.getLogger(__name__)

DEFAULT_CARD_COUNT = 120


def ensure_ready(card_count: int | None = None) -> dict[str, int | str]:
    settings = get_settings()
    docs = dict(generate_policy_docs())
    # Fail loudly here rather than shipping a corpus that contradicts the rules.
    assert_docs_consistent(docs)

    init_db()
    existing = get_all_cards(include_inactive=True)
    if existing and card_count is None:
        cards = existing
        loaded = "existing"
    else:
        cards = generate_cards(card_count or DEFAULT_CARD_COUNT, settings.synthetic_seed)
        upsert_cards(
            cards,
            {c.card_id: [r.model_dump(mode="json") for r in c.reward_rules] for c in cards},
        )
        loaded = "generated"

    docs_dir = settings.resolve(settings.policy_docs_dir)
    written = write_policy_docs(docs_dir)
    stats = ingest(docs_dir, cards)

    return {
        "cards": len(cards),
        "cards_source": loaded,
        "policy_docs": len(written),
        "chunks": stats["chunks"],
        "store": get_store().name,
    }


def force_rebuild(card_count: int | None = None) -> dict:
    """Regenerate everything from the seed. Used by `make data`."""
    return ensure_ready(card_count or DEFAULT_CARD_COUNT)
