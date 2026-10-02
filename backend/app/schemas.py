"""Data contracts.

These are the shapes that cross module boundaries: the intake LLM must produce
a `PartialProfile`, the engine consumes a `UserProfile`, the catalogue is a
list of `Card`, and the API returns a `RecommendationResponse`.

Every schema here is strict. If the LLM returns an out-of-range income or an
impossible age, validation must fail loudly rather than quietly clamp.
"""

from __future__ import annotations

import typing
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

CardTier = Literal["secured", "entry", "mid", "premium"]
Preference = Literal["cashback", "travel", "lounge", "fuel", "lifetime_free"]


class Employment(StrEnum):
    """Employment types a card can be limited to.

    StrEnum rather than `str, Enum`: on 3.11+ it is the intended form, and
    `str(Employment.SALARIED)` returns the value instead of "Employment.SALARIED"
    - which matters because these strings go into prompts and SQL.
    """
    SALARIED = "salaried"
    SELF_EMPLOYED = "self_employed"
    BUSINESS_OWNER = "business_owner"
    STUDENT = "student"
    RETIRED = "retired"
    HOMEMAKER = "homemaker"


# ---------------------------------------------------------------------------
# Customer
# ---------------------------------------------------------------------------
class SpendMix(BaseModel):
    """Monthly spend in rupees, split by category."""

    model_config = ConfigDict(extra="forbid")

    fuel: int = Field(0, ge=0)
    dining: int = Field(0, ge=0)
    groceries: int = Field(0, ge=0)
    online_shopping: int = Field(0, ge=0)
    travel: int = Field(0, ge=0)
    utilities: int = Field(0, ge=0)
    other: int = Field(0, ge=0)

    def total(self) -> int:
        return sum(getattr(self, c) for c in self.__class__.model_fields)

    def as_dict(self) -> dict[str, int]:
        return {c: getattr(self, c) for c in self.__class__.model_fields}


