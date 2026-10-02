"""Shared fixtures.

Tests run against the in-process vector store and SQLite, never against a real
Qdrant or Postgres, so `make test` needs no credentials and no network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app import thresholds as T  # noqa: E402
from app.catalogue import generate_cards  # noqa: E402
from app.db import init_db, session_scope, upsert_cards  # noqa: E402
from app.rag.ingest import ingest  # noqa: E402
from app.rag.store import InMemoryStore, set_store  # noqa: E402
from app.schemas import Employment, SpendMix, UserProfile  # noqa: E402

CARD_COUNT = 60


@pytest.fixture(scope="session")
def cards():
    return generate_cards(CARD_COUNT, seed=4242)


@pytest.fixture(scope="session")
def policy():
    """Kept as a fixture name so tests read naturally. The data is a module."""
    return T


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    """Point the app at a throwaway SQLite file and a fresh vector store.

    `EMBED_BACKEND=hash` is forced so tests never download a model and never
    make retrieval quality depend on network availability. The MiniLM path is
    covered separately in test_rag.
    """
    from app.config import get_settings

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.sqlite3'}")
    monkeypatch.setenv("EMBED_BACKEND", "hash")
    get_settings.cache_clear()
    from app import db as db_mod

    db_mod.get_engine.cache_clear()
    db_mod.get_sessionmaker.cache_clear()
    set_store(InMemoryStore())
    yield
    db_mod.get_engine.cache_clear()
    db_mod.get_sessionmaker.cache_clear()
    get_settings.cache_clear()
    set_store(None)


@pytest.fixture
def loaded_db(cards):
    init_db()
    rules = {c.card_id: [r.model_dump(mode="json") for r in c.reward_rules] for c in cards}
    upsert_cards(cards, rules)
    return cards


@pytest.fixture
def ingested(cards):
    ingest(cards=cards)
    return cards


def make_profile(**overrides) -> UserProfile:
    """A mid-career salaried customer in a tier-1 city: the happy path."""
    base = dict(
        profile_id="TEST_001",
        age=32,
        monthly_income=95_000,
        employment=Employment.SALARIED,
        city_tier=1,
        cibil_score=775,
        existing_cards=2,
        missed_payments_12m=0,
        recent_inquiries_6m=1,
        utilization_pct=22.0,
        monthly_spend=SpendMix(
            fuel=4_000,
            dining=9_000,
            groceries=7_000,
            online_shopping=14_000,
            travel=16_000,
            utilities=3_000,
            other=6_000,
        ),
        preferences=["travel", "lounge"],
    )
    base.update(overrides)
    return UserProfile(**base)


@pytest.fixture
def profile() -> UserProfile:
    return make_profile()


@pytest.fixture
def student_profile() -> UserProfile:
    return make_profile(
        profile_id="TEST_STUDENT",
        age=20,
        monthly_income=18_000,
        employment=Employment.STUDENT,
        cibil_score=None,
        existing_cards=0,
        monthly_spend=SpendMix(online_shopping=6_000, dining=3_000, groceries=4_000),
        preferences=["cashback"],
    )
