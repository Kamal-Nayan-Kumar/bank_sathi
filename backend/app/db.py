"""Postgres access for the structured card catalogue.

Cards live in SQL, not in the vector store. "min_income <= 45000" is an exact
numeric comparison; embeddings are the wrong tool for it and make the answer
unfalsifiable.

Falls back to local SQLite so the system boots and tests with no DATABASE_URL.
The schema is plain SQL in schema.sql and is written to run on both engines.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from sqlalchemy import JSON, Boolean, Integer, String, Text, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import get_settings
from app.schemas import Card

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cards (
    card_id              VARCHAR(16) PRIMARY KEY,
    name                 VARCHAR(120)  NOT NULL,
    bank                 VARCHAR(80)   NOT NULL,
    network              VARCHAR(16)   NOT NULL,
    segment              VARCHAR(32)   NOT NULL,
    tier                 VARCHAR(16)   NOT NULL,
    annual_fee           INTEGER       NOT NULL,
    joining_fee          INTEGER       NOT NULL DEFAULT 0,
    fee_waiver_spend     INTEGER       NULL,
    min_monthly_income   INTEGER       NOT NULL,
    min_cibil            INTEGER       NULL,
    min_age              INTEGER       NOT NULL,
    max_age              INTEGER       NOT NULL,
    apr_pct              FLOAT         NOT NULL,
    lounge_visits_year   INTEGER       NOT NULL DEFAULT 0,
    is_active            BOOLEAN       NOT NULL DEFAULT TRUE,
    assumed_fields       JSON          NOT NULL DEFAULT '[]',
    source               VARCHAR(64)   NOT NULL DEFAULT 'synthetic'
);

-- The prefilter hits min_income / min_cibil / age on every request, so those
-- get composite indexes. An index on a low-cardinality column like tier alone
-- would not be used by the planner.
CREATE INDEX IF NOT EXISTS ix_cards_prefilter
    ON cards (is_active, min_monthly_income, min_cibil);
CREATE INDEX IF NOT EXISTS ix_cards_age ON cards (min_age, max_age);

-- Reward rates sit beside the card as a JSON payload: the scoring pass needs
-- them in the same round trip, and a normalised table would add a join per
-- card per request for no benefit at this catalogue size.
CREATE TABLE IF NOT EXISTS card_rewards (
    card_id  VARCHAR(16) PRIMARY KEY,
    payload  TEXT NOT NULL
);
"""


class Base(DeclarativeBase):
    pass


class CardRewardRow(Base):
    """Reward rates as a JSON payload, one row per card.

    Declared as a model rather than raw SQL so the table is part of
    `Base.metadata` and `init_db` can create it on either engine.
    """

    __tablename__ = "card_rewards"

    card_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


class CardRow(Base):
    __tablename__ = "cards"

    card_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    bank: Mapped[str] = mapped_column(String(80))
    network: Mapped[str] = mapped_column(String(16))
    segment: Mapped[str] = mapped_column(String(32))
    tier: Mapped[str] = mapped_column(String(16))
    annual_fee: Mapped[int] = mapped_column(Integer)
    joining_fee: Mapped[int] = mapped_column(Integer, default=0)
    fee_waiver_spend: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_monthly_income: Mapped[int] = mapped_column(Integer)
    min_cibil: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_age: Mapped[int] = mapped_column(Integer)
    max_age: Mapped[int] = mapped_column(Integer)
    apr_pct: Mapped[float] = mapped_column()
    lounge_visits_year: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    assumed_fields: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(64), default="synthetic")

    def to_card(self, reward_rules: list[dict[str, Any]]) -> Card:
        return Card(
            card_id=self.card_id,
            name=self.name,
            bank=self.bank,
            network=self.network,
            segment=self.segment,
            tier=self.tier,
            annual_fee=self.annual_fee,
            joining_fee=self.joining_fee,
            fee_waiver_spend=self.fee_waiver_spend,
            min_monthly_income=self.min_monthly_income,
            min_cibil=self.min_cibil,
            min_age=self.min_age,
            max_age=self.max_age,
            allowed_employment=[],
            apr_pct=self.apr_pct,
            lounge_visits_per_year=self.lounge_visits_year,
            reward_rules=reward_rules,
            is_active=self.is_active,
            assumed_fields=list(self.assumed_fields or []),
            source=self.source,
        )


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    s = get_settings()
    if s.has_postgres:
        url = s.database_url
        kwargs: dict[str, Any] = {
            "pool_size": s.db_pool_size,
            "max_overflow": s.db_max_overflow,
            "pool_pre_ping": True,  # Neon closes idle connections aggressively
        }
    else:
        # SQLite fallback: no pooling, and check_same_thread=False because
        # FastAPI serves requests from a thread pool.
        from app.policy import REPO_ROOT

        url = f"sqlite:///{REPO_ROOT / 'data' / 'bank_sathi.sqlite3'}"
        kwargs = {"connect_args": {"check_same_thread": False}}
    eng = create_engine(url, **kwargs)
    if s.has_postgres:
        with eng.connect() as conn:
            # A hung query on a serverless Postgres is worse than a failed one.
            conn.execute(text(f"SET statement_timeout = {s.db_statement_timeout_ms}"))
        _pgvector_addon(eng)
    return eng