class UserProfile(BaseModel):
    """A complete, validated customer profile. The engine's only input."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str = "anonymous"
    age: int = Field(ge=18, le=80)
    monthly_income: int = Field(ge=0)
    employment: Employment
    city_tier: Literal[1, 2, 3]
    cibil_score: int | None = Field(None, ge=300, le=900)
    existing_cards: int = Field(0, ge=0)
    missed_payments_12m: int = Field(0, ge=0)
    recent_inquiries_6m: int = Field(0, ge=0)
    utilization_pct: float | None = Field(None, ge=0, le=100)
    monthly_spend: SpendMix
    preferences: list[Preference] = Field(default_factory=list)

    @model_validator(mode="after")
    def _spend_cannot_exceed_income(self) -> UserProfile:
        # A customer spending 3x their income is a data error, not a rich
        # spender. Allowing it through would silently distort net value.
        total = self.monthly_spend.total()
        if self.monthly_income > 0 and total > self.monthly_income * 3:
            raise ValueError(
                f"monthly spend {total} is implausible against income {self.monthly_income}"
            )
        return self

    @property
    def is_new_to_credit(self) -> bool:
        return self.cibil_score is None or self.existing_cards == 0


class PartialProfile(BaseModel):
    """What the intake LLM is allowed to return: everything optional.

    Separation matters. If the LLM had to produce a `UserProfile`, a single
    missing field would fail the whole extraction and we could never ask a
    partial follow-up question.
    """

    model_config = ConfigDict(extra="forbid")

    age: int | None = Field(None, ge=0, le=120)
    monthly_income: int | None = Field(None, ge=0)
    employment: Employment | None = None
    city_tier: Literal[1, 2, 3] | None = None
    cibil_score: int | None = Field(None, ge=300, le=900)
    existing_cards: int | None = Field(None, ge=0)
    missed_payments_12m: int | None = Field(None, ge=0)
    recent_inquiries_6m: int | None = Field(None, ge=0)
    utilization_pct: float | None = Field(None, ge=0, le=100)
    monthly_spend: SpendMix | None = None
    max_annual_fee: int | None = Field(None, ge=0)
    preferences: list[Preference] | None = None

    def merged_with(self, prior: PartialProfile) -> PartialProfile:
        """Later conversation turns only fill blanks, they never erase.

        exclude_none matters: dumping every field would write the unset ones
        back as null and wipe what the customer told us on the previous turn,
        so a chat could never accumulate a profile no matter how long it ran.
        """
        return prior.model_copy(update=self.model_dump(exclude_none=True))


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------
class RewardRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    # Effective percentage back already converted from points. Storing the
    # effective value keeps the scoring engine free of point-value arithmetic.
    value_pct: float = Field(ge=0)
    monthly_cap_rs: int | None = Field(None, ge=0)


class Card(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card_id: str
    name: str
    bank: str
    network: Literal["Visa", "Mastercard", "RuPay", "Amex"] = "Visa"
    segment: str
    tier: CardTier
    annual_fee: int = Field(ge=0)
    joining_fee: int = Field(0, ge=0)
    fee_waiver_spend: int | None = Field(None, ge=0)
    min_monthly_income: int = Field(ge=0)
    min_cibil: int | None = Field(None, ge=300, le=900)
    min_age: int = Field(21, ge=18)
    max_age: int = Field(70, ge=21)
    allowed_employment: list[Employment]
    apr_pct: float = Field(ge=0)
    lounge_visits_per_year: int = Field(0, ge=0)
    reward_rules: list[RewardRule]
    benefit_highlights: list[str] = Field(default_factory=list)
    is_active: bool = True
    # Fields we filled in from tier defaults rather than a published source.
    assumed_fields: list[str] = Field(default_factory=list)
    source: str = "synthetic"

    @model_validator(mode="after")
    def _age_window_is_sane(self) -> Card:
        if self.max_age < self.min_age:
            raise ValueError(f"{self.card_id}: max_age below min_age")
        return self

    def rule_for(self, category: str) -> RewardRule | None:
        for r in self.reward_rules:
            if r.category == category:
                return r
        return None


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------
class Reason(BaseModel):
    """One failed rule, with enough detail to render it and to fix it."""

    model_config = ConfigDict(extra="forbid")

    code: str
    label: str
    message: str
    actual: float | str | None = None
    required: float | str | None = None
    # How far off the customer is, in the unit of `required`. None when the gap
    # is not meaningful (e.g. employment type).
    gap: float | None = None
    fixable: bool = True
    # A near miss is a failure we expect the customer to clear with time.
    near_miss: bool = False


class GateResult(BaseModel):
    passed: bool
    reasons: list[Reason] = Field(default_factory=list)


class CardEvaluation(BaseModel):
    card_id: str
    eligible: bool
    reasons: list[Reason] = Field(default_factory=list)


class Recommendation(BaseModel):
    rank: int
    card_id: str
    name: str
    bank: str
    tier: CardTier
    score: float = Field(ge=0)
    net_annual_value_rs: int
    est_credit_limit_rs: int
    apr_pct: float
    fee_payable_rs: int
    key_benefits: list[str] = Field(default_factory=list)
    why: str = ""
    sources: list[str] = Field(default_factory=list)


class ScoreBreakdown(BaseModel):
    """Kept so the UI can explain the ranking instead of asserting it."""

    card_id: str
    net_value: float
    spend_alignment: float
    preference_match: float
    fee_fit: float
    total: float


class VerifierReport(BaseModel):
    passed: bool
    checks: list[dict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    retries: int = 0
    used_fallback: bool = False


class PolicyEvidence(BaseModel):
    source: str
    card_id: str | None = None
    section: str
    text: str
    score: float = 0.0


class RecommendationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    status: Literal[
        "recommendations_available",
        "no_matching_cards",
        "need_more_information",
        "profile_rejected",
    ]
    decision: Literal["recommended", "rejected", "pending"] = "pending"
    profile: UserProfile | None = None
    recommendations: list[Recommendation] = Field(default_factory=list)
    # Cards the customer failed, with reasons. Capped, not exhaustive.
    rejections: list[CardEvaluation] = Field(default_factory=list)
    rejected_cards: list[Reason] = Field(default_factory=list)
    near_miss: list[CardEvaluation] = Field(default_factory=list)
    improvement_steps: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    question: str | None = None
    summary: str = ""
    evidence: list[PolicyEvidence] = Field(default_factory=list)
    score_breakdown: list[ScoreBreakdown] = Field(default_factory=list)
    verifier: VerifierReport | None = None
    trace: dict[str, float] = Field(default_factory=dict)
    # The intake result, forwarded so the chat route can persist exactly what
    # the engine decided on. Deliberately not part of the serialised contract
    # the customer sees.
    _partial: typing.Any = PrivateAttr(default=None)
    partial: PartialProfile | None = Field(default=None, exclude=True)


# ---------------------------------------------------------------------------
# API request bodies
# ---------------------------------------------------------------------------
class NaturalLanguageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    # What the engine already knows, sent back by the client. The API holds no
    # server-side sessions, so without this every follow-up turn starts from
    # nothing and the conversation can never converge: the customer answers one
    # question and the engine forgets the three they gave earlier.
    prior: dict[str, typing.Any] | None = None


class ProfileRequest(BaseModel):
    profile: UserProfile


class CardQuery(BaseModel):
    card_ids: list[str] | None = None
    limit: int = Field(20, ge=1, le=200)
