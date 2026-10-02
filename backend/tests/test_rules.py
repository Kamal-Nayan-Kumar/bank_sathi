"""Rule engine: boundaries, gaps, near-miss classification.

Every threshold is tested at value-1 / value / value+1. A comparison that is
never probed on both sides of its boundary is a comparison nobody should trust
with a rejection.
"""

from __future__ import annotations

import pytest

from app.rules.engine import (
    candidate_filter_sql,
    candidate_params,
    evaluate_card,
    fetch_candidates,
    global_gate,
    improvement_steps,
    is_new_to_credit,
    near_misses,
)
from app.rules.scoring import (
    category_rewards,
    est_credit_limit,
    fee_payable,
    lounge_credit,
    net_annual_value,
    rank,
    score_breakdown,
)
from app.schemas import Card, Employment, RewardRule, SpendMix, UserProfile
from tests.conftest import make_profile


def card(**over) -> Card:
    base = dict(
        card_id="CARD_T1",
        name="Test Card",
        bank="Test Bank",
        network="Visa",
        segment="cashback",
        tier="mid",
        annual_fee=5_000,
        joining_fee=0,
        fee_waiver_spend=None,
        min_monthly_income=50_000,
        min_cibil=700,
        min_age=23,
        max_age=65,
        allowed_employment=[Employment.SALARIED, Employment.SELF_EMPLOYED],
        apr_pct=24.0,
        lounge_visits_per_year=0,
        reward_rules=[RewardRule(category="other", value_pct=1.0)],
        benefit_highlights=[],
    )
    base.update(over)
    return Card(**base)


# ----------------------------------------------------------------- global gate
class TestGlobalGate:
    def test_clean_profile_passes(self, profile):
        assert global_gate(profile).passed

    def test_age_below_min_is_caught_by_the_schema_first(self):
        """The schema is the first line of defence on age, not the gate.

        `UserProfile.age` is ge=18, so a 17-year-old never reaches the engine
        through the API. The gate keeps the check as defense in depth.
        """
        with pytest.raises(ValueError):
            make_profile(age=17)

    def test_gate_age_branch_still_fires_when_bypassed(self, policy):
        # model_construct skips validation on purpose: this probes the gate's own
        # branch, which is otherwise unreachable because the schema is stricter.
        raw = make_profile(age=policy.min_age)
        bad = raw.model_copy(update={"age": policy.min_age - 1})
        r = global_gate(bad)
        assert not r.passed
        assert r.reasons[0].code == "AGE_BELOW_MIN"
        assert r.reasons[0].gap == 1

    def test_age_at_min_passes(self, policy):
        assert global_gate(make_profile(age=policy.min_age)).passed

    def test_age_above_max_rejected(self, policy):
        r = global_gate(make_profile(age=policy.max_age + 1))
        assert any(x.code == "AGE_ABOVE_MAX" for x in r.reasons)

    def test_cibil_at_company_min_passes(self, policy):
        assert global_gate(make_profile(cibil_score=policy.min_cibil)).passed

    def test_cibil_one_below_company_min_fails(self, policy):
        r = global_gate(make_profile(cibil_score=policy.min_cibil - 1))
        assert not r.passed
        assert r.reasons[0].code == "CIBIL_BELOW_COMPANY_MIN"

    def test_no_cibil_is_not_a_gate_failure(self):
        """A customer with no history is restricted, not rejected."""
        r = global_gate(make_profile(cibil_score=None, existing_cards=0))
        assert r.passed

    def test_missed_payment_is_a_hard_fail(self, policy):
        at_limit = global_gate(
            make_profile(missed_payments_12m=policy.max_missed_payments_12m)
        )
        assert at_limit.passed
        over = global_gate(
            make_profile(missed_payments_12m=policy.max_missed_payments_12m + 1)
        )
        assert not over.passed
        assert over.reasons[0].code == "MISSED_PAYMENTS_PRESENT"

    def test_inquiries_boundary(self, policy):
        limit = policy.max_recent_inquiries_6m
        assert global_gate(make_profile(recent_inquiries_6m=limit)).passed
        assert not global_gate(make_profile(recent_inquiries_6m=limit + 1)).passed

    def test_utilization_boundary(self, policy):
        assert global_gate(make_profile(utilization_pct=policy.max_utilization_pct)).passed
        assert not global_gate(
            make_profile(utilization_pct=policy.max_utilization_pct + 0.1)
        ).passed

    def test_zero_income_rejected(self):
        r = global_gate(make_profile(monthly_income=0))
        assert not r.passed
        assert r.reasons[0].code == "INCOME_NOT_DECLARED"

    def test_all_failures_reported_not_just_first(self, profile, policy):
        r = global_gate(
            make_profile(
                missed_payments_12m=2,
                recent_inquiries_6m=policy.max_recent_inquiries_6m + 1,
                utilization_pct=99.0,
            )
        )
        codes = {x.code for x in r.reasons}
        assert codes == {
            "MISSED_PAYMENTS_PRESENT",
            "TOO_MANY_RECENT_INQUIRIES",
            "UTILIZATION_TOO_HIGH",
        }


