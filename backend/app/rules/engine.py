"""The decision engine.

This module is the system. Everything else is presentation.

Three rules govern what lives here:

1. The LLM does not appear anywhere in this file, or anything it calls.
2. No threshold is a literal. Every number comes from `app.thresholds`.
3. Every failure carries a reason code and, where meaningful, a gap, so the
   same function that rejects a card also explains how far off it was.
"""

from __future__ import annotations

from app import thresholds as T
from app.schemas import (
    Card,
    CardEvaluation,
    GateResult,
    Reason,
    UserProfile,
)


# ------------------------------------------------------------------ helpers
def _reason(
    code: str,
    *,
    actual=None,
    required=None,
    gap=None,
    fixable=True,
    near_miss=False,
) -> Reason:
    return Reason(
        code=code,
        label=T.label(code),
        message=T.reason(code, required=required, actual=actual, gap=gap),
        actual=actual,
        required=required,
        gap=gap,
        fixable=fixable,
        near_miss=near_miss,
    )


# ------------------------------------------------------------------ gate
def global_gate(profile: UserProfile) -> GateResult:
    """Company-wide screening. Failing here means no card at all is recommended.

    Run before SQL. These are the checks where a rejected customer should not
    make us scan the whole catalogue to reach the same answer.
    """
    reasons: list[Reason] = []

    if profile.age < T.MIN_AGE:
        reasons.append(
            _reason(
                "AGE_BELOW_MIN",
                actual=profile.age,
                required=T.MIN_AGE,
                gap=T.MIN_AGE - profile.age,
            )
        )
    elif profile.age > T.MAX_AGE:
        reasons.append(
            _reason(
                "AGE_ABOVE_MAX",
                actual=profile.age,
                required=T.MAX_AGE,
                gap=profile.age - T.MAX_AGE,
            )
        )

    # No credit history is not a rejection: new-to-credit customers are handled
    # by tier filtering plus an advisory reason. MIN_CIBIL is the floor for
    # customers who do have a score.
    if profile.cibil_score is not None and profile.cibil_score < T.MIN_CIBIL:
        reasons.append(
            _reason(
                "CIBIL_BELOW_COMPANY_MIN",
                actual=profile.cibil_score,
                required=T.MIN_CIBIL,
                gap=T.MIN_CIBIL - profile.cibil_score,
            )
        )

    if profile.missed_payments_12m > T.MAX_MISSED_PAYMENTS_12M:
        reasons.append(
            _reason(
                "MISSED_PAYMENTS_PRESENT",
                actual=profile.missed_payments_12m,
                required=T.MAX_MISSED_PAYMENTS_12M,
                gap=profile.missed_payments_12m - T.MAX_MISSED_PAYMENTS_12M,
            )
        )

    if profile.recent_inquiries_6m > T.MAX_RECENT_INQUIRIES_6M:
        reasons.append(
            _reason(
                "TOO_MANY_RECENT_INQUIRIES",
                actual=profile.recent_inquiries_6m,
                required=T.MAX_RECENT_INQUIRIES_6M,
                gap=profile.recent_inquiries_6m - T.MAX_RECENT_INQUIRIES_6M,
            )
        )

    if (
        profile.utilization_pct is not None
        and profile.utilization_pct > T.MAX_UTILIZATION_PCT
    ):
        reasons.append(
            _reason(
                "UTILIZATION_TOO_HIGH",
                actual=round(profile.utilization_pct, 1),
                required=T.MAX_UTILIZATION_PCT,
                gap=round(profile.utilization_pct - T.MAX_UTILIZATION_PCT, 1),
            )
        )

    if profile.monthly_income <= 0:
        reasons.append(_reason("INCOME_NOT_DECLARED", actual=0, required=1))

    return GateResult(passed=not reasons, reasons=reasons)


def is_new_to_credit(profile: UserProfile) -> bool:
    """Limited credit history: no score, or a score we treat as thin."""
    if profile.cibil_score is None:
        return True
    return profile.cibil_score <= T.NEW_TO_CREDIT_CIBIL


