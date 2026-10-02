"""The decision engine.

This module is the system. Everything else is presentation.

Three rules govern what lives here:

1. The LLM does not appear anywhere in this file, or anything it calls.
2. No threshold is a literal. Every number is read from policy.yaml.
3. Every failure carries a reason code and, where meaningful, a gap, so the
   same function that rejects a card also explains how far off it was.
"""

from __future__ import annotations

from app.policy import Policy, get_policy
from app.schemas import (
    Card,
    CardEvaluation,
    Employment,
    GateResult,
    Reason,
    UserProfile,
)

# ------------------------------------------------------------------ helpers


def _reason(
    policy: Policy,
    code: str,
    *,
    actual=None,
    required=None,
    gap=None,
    fixable=True,
    near_miss=False,
    **fmt,
) -> Reason:
    return Reason(
        code=code,
        label=policy.label(code),
        message=policy.message(code, required=required, actual=actual, **fmt),
        actual=actual,
        required=required,
        gap=gap,
        fixable=fixable,
        near_miss=near_miss,
    )


def _near_miss_reason(policy: Policy, code: str, *, required, gap, **kw) -> Reason:
    return _reason(
        policy, code, required=required, gap=gap, near_miss=True, fixable=True, **kw
    )


# ------------------------------------------------------------------ gate
def global_gate(profile: UserProfile, policy: Policy | None = None) -> GateResult:
    """Company-wide screening. Failing here means no card at all is recommended.

    Run before SQL. These are the checks where a rejected customer should not
    make us page through 120 cards to reach the same answer.
    """
    policy = policy or get_policy()
    reasons: list[Reason] = []

    if profile.age < policy.min_age:
        reasons.append(
            _reason(
                policy,
                "AGE_BELOW_MIN",
                actual=profile.age,
                required=policy.min_age,
                gap=policy.min_age - profile.age,
            )
        )
    elif profile.age > policy.max_age:
        reasons.append(
            _reason(
                policy,
                "AGE_ABOVE_MAX",
                actual=profile.age,
                required=policy.max_age,
                gap=profile.age - policy.max_age,
            )
        )

    # A customer with no credit history is not rejected; `min_cibil` is the
    # floor for customers who *have* a score, and new-to-credit customers are
    # handled by tier filtering plus an advisory reason.
    if profile.cibil_score is not None and profile.cibil_score < policy.min_cibil:
        reasons.append(
            _reason(
                policy,
                "CIBIL_BELOW_COMPANY_MIN",
                actual=profile.cibil_score,
                required=policy.min_cibil,
                gap=policy.min_cibil - profile.cibil_score,
            )
        )

    if profile.missed_payments_12m > policy.max_missed_payments_12m:
        reasons.append(
            _reason(
                policy,
                "MISSED_PAYMENTS_PRESENT",
                actual=profile.missed_payments_12m,
                required=policy.max_missed_payments_12m,
                gap=profile.missed_payments_12m - policy.max_missed_payments_12m,
            )
        )

    if profile.recent_inquiries_6m > policy.max_recent_inquiries_6m:
        reasons.append(
            _reason(
                policy,
                "TOO_MANY_RECENT_INQUIRIES",
                actual=profile.recent_inquiries_6m,
                required=policy.max_recent_inquiries_6m,
                gap=profile.recent_inquiries_6m - policy.max_recent_inquiries_6m,
            )
        )

    if (
        profile.utilization_pct is not None
        and profile.utilization_pct > policy.max_utilization_pct
    ):
        reasons.append(
            _reason(
                policy,
                "UTILIZATION_TOO_HIGH",
                actual=round(profile.utilization_pct, 1),
                required=policy.max_utilization_pct,
                gap=round(profile.utilization_pct - policy.max_utilization_pct, 1),
            )
        )

    if profile.monthly_income <= 0:
        reasons.append(_reason(policy, "INCOME_NOT_DECLARED", actual=0, required=1))

    return GateResult(passed=not reasons, reasons=reasons)


def is_new_to_credit(profile: UserProfile, policy: Policy | None = None) -> bool:
    policy = policy or get_policy()
    if profile.cibil_score is None:
        return True
    return profile.cibil_score <= policy.new_to_credit_cibil