# --------------------------------------------------------------- new to credit
class TestNewToCredit:
    def test_no_cibil_is_new(self, profile, policy):
        assert is_new_to_credit(make_profile(cibil_score=None), policy)

    def test_at_threshold_is_new(self, policy):
        assert is_new_to_credit(make_profile(cibil_score=policy.new_to_credit_cibil), policy)

    def test_above_threshold_is_not_new(self, policy):
        assert not is_new_to_credit(
            make_profile(cibil_score=policy.new_to_credit_cibil + 1), policy
        )

    def test_zero_existing_cards_with_no_score_is_new(self, profile, policy):
        assert is_new_to_credit(
            make_profile(existing_cards=0, cibil_score=None), policy
        )

    def test_a_high_score_with_no_cards_is_not_new(self, profile, policy):
        """No cards but a strong score means they have history elsewhere."""
        assert not is_new_to_credit(
            make_profile(existing_cards=0, cibil_score=800), policy
        )


# ----------------------------------------------------------------- evaluate
class TestEvaluateCard:
    def test_eligible(self, profile):
        assert evaluate_card(profile, card()).eligible

    def test_income_boundary(self, profile):
        c = card(min_monthly_income=95_000)
        assert evaluate_card(make_profile(monthly_income=95_000), c).eligible
        r = evaluate_card(make_profile(monthly_income=94_999), c)
        assert not r.eligible
        assert r.reasons[0].code == "INCOME_BELOW_MIN"
        assert r.reasons[0].gap == 1

    def test_cibil_boundary(self, profile):
        c = card(min_cibil=750)
        assert evaluate_card(make_profile(cibil_score=750), c).eligible
        r = evaluate_card(make_profile(cibil_score=749), c)
        assert r.reasons[0].code == "CIBIL_BELOW_MIN"
        assert r.reasons[0].gap == 1

    def test_null_card_cibil_skips_the_check(self, profile):
        assert evaluate_card(profile, card(min_cibil=None)).eligible

    def test_null_profile_cibil_skips_the_check(self, profile):
        """Secured cards must be openable by customers with no history."""
        assert evaluate_card(make_profile(cibil_score=None), card(min_cibil=None)).eligible

    def test_age_window(self, profile):
        c = card(min_age=25, max_age=45)
        assert evaluate_card(make_profile(age=25), c).eligible
        assert evaluate_card(make_profile(age=45), c).eligible
        assert not evaluate_card(make_profile(age=24), c).eligible
        assert not evaluate_card(make_profile(age=46), c).eligible

    def test_employment_not_allowed(self, profile):
        c = card(allowed_employment=[Employment.BUSINESS_OWNER])
        r = evaluate_card(profile, c)
        assert r.reasons[0].code == "EMPLOYMENT_NOT_ALLOWED"
        assert r.reasons[0].fixable is False

    def test_inactive_card(self, profile):
        r = evaluate_card(profile, card(is_active=False))
        assert r.reasons[0].code == "CARD_INACTIVE"

    def test_every_failure_listed(self, profile):
        r = evaluate_card(profile, card(min_monthly_income=999_999, min_cibil=890, min_age=40))
        assert len(r.reasons) >= 2

    def test_gap_is_exact(self, profile):
        r = evaluate_card(profile, card(min_monthly_income=120_000))
        assert r.reasons[0].gap == 25_000