def _pgvector_addon(eng: Engine) -> None:
    """Enable pgvector if the provider has it. Not required by the pipeline."""
    with eng.connect() as conn:
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception:  # noqa: BLE001 - provider without the extension
            pass


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    s = get_sessionmaker()
    db = s()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Create tables and indexes from SCHEMA_SQL.

    Written as explicit DDL rather than `metadata.create_all` so the same text
    can be run by hand against Neon without going through the ORM.
    """
    eng = get_engine()
    with eng.begin() as conn:
        for stmt in SCHEMA_SQL.split(";"):
            if stmt.strip():
                conn.execute(text(stmt))


def upsert_cards(cards: list[Card], rules_by_card: dict[str, list[dict]]) -> int:
    """Idempotent load. Rewriting the catalogue is a normal dev action."""
    rows = []
    for c in cards:
        rows.append(
            {
                "card_id": c.card_id,
                "name": c.name,
                "bank": c.bank,
                "network": c.network,
                "segment": c.segment,
                "tier": c.tier,
                "annual_fee": c.annual_fee,
                "joining_fee": c.joining_fee,
                "fee_waiver_spend": c.fee_waiver_spend,
                "min_monthly_income": c.min_monthly_income,
                "min_cibil": c.min_cibil,
                "min_age": c.min_age,
                "max_age": c.max_age,
                "apr_pct": c.apr_pct,
                "lounge_visits_year": c.lounge_visits_per_year,
                "is_active": c.is_active,
                "assumed_fields": c.assumed_fields,
                "source": c.source,
            }
        )
    with session_scope() as db:
        for row in rows:
            existing = db.get(CardRow, row["card_id"])
            if existing:
                for k, v in row.items():
                    setattr(existing, k, v)
            else:
                db.add(CardRow(**row))
    # Reward rules live beside the card row as JSON: one query returns the card
    # and its rates together, and a normalised rewards table would need a join
    # on every scoring pass for a dataset this small.
    _write_rules(rules_by_card)
    return len(rows)


def _write_rules(rules_by_card: dict[str, list[dict]]) -> None:
    """Replace reward payloads wholesale.

    Upserting rules per row would leave orphaned rules for cards that were
    regenerated away. The catalogue is regenerated as a set, so it is replaced
    as a set.
    """
    from sqlalchemy import delete

    with session_scope() as db:
        db.execute(delete(CardRewardRow))
        for card_id, rules in rules_by_card.items():
            db.add(CardRewardRow(card_id=card_id, payload=_json(rules)))


def _json(value: Any) -> str:
    import json

    return json.dumps(value)


def get_card(card_id: str) -> Card | None:
    from sqlalchemy import select

    with session_scope() as db:
        row = db.get(CardRow, card_id)
        if row is None:
            return None
        rules = _load_rules(db, [card_id]).get(card_id, [])
        return _rehydrate(row, rules)


def get_all_cards(include_inactive: bool = False) -> list[Card]:
    from sqlalchemy import select

    with session_scope() as db:
        stmt = select(CardRow)
        if not include_inactive:
            stmt = stmt.where(CardRow.is_active.is_(True))
        rows = list(db.execute(stmt).scalars())
        rules = _load_rules(db, [r.card_id for r in rows])
    return [_rehydrate(r, rules.get(r.card_id, [])) for r in rows]


def _load_rules(db: Session, card_ids: list[str]) -> dict[str, list[dict]]:
    if not card_ids:
        return {}
    import json

    out: dict[str, list[dict]] = {}
    # Chunked because SQLite caps bound parameters (999) and a 500-card
    # catalogue would exceed it in one IN clause.
    for i in range(0, len(card_ids), 500):
        chunk = card_ids[i : i + 500]
        placeholders = ",".join(f":c{j}" for j in range(len(chunk)))
        params = {f"c{j}": cid for j, cid in enumerate(chunk)}
        rows = db.execute(
            text(f"SELECT card_id, payload FROM card_rewards WHERE card_id IN ({placeholders})"),
            params,
        ).all()
        for cid, payload in rows:
            out[cid] = json.loads(payload)
    return out


def _rehydrate(row: CardRow, rules: list[dict]) -> Card:
    """Rebuild a Card, restoring employment from the tier default.

    allowed_employment is tier-wide in our synthetic catalogue, so it is
    derived from policy.yaml on read rather than duplicated per row. That keeps
    a policy change from silently disagreeing with the database.
    """
    from app.policy import get_policy
    from app.schemas import Employment

    cfg = get_policy().tier(row.tier)
    return Card(
        card_id=row.card_id,
        name=row.name,
        bank=row.bank,
        network=row.network,
        segment=row.segment,
        tier=row.tier,
        annual_fee=row.annual_fee,
        joining_fee=row.joining_fee,
        fee_waiver_spend=row.fee_waiver_spend,
        min_monthly_income=row.min_monthly_income,
        min_cibil=row.min_cibil,
        min_age=row.min_age,
        max_age=row.max_age,
        allowed_employment=[Employment(e) for e in cfg["allowed_employment"]],
        apr_pct=row.apr_pct,
        lounge_visits_per_year=row.lounge_visits_year,
        reward_rules=rules,
        is_active=row.is_active,
        assumed_fields=list(row.assumed_fields or []),
        source=row.source,
    )