# ------------------------------------------------------------------ per card
def evaluate_card(
    profile: UserProfile, card: Card, policy: Policy | None = None
) -> CardEvaluation:
    """Every rule, for one card. No early exit.

    A customer should see all the reasons a card failed, not just the first
    one, otherwise "you were close" advice is wrong: fixing one blocker may
    simply reveal the next.
    """
    policy = policy or get_policy()
    reasons: list[Reason] = []

    if not card.is_active:
        reasons.append(_reason(policy, "CARD_INACTIVE", required=card.card_id))

    if profile.age < card.min_age:
        gap = card.min_age - profile.age
        reasons.append(
            _near_miss_reason(
                policy, "AGE_BELOW_MIN", required=card.min_age, gap=gap, actual=profile.age
            )
            if gap <= policy.nm_age_slack_years
            else _reason(
                policy, "AGE_BELOW_MIN", actual=profile.age, required=card.min_age, gap=gap
            )
        )
    elif profile.age > card.max_age:
        reasons.append(
            _reason(
                policy,
                "AGE_ABOVE_MAX",
                actual=profile.age,
                required=card.max_age,
                gap=profile.age - card.max_age,
            )
        )

    if profile.monthly_income < card.min_monthly_income:
        gap = card.min_monthly_income - profile.monthly_income
        slack = card.min_monthly_income * policy.nm_income_slack_ratio + policy.nm_income_slack_absolute
        reasons.append(
            _near_miss_reason(
                policy,
                "INCOME_BELOW_MIN",
                required=card.min_monthly_income,
                gap=gap,
                actual=profile.monthly_income,
            )
            if profile.monthly_income >= slack
            else _reason(
                policy,
                "INCOME_BELOW_MIN",
                actual=profile.monthly_income,
                required=card.min_monthly_income,
                gap=gap,
            )
        )

    # A null card minimum means no CIBIL requirement; a null customer score is
    # handled by the tier filter, not here.
    if card.min_cibil is not None and profile.cibil_score is not None:
        if profile.cibil_score < card.min_cibil:
            gap = card.min_cibil - profile.cibil_score
            reasons.append(
                _near_miss_reason(
                    policy,
                    "CIBIL_BELOW_MIN",
                    required=card.min_cibil,
                    gap=gap,
                    actual=profile.cibil_score,
                )
                if gap <= policy.nm_cibil_absolute_slack
                else _reason(
                    policy,
                    "CIBIL_BELOW_MIN",
                    actual=profile.cibil_score,
                    required=card.min_cibil,
                    gap=gap,
                )
            )

    if profile.employment not in card.allowed_employment:
        reasons.append(
            _reason(
                policy,
                "EMPLOYMENT_NOT_ALLOWED",
                actual=profile.employment.value,
                required=", ".join(e.value for e in card.allowed_employment),
                fixable=False,
            )
        )

    return CardEvaluation(
        card_id=card.card_id,
        eligible=not reasons,
        reasons=reasons,
    )


def near_misses(
    evaluations: list[CardEvaluation],
    cards_by_id: dict[str, Card],
    policy: Policy | None = None,
) -> list[CardEvaluation]:
    """Cards the customer failed on one small, fixable margin."""
    policy = policy or get_policy()
    if not policy.near_miss.get("enabled", True):
        return []
    out: list[CardEvaluation] = []
    for ev in evaluations:
        if ev.eligible:
            continue
        fixable = [r for r in ev.reasons if r.near_miss and r.fixable]
        # All failures must be near misses, not just one of them.
        if len(ev.reasons) <= policy.nm_max_failed_rules and len(fixable) == len(ev.reasons):
            out.append(ev)
    out.sort(key=lambda e: (sum(r.gap or 0 for r in e.reasons), e.card_id))
    return out


def improvement_steps(
    near: list[CardEvaluation],
    policy: Policy | None = None,
    profile: UserProfile | None = None,
    limit: int = 4,
) -> list[str]:
    """Concrete, gap-shaped advice. Never "take a loan to raise your score"."""
    policy = policy or get_policy()
    steps: list[str] = []
    seen: set[str] = set()

    # New-to-credit customers get the starter-card route before any gap maths,
    # because "earn Rs 6,000 more" is not the blocker for them.
    if profile is not None and is_new_to_credit(profile, policy):
        msg = policy.improve("NEW_TO_CREDIT")
        if msg not in seen:
            seen.add(msg)
            steps.append(msg)

    for ev in near:
        for r in ev.reasons:
            msg = _improve_for(r, policy)
            if msg and msg not in seen:
                seen.add(msg)
                steps.append(msg)
    return steps[:limit]


def _improve_for(reason: Reason, policy: Policy) -> str | None:
    if reason.code == "INCOME_BELOW_MIN" and reason.gap is not None:
        return policy.improve("INCOME_BELOW_MIN", gap=int(reason.gap), required=reason.required)
    if reason.code == "CIBIL_BELOW_MIN" and reason.gap is not None:
        return policy.improve(
            "CIBIL_BELOW_MIN", gap=int(reason.gap), required=reason.required
        )
    if reason.code == "AGE_BELOW_MIN" and reason.required is not None:
        return policy.improve("AGE_BELOW_MIN", required=reason.required)
    return None


# ------------------------------------------------------------------ SQL
def candidate_filter_sql(profile: UserProfile):
    """Parameterised WHERE clause plus params for employment membership.

    Kept separate from `prefilter_sql` because SQLite has no `= ANY(array)`
    while Postgres does; the dialect difference is confined to this function
    instead of leaking into the callers.
    """
    from sqlalchemy import text

    return text(
        """
        SELECT card_id
        FROM cards
        WHERE is_active = :is_active
          AND min_monthly_income <= :income
          AND min_age <= :age AND max_age >= :age
          AND (min_cibil IS NULL OR :cibil IS NULL OR min_cibil <= :cibil)
          AND (:new_to_credit = 0 OR min_monthly_income <= :tier_cap)
        """
    )


def candidate_params(profile: UserProfile, policy: Policy | None = None) -> dict:
    policy = policy or get_policy()
    ntc = is_new_to_credit(profile, policy)
    return {
        "is_active": True,
        "income": profile.monthly_income,
        "age": profile.age,
        "cibil": profile.cibil_score,
        "new_to_credit": int(ntc),
        "tier_cap": int(policy.tier("entry")["min_monthly_income"]),
    }


def fetch_candidates(
    db, profile: UserProfile, policy: Policy | None = None
) -> list[str]:
    """Run the prefilter.

    Employment stays out of the SQL because it is a list membership check and
    the row count is small; it is applied by `evaluate_card` right after, which
    keeps one authoritative implementation of that rule.
    """
    rows = db.execute(
        candidate_filter_sql(profile), candidate_params(profile, policy)
    ).scalars()
    return list(rows)