# ----------------------------------------------------------------- near miss
class TestNearMiss:
    def test_small_income_gap_is_a_near_miss(self, profile, policy):
        c = card(min_monthly_income=100_000)
        r = evaluate_card(make_profile(monthly_income=95_000), c)
        assert r.reasons[0].near_miss

    def test_large_income_gap_is_not(self, profile):
        r = evaluate_card(profile, card(min_monthly_income=500_000))
        assert not r.reasons[0].near_miss

    def test_small_cibil_gap_is_a_near_miss(self, profile):
        r = evaluate_card(profile, card(min_cibil=785))
        assert r.reasons[0].near_miss

    def test_two_failures_is_not_a_near_miss(self, profile):
        ev = evaluate_card(profile, card(min_monthly_income=100_000, min_cibil=790))
        assert not near_misses([ev], {})

    def test_missed_payments_are_never_a_near_miss(self, profile):
        """An on-file default is not a small margin; do not imply it is."""
        r = evaluate_card(profile, card(min_monthly_income=100_000))
        assert r.reasons[0].near_miss
        ev = type(r)(card_id="X", eligible=False, reasons=[r.reasons[0]])
        assert near_misses([ev], {})

    def test_improvement_step_quotes_the_gap(self, profile):
        ev = evaluate_card(profile, card(min_monthly_income=110_000))
        steps = improvement_steps(near_misses([ev], {}, None), None, profile)
        assert any("15,000" in s or "15000" in s for s in steps)

    def test_no_debt_advice_in_steps(self, profile):
        ev = evaluate_card(profile, card(min_cibil=780))
        steps = improvement_steps(near_misses([ev], {}, None), None, profile)
        joined = " ".join(steps).lower()
        assert "loan" not in joined and "borrow" not in joined

    def test_new_to_credit_gets_the_starter_route_first(self):
        prof = make_profile(cibil_score=None, existing_cards=0)
        steps = improvement_steps([], None, prof)
        assert steps and "secured" in steps[0].lower()


# ------------------------------------------------------------------ SQL
class TestPrefilter:
    def test_no_string_interpolation_of_profile_values(self, profile):
        """Values must be bound parameters, never concatenated into SQL."""
        sql = str(candidate_filter_sql(profile))
        assert str(profile.monthly_income) not in sql
        assert str(profile.age) not in sql
        assert ":income" in sql and ":age" in sql

    def test_params_include_profile_values(self, profile):
        p = candidate_params(profile)
        assert p["income"] == profile.monthly_income
        assert p["age"] == profile.age

    def test_filters_down_to_a_subset(self, loaded_db):
        from app.db import session_scope

        with session_scope() as db:
            ids = fetch_candidates(db, make_profile(monthly_income=60_000))
        assert 0 < len(ids) < len(loaded_db)

    def test_low_income_returns_fewer(self, loaded_db):
        from app.db import session_scope

        with session_scope() as db:
            rich = fetch_candidates(db, make_profile(monthly_income=500_000))
            poor = fetch_candidates(db, make_profile(monthly_income=20_000))
        assert len(poor) < len(rich)

    def test_new_to_credit_is_capped_to_entry_income(self, loaded_db):
        from app.db import session_scope

        prof = make_profile(cibil_score=None, existing_cards=0, monthly_income=60_000)
        with session_scope() as db:
            ids = fetch_candidates(db, prof)
            cards = {c.card_id: c for c in __import__("app.db", fromlist=["x"]).get_all_cards()}
        assert all(cards[i].tier in ("secured", "entry") for i in ids)

    def test_inactive_cards_excluded(self, loaded_db):
        from app.db import get_all_cards, session_scope

        inactive = {c.card_id for c in get_all_cards(include_inactive=True) if not c.is_active}
        assert inactive
        with session_scope() as db:
            ids = set(fetch_candidates(db, make_profile(monthly_income=900_000)))
        assert not (ids & inactive)


