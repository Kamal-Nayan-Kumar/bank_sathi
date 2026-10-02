"""Database wiring.

Small and mostly integration-shaped: the rules that matter here are about
isolation and about never quietly using a different database than the one asked
for.
"""

from __future__ import annotations

import pytest
from app.config import get_settings
from app.db import get_engine, init_db, session_scope, upsert_cards


def test_sqlite_url_is_honoured(monkeypatch, tmp_path):
    """Regression: the SQLite fallback used to ignore DATABASE_URL entirely.

    Every test therefore read and rewrote `data/bank_sathi.sqlite3`, the
    developer's real catalogue. A 60-card fixture was then compared against a
    stale 120-card table, and the failure looked like a rule bug.
    """
    from app import db as db_mod

    target = tmp_path / "explicit.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{target}")
    get_settings.cache_clear()
    db_mod.get_engine.cache_clear()
    db_mod.get_sessionmaker.cache_clear()
    try:
        assert str(get_engine().url).endswith("explicit.sqlite3")
    finally:
        db_mod.get_engine.cache_clear()
        db_mod.get_sessionmaker.cache_clear()


def test_empty_url_falls_back_to_the_local_file(monkeypatch):
    from app import db as db_mod

    monkeypatch.setenv("DATABASE_URL", "")
    get_settings.cache_clear()
    db_mod.get_engine.cache_clear()
    db_mod.get_sessionmaker.cache_clear()
    try:
        url = str(get_engine().url)
        assert url.endswith("data/bank_sathi.sqlite3")
    finally:
        db_mod.get_engine.cache_clear()
        db_mod.get_sessionmaker.cache_clear()


def test_a_malformed_url_fails_loudly(monkeypatch, tmp_path):
    """Silently falling back to a local file would look like it connected."""
    from app import db as db_mod

    monkeypatch.setenv("DATABASE_URL", "mysql://user:pw@host/db")
    get_settings.cache_clear()
    db_mod.get_engine.cache_clear()
    db_mod.get_sessionmaker.cache_clear()
    try:
        with pytest.raises(ValueError, match="DATABASE_URL"):
            get_engine()
    finally:
        db_mod.get_engine.cache_clear()
        db_mod.get_sessionmaker.cache_clear()


def test_init_db_is_idempotent(loaded_db):
    """Called on every boot, so running twice must not raise."""
    init_db()
    init_db()
    with session_scope() as db:
        from sqlalchemy import text

        assert db.execute(text("SELECT COUNT(*) FROM cards")).scalar() == len(loaded_db)


def test_upsert_replaces_rather_than_duplicating(cards, loaded_db):
    from app.db import get_all_cards

    before = len(get_all_cards(include_inactive=True))
    upsert_cards(cards, {c.card_id: [] for c in cards})
    assert len(get_all_cards(include_inactive=True)) == before


def test_orphaned_reward_rows_are_dropped(cards, loaded_db):
    """Rules are replaced as a set, so a removed card leaves nothing behind."""
    from app.db import get_all_cards
    from sqlalchemy import text

    upsert_cards(cards, {c.card_id: [] for c in cards})
    live_ids = {c.card_id for c in get_all_cards(include_inactive=True)}
    with session_scope() as db:
        orphans = db.execute(
            text("SELECT COUNT(*) FROM card_rewards WHERE card_id = 'CARD_999'")
        ).scalar()
    assert orphans == 0
    assert live_ids


def test_prefilter_query_is_parameterised(profile):
    """Profile values must never be concatenated into SQL text."""
    from app.rules.engine import candidate_filter_sql

    sql = str(candidate_filter_sql(profile))
    assert str(profile.monthly_income) not in sql
    assert str(profile.age) not in sql
    assert ":income" in sql