# ------------------------------------------------------------------ per card
def evaluate_card(profile: UserProfile, card: Card) -> CardEvaluation:
    """Every rule, for one card. No early exit.

    A customer should see all the reasons a card failed, not just the first
    one: otherwise "you were close" advice is wrong, because clearing one
    blocker may simply reveal the next.
    """
    reasons: list[Reason] = []

    if not card.is_active:
        reasons.append(_reason("CARD_INACTIVE", required=card.card_id, fixable=False))

    if profile.age < card.min_age:
        gap = card.min_age - profile.age
        reasons.append(
            _reason(
                "AGE_BELOW_MIN",
                actual=profile.age,
                required=card.min_age,
                gap=gap,
                near_miss=gap <= T.NEAR_MISS_AGE_SLACK_YEARS,
            )
        )
    elif profile.age > card.max_age:
        reasons.append(
            _reason(
                "AGE_ABOVE_MAX",
                actual=profile.age,
                required=card.max_age,
                gap=profile.age - card.max_age,
            )
        )

    if profile.monthly_income < card.min_monthly_income:
        gap = card.min_monthly_income - profile.monthly_income
        slack = (
            card.min_monthly_income * T.NEAR_MISS_INCOME_SLACK_RATIO
            + T.NEAR_MISS_INCOME_SLACK_ABSOLUTE
        )
        reasons.append(
            _reason(
                "INCOME_BELOW_MIN",
                actual=profile.monthly_income,
                required=card.min_monthly_income,
                gap=gap,
                near_miss=profile.monthly_income >= slack,
            )
        )

    # A null card minimum means no score requirement; a null customer score is
    # handled by the tier filter, not here, so secured cards stay openable.
    if card.min_cibil is not None and profile.cibil_score is not None:
        if profile.cibil_score < card.min_cibil:
            gap = card.min_cibil - profile.cibil_score
            reasons.append(
                _reason(
                    "CIBIL_BELOW_MIN",
                    actual=profile.cibil_score,
                    required=card.min_cibil,
                    gap=gap,
                    near_miss=gap <= T.NEAR_MISS_CIBIL_SLACK,
                )
            )

    if profile.employment not in card.allowed_employment:
        reasons.append(
            _reason(
                "EMPLOYMENT_NOT_ALLOWED",
                actual=profile.employment.value,
                required=", ".join(e.value for e in card.allowed_employment),
                fixable=False,
            )
        )

    return CardEvaluation(card_id=card.card_id, eligible=not reasons, reasons=reasons)


def near_misses(evaluations: list[CardEvaluation]) -> list[CardEvaluation]:
    """Cards the customer failed on one small, fixable margin."""
    if not T.NEAR_MISS_ENABLED:
        return []
    out: list[CardEvaluation] = []
    for ev in evaluations:
        if ev.eligible:
            continue
        fixable = [r for r in ev.reasons if r.near_miss and r.fixable]
        # Every failure must be a near miss, not merely one of them: a customer
        # who is short on income *and* credit score is not "almost there".
        if (
            len(ev.reasons) <= T.NEAR_MISS_MAX_FAILED_RULES
            and len(fixable) == len(ev.reasons)
            and all(r.code in T.NEAR_MISS_REASON_CODES for r in ev.reasons)
        ):
            out.append(ev)
    out.sort(key=lambda e: (sum(r.gap or 0 for r in e.reasons), e.card_id))
    return out


def improvement_steps(
    near: list[CardEvaluation],
    profile: UserProfile | None = None,
    limit: int = 4,
) -> list[str]:
    """Concrete, gap-shaped advice. Never "take a loan to raise your score"."""
    steps: list[str] = []
    seen: set[str] = set()

    # New-to-credit customers get the starter-card route before any gap maths,
    # because "earn Rs 6,000 more" is not their blocker.
    if profile is not None and is_new_to_credit(profile):
        msg = T.improve("NEW_TO_CREDIT")
        if msg and msg not in seen:
            seen.add(msg)
            steps.append(msg)

    for ev in near:
        for r in ev.reasons:
            msg = T.improve(r.code, required=r.required, gap=r.gap)
            if msg and msg not in seen:
                seen.add(msg)
                steps.append(msg)
    return steps[:limit]


# ------------------------------------------------------------------ SQL
def candidate_filter_sql(profile: UserProfile):
    """Parameterised prefilter.

    The profile supplies bound values only. There is no path by which a model
    can reach this SQL, which is the whole point of writing it by hand.
    """
    from sqlalchemy import text

    # `CAST(:cibil AS INTEGER)` rather than a bare `:cibil IS NULL`: Postgres
    # cannot infer a type for an untyped NULL bind parameter, so a customer with
    # no credit score yet raised "could not determine data type of parameter" and
    # the whole turn died. The cast is what makes the IS NULL branch legal.
    return text(
        """
        SELECT card_id
        FROM cards
        WHERE is_active = :is_active
          AND min_monthly_income <= :income
          AND min_age <= :age AND max_age >= :age
          AND (
                min_cibil IS NULL
                OR CAST(:cibil AS INTEGER) IS NULL
                OR min_cibil <= CAST(:cibil AS INTEGER)
              )
          AND (:new_to_credit = 0 OR min_monthly_income <= :tier_cap)
        """
    )


def candidate_params(profile: UserProfile) -> dict:
    ntc = is_new_to_credit(profile)
    return {
        "is_active": True,
        "income": profile.monthly_income,
        "age": profile.age,
        "cibil": profile.cibil_score,
        "new_to_credit": int(ntc),
        # New-to-credit customers are capped at the entry tier's income floor, so
        # a premium card they could not open never reaches the rule engine.
        "tier_cap": T.TIERS["entry"].min_monthly_income,
    }


def fetch_candidates(db, profile: UserProfile) -> list[str]:
    """Run the prefilter.

    Employment stays out of the SQL because it is a list membership check on a
    small row count; `evaluate_card` applies it immediately after, which keeps
    exactly one authoritative implementation of that rule.
    """
    rows = db.execute(candidate_filter_sql(profile), candidate_params(profile)).scalars()
    return list(rows)