# ------------------------------------------------------------------ scoring
class TestFeeAndValue:
    def test_fee_paid_when_no_waiver(self, profile):
        assert fee_payable(card(annual_fee=5_000), profile) == 5_000

    def test_fee_waived_at_threshold(self, profile, policy):
        c = card(annual_fee=5_000, fee_waiver_spend=50_000)
        assert profile.monthly_spend.total() * 12 >= 50_000
        assert fee_payable(c, profile) == 0

    def test_fee_owed_one_rupee_short_of_waiver(self, profile):
        annual = profile.monthly_spend.total() * 12
        c = card(annual_fee=5_000, fee_waiver_spend=annual + 1)
        assert fee_payable(c, profile) == 5_000

    def test_category_reward_respects_cap(self, profile):
        # Rs 16,000 of travel at 5% would be Rs 800 a month, under the cap, so
        # the cap is set below it to prove the cap is what binds.
        c = card(reward_rules=[RewardRule(category="travel", value_pct=5.0, monthly_cap_rs=300)])
        assert category_rewards(c, profile) == pytest.approx(300 * 12)

    def test_category_reward_below_cap_is_not_capped(self, profile):
        c = card(reward_rules=[RewardRule(category="travel", value_pct=5.0, monthly_cap_rs=5_000)])
        assert category_rewards(c, profile) == pytest.approx(16_000 * 0.05 * 12)

    def test_category_reward_without_cap(self, profile):
        c = card(reward_rules=[RewardRule(category="travel", value_pct=5.0)])
        assert category_rewards(c, profile) == pytest.approx(16_000 * 0.05 * 12)

    def test_zero_spend_category_earns_nothing(self, profile):
        c = card(reward_rules=[RewardRule(category="fuel", value_pct=5.0)])
        prof = make_profile(monthly_spend=SpendMix(fuel=0, other=1_000))
        assert category_rewards(c, prof) == 0

    def test_lounge_credit_only_when_preferred(self, profile, policy):
        c = card(lounge_visits_per_year=8)
        assert lounge_credit(c, profile) > 0  # profile prefers travel + lounge
        assert lounge_credit(c, make_profile(preferences=["cashback"])) == 0

    def test_lounge_credit_is_capped(self, profile, policy):
        c = card(lounge_visits_per_year=100)
        assert lounge_credit(c, profile) == policy.lounge_annual_credit_cap_rs

    def test_net_value_subtracts_fee(self, profile):
        """Rs 1% on 'other' (Rs 6,000) is Rs 720 a year, so the fee must win."""
        c = card(reward_rules=[RewardRule(category="other", value_pct=1.0)], annual_fee=2_400)
        expected = category_rewards(c, profile) - 2_400
        assert expected < 0
        assert net_annual_value(c, profile) == 0  # floored, not negative

    def test_net_value_is_rewards_minus_fee_when_positive(self, profile):
        c = card(
            reward_rules=[RewardRule(category="travel", value_pct=3.0)],
            annual_fee=2_400,
        )
        expected = category_rewards(c, profile) - 2_400
        assert expected > 0
        assert net_annual_value(c, profile) == round(expected)

    def test_net_value_never_negative(self, profile):
        c = card(reward_rules=[RewardRule(category="travel", value_pct=1.0)], annual_fee=50_000)
        prof = make_profile(monthly_spend=SpendMix(travel=100, other=100))
        assert net_annual_value(c, prof) == 0

    def test_credit_limit_uses_tier_multiplier(self, profile, policy):
        c = card(tier="premium")
        mult = policy.tier("premium")["limit_income_multiplier"]
        assert est_credit_limit(c, profile) == int(profile.monthly_income * mult // 5000 * 5000)


class TestRanking:
    def test_ranked_order_matches_scores(self, profile):
        cards = [
            card(card_id="A", reward_rules=[RewardRule(category="travel", value_pct=3.0)], annual_fee=0),
            card(card_id="B", reward_rules=[RewardRule(category="travel", value_pct=2.0)], annual_fee=0),
            card(card_id="C", reward_rules=[RewardRule(category="fuel", value_pct=0.5)], annual_fee=0),
        ]
        ranked, _ = rank(cards, profile)
        scores = [s for _, s, _ in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_lifetime_free_beats_a_fee_when_value_is_equal(self, profile):
        free = card(
            card_id="FREE",
            annual_fee=0,
            reward_rules=[RewardRule(category="travel", value_pct=3.0)],
        )
        paid = card(
            card_id="PAID",
            annual_fee=9_000,
            reward_rules=[RewardRule(category="travel", value_pct=3.0)],
        )
        ranked, _ = rank([paid, free], profile)
        assert [c.card_id for c, _, _ in ranked][0] == "FREE"

    def test_low_value_cards_dropped(self, profile, policy):
        junk = card(
            card_id="JUNK",
            annual_fee=40_000,
            reward_rules=[RewardRule(category="other", value_pct=0.1)],
        )
        _, dropped = rank([junk], profile)
        assert [c.card_id for c in dropped] == ["JUNK"]

    def test_respects_max_recommendations(self, profile, policy):
        many = [
            card(
                card_id=f"C{i}",
                reward_rules=[RewardRule(category="travel", value_pct=1.0 + i * 0.1)],
            )
            for i in range(20)
        ]
        ranked, _ = rank(many, profile)
        assert len(ranked) <= policy.max_recommendations

    def test_breakdown_weights_sum_to_total(self, profile):
        c = card(reward_rules=[RewardRule(category="travel", value_pct=3.0)], annual_fee=0)
        bd = score_breakdown(c, profile)
        w = profile_scoring_weights()
        assert bd.total == pytest.approx(
            w["net_value"] * bd.net_value
            + w["spend_alignment"] * bd.spend_alignment
            + w["preference_match"] * bd.preference_match
            + w["fee_fit"] * bd.fee_fit,
            abs=1e-4,
        )

    def test_ranking_is_deterministic(self, profile, cards):
        eligible = [
            c
            for c in cards
            if evaluate_card(profile, c).eligible
        ]
        a, _ = rank(eligible, profile)
        b, _ = rank(list(reversed(eligible)), profile)
        assert [c.card_id for c, _, _ in a] == [c.card_id for c, _, _ in b]

    def test_cap_reduces_spend_alignment(self, profile):
        """A 5% rate with a tiny cap must not outrank an uncapped 3%."""
        capped = card(
            card_id="CAP",
            reward_rules=[RewardRule(category="travel", value_pct=5.0, monthly_cap_rs=200)],
        )
        real = card(
            card_id="REAL",
            reward_rules=[RewardRule(category="travel", value_pct=3.0)],
        )
        assert score_breakdown(real, profile).spend_alignment > score_breakdown(
            capped, profile
        ).spend_alignment


def profile_scoring_weights():
    from app.policy import get_policy

    return get_policy().scoring_weights


# ------------------------------------------------------------------ schema
class TestProfileValidation:
    def test_implausible_spend_rejected(self):
        with pytest.raises(ValueError, match="implausible"):
            make_profile(monthly_income=20_000, monthly_spend=SpendMix(other=500_000))

    def test_age_below_18_rejected(self):
        with pytest.raises(ValueError):
            make_profile(age=17)

    def test_unknown_field_rejected(self):
        with pytest.raises(ValueError):
            UserProfile(
                age=30,
                monthly_income=50_000,
                employment="salaried",
                city_tier=1,
                monthly_spend=SpendMix(),
                salary=1,
            )

    def test_cibil_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            make_profile(cibil_score=250)
