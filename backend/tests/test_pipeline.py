"""Intake, masking, verifier and the graph.

These are the tests that justify the claim "the LLM never decides". Most of
them force the LLM path to fail, because a system that degrades correctly is
the thing worth testing.
"""

from __future__ import annotations

import pytest

from app import llm, masking, thresholds as T
from app.intake import (
    LLMExtractor,
    RegexExtractor,
    _coerce,
    _drop_unknown,
    missing_fields,
    next_question,
    parse_amount,
    to_profile,
)
from app.schemas import PartialProfile, Reason, SpendMix
from tests.conftest import make_profile


class TestMoneyParsing:
    @pytest.mark.parametrize(
        "sentence,fragment,expected",
        [
            ("1.2L per month", "1.2L", 120_000),
            ("my income is 1.2L per month", "1.2L", 120_000),
            ("95k per month", "95k", 95_000),
            ("salary 95,000 per month", "95,000", 95_000),
            ("my monthly income is 45000", "45000", 45_000),
            ("income 8 lakh per annum", "8 lakh", 66_667),
            ("my CTC is 25 lakh", "25 lakh", 208_333),
            ("earning 1.5 crore a year", "1.5 crore", 1_250_000),
            ("I get paid 40k per month", "40k", 40_000),
            ("my annual income is 2400000", "2400000", 200_000),
            ("Rs 40,000 a month", "Rs 40,000", 40_000),
        ],
    )
    def test_period_is_normalised_to_monthly(self, sentence, fragment, expected):
        """Everything downstream deals in one unit.

        "12 lakh per annum" becoming an income of 12,000 a month would be a
        silent, catastrophic misclassification. The fragment/context split is
        what makes this reliable: the number alone cannot say which period it
        belongs to, so it is told.
        """
        got, _ = parse_amount(fragment, sentence)
        assert got == pytest.approx(expected, rel=0.01)

    def test_bare_number_needs_context_to_be_a_number_at_all(self):
        """A fragment with no anchor is rejected, which is the guard against
        picking a spend figure up as an income."""
        assert parse_amount("salary 95,000", "salary 95,000")[0] is None

    def test_no_amount_returns_none(self):
        assert parse_amount("I have no idea")[0] is None


class TestRegexExtractor:
    def test_extracts_a_complete_message(self):
        p = RegexExtractor().extract(
            "I'm 32, salaried in Bangalore, earning 95k a month. "
            "Credit score 775. I spend 40k a month on travel."
        )
        assert p.age == 32
        assert p.monthly_income == 95_000
        assert p.employment.value == "salaried"
        assert p.city_tier == 1
        assert p.cibil_score == 775

    def test_leaves_unknown_fields_null(self):
        """The offline path must never invent a value."""
        p = RegexExtractor().extract("I like cashback cards")
        assert p.age is None
        assert p.monthly_income is None

    def test_annual_income_is_converted(self):
        p = RegexExtractor().extract("my salary is 12 lakh a year")
        assert p.monthly_income == 100_000

    def test_bare_number_without_a_cue_is_not_income(self):
        """A number next to no income cue is far more likely to be a spend."""
        assert RegexExtractor._income("I have 40000 to spend") is None

    def test_cardless_customer_leaves_score_null(self):
        p = RegexExtractor().extract("24, Mumbai, 60k a month, no credit card ever")
        assert p.cibil_score is None

    def test_no_annual_fee_sets_the_preference(self):
        p = RegexExtractor().extract("70k a month, I want a lifetime free card")
        assert "lifetime_free" in (p.preferences or [])


class TestLLMCoercion:
    def test_maps_a_spend_category_to_the_nearest_preference(self):
        out = _coerce({"preferences": ["online_shopping"], "age": 30})
        assert out["preferences"] == ["cashback"]

    def test_drops_an_unknown_preference(self):
        out = _coerce({"preferences": ["quantum_bounty", "fuel"]})
        assert out["preferences"] == ["fuel"]

    def test_empty_preference_list_becomes_none(self):
        """None asks the customer; [] means they declined to say."""
        assert _coerce({"preferences": ["nonsense"]})["preferences"] is None

    def test_unknown_employment_is_dropped_not_invented(self):
        assert _coerce({"employment": "astronaut"})["employment"] is None

    def test_unknown_fields_are_dropped(self):
        out = _drop_unknown({"age": 30, "favourite_colour": "blue"})
        assert "favourite_colour" not in out

    def test_coerced_output_validates(self):
        from app.schemas import PartialProfile

        out = _drop_unknown(
            _coerce(
                {
                    "age": 32,
                    "monthly_income": 95_000,
                    "employment": "Salaried",
                    "preferences": ["SHOPPING", "MILES"],
                }
            )
        )
        p = PartialProfile.model_validate(out)
        assert p.employment.value == "salaried"
        assert set(p.preferences) == {"cashback", "travel"}


class TestProviderFailover:
    """Groq's free tier rate-limits, so the second provider is a normal path."""

    def test_providers_are_ordered_primary_first(self, monkeypatch):
        from app.config import get_settings

        s = get_settings()
        monkeypatch.setattr(s, "groq_api_key", "k1")
        monkeypatch.setattr(s, "openrouter_api_key", "")
        assert [name for name, _, _ in llm.providers()] == ["groq"]

        monkeypatch.setattr(s, "openrouter_api_key", "k2")
        assert [name for name, _, _ in llm.providers()] == ["groq", "openrouter"]

    def test_second_provider_is_used_when_the_first_is_unauthorised(self, monkeypatch):
        import httpx

        from app.config import get_settings

        s = get_settings()
        monkeypatch.setattr(s, "groq_api_key", "bad")
        monkeypatch.setattr(s, "openrouter_api_key", "good")
        monkeypatch.setattr(llm, "LLM_MAX_ATTEMPTS", 1)
        llm.reset_usage()

        def fake_post(messages, model, *, base_url, api_key, provider, **kw):
            if provider == "groq":
                raise httpx.HTTPStatusError(
                    "401",
                    request=httpx.Request("POST", "https://api.groq.com"),
                    response=httpx.Response(401, request=httpx.Request("POST", "https://api.groq.com")),
                )
            return '{"ok": true}'

        monkeypatch.setattr(llm, "_post", fake_post)
        result = llm.chat("go", "go", json_mode=True)
        assert result.used_llm
        assert result.json_value == {"ok": True}
        assert llm.usage()["fallbacks"] == 1

    def test_total_outage_degrades_rather_than_raising(self, monkeypatch):
        import httpx

        from app.config import get_settings

        s = get_settings()
        monkeypatch.setattr(s, "groq_api_key", "bad")
        monkeypatch.setattr(s, "openrouter_api_key", "bad")
        monkeypatch.setattr(llm, "LLM_MAX_ATTEMPTS", 1)

        def always_401(messages, model, *, base_url, api_key, provider, **kw):
            raise httpx.HTTPStatusError(
                "401",
                request=httpx.Request("POST", "https://x"),
                response=httpx.Response(401, request=httpx.Request("POST", "https://x")),
            )

        monkeypatch.setattr(llm, "_post", always_401)
        result = llm.chat("go", "go")
        assert result.used_llm is False
        assert result.error


class TestFallbackOnLLMFailure:
    def test_extraction_survives_a_failed_call(self, monkeypatch):
        monkeypatch.setattr(llm, "chat", lambda *a, **k: llm.LLMResult(used_llm=False, error="boom"))
        p = LLMExtractor().extract("I'm 30, earning 80k a month in Pune")
        assert p.age == 30
        assert p.monthly_income == 80_000

    def test_extraction_survives_unparseable_json(self, monkeypatch):
        monkeypatch.setattr(
            llm, "chat", lambda *a, **k: llm.LLMResult(text="sorry!", json_value=None, used_llm=True)
        )
        p = LLMExtractor().extract("I'm 30, earning 80k a month")
        assert p.age == 30

    def test_extraction_survives_an_unfixable_profile(self, monkeypatch):
        monkeypatch.setattr(
            llm,
            "chat",
            lambda *a, **k: llm.LLMResult(
                json_value={"age": "old", "employment": None}, used_llm=True
            ),
        )
        p = LLMExtractor().extract("I'm 30, earning 80k a month")
        assert p.age == 30


class TestMissingFields:
    def test_empty_profile_is_all_missing(self):
        assert missing_fields(PartialProfile()) == T.REQUIRED_PROFILE_FIELDS

    def test_complete_profile_has_none(self, profile):
        partial = PartialProfile(
            age=profile.age,
            monthly_income=profile.monthly_income,
            employment=profile.employment,
            city_tier=profile.city_tier,
            monthly_spend=profile.monthly_spend,
        )
        assert missing_fields(partial) == []

    def test_zero_income_counts_as_missing(self):
        """Zero is a placeholder, not an answer. It must trigger a question."""
        partial = PartialProfile(
            age=30, monthly_income=0, employment="salaried", city_tier=1,
            monthly_spend=SpendMix(other=1_000),
        )
        assert "monthly_income" in missing_fields(partial)

    def test_question_order_is_income_first_after_age(self):
        partial = PartialProfile(age=30)
        assert next_question(partial) == T.FOLLOWUP_QUESTIONS["monthly_income"]

    def test_preferences_question_comes_last(self):
        partial = PartialProfile(
            age=30, monthly_income=50_000, employment="salaried", city_tier=1,
            monthly_spend=SpendMix(other=5_000),
        )
        assert next_question(partial) == T.PREFERENCE_QUESTION

    def test_to_profile_refuses_an_incomplete_profile(self):
        with pytest.raises(ValueError, match="missing"):
            to_profile(PartialProfile(age=30))


class TestMasking:
    @pytest.mark.parametrize(
        "raw,label",
        [
            ("my PAN is ABCDE1234F", "pan_alpha"),
            ("4111111111111111", "pan"),
            ("call me on 9876543210", "phone"),
            ("email ravi.sharma@example.com", "email"),
            ("cvv is 123", "cvv"),
            ("expires 04/27", "expiry"),
            ("aadhaar 1234 5678 9012", "aadhaar"),
        ],
    )
    def test_each_pattern_is_caught(self, raw, label):
        _, labels = masking.mask(raw)
        assert label in labels

    def test_masking_removes_the_value(self):
        clean, _ = masking.mask("my PAN is ABCDE1234F and phone 9876543210")
        assert "ABCDE1234F" not in clean
        assert "9876543210" not in clean

    def test_ordinary_text_is_untouched(self):
        raw = "I earn 95000 a month and my CIBIL is 775"
        clean, labels = masking.mask(raw)
        assert clean == raw
        assert labels == []

    def test_is_clean_detects_a_leak(self):
        assert not masking.is_clean("call 9876543210")
        assert masking.is_clean("call me any time")


class TestVerifier:
    """The verifier's value is that it fails. Tests that only check the happy
    path would prove nothing."""

    def _state(self, profile, cards):
        return {
            "eligible_cards": cards,
            "gate_passed": True,
        }

    def test_passes_a_clean_response(self, loaded_db, profile):
        from app.graph.run import run_profile
        from app.schemas import ProfileRequest

        response = run_profile(ProfileRequest(profile=profile))
        assert response.verifier is not None
        assert response.verifier.passed, response.verifier.checks

    def test_catches_a_recommendation_for_an_ineligible_card(self, profile, cards, loaded_db):
        """The single most important check in the system."""
        from app.verify import verify_response

        # CARD_001 is far out of reach for this profile, so it cannot be in the
        # eligible set the verifier is given.
        response = _clean_response()
        response.recommendations = [_rec("CARD_001", score=0.9)]
        state = self._state(profile, [])
        report = verify_response(response, state)
        assert not report.passed
        check = next(c for c in report.checks if c["name"] == "eligibility_consistency")
        assert not check["passed"]

    def test_catches_approval_language_for_a_rejected_customer(self, profile, loaded_db):
        from app.verify import verify_response

        response = _clean_response()
        response.status = "profile_rejected"
        response.decision = "rejected"
        response.summary = "You are approved for all of these cards."
        state = {"eligible_cards": [], "gate_passed": False}
        report = verify_response(response, state)
        assert not report.passed
        assert not next(
            c for c in report.checks if c["name"] == "no_approval_language_when_rejected"
        )["passed"]

    def test_catches_an_unsupported_number(self, profile, loaded_db):
        from app.verify import verify_response

        response = _clean_response()
        response.summary = "This card earns you Rs 99,999 a year in cash back."
        response.evidence = []
        report = verify_response(response, self._state(profile, []))
        assert not report.passed

    def test_allows_a_number_the_engine_computed(self, profile, loaded_db):
        from app.graph.run import run_profile
        from app.schemas import ProfileRequest

        response = run_profile(ProfileRequest(profile=profile))
        net = response.recommendations[0].net_annual_value_rs
        response.summary = f"That works out to Rs {net:,} of value over a year."
        from app.verify import verify_response

        report = verify_response(response, self._state(profile, loaded_db))
        assert next(
            c for c in report.checks if c["name"] == "no_unsupported_numbers"
        )["passed"]

    def test_catches_pii_in_the_output(self, profile, loaded_db):
        from app.verify import verify_response

        response = _clean_response()
        response.summary = "Sure, I have emailed the details to ravi@example.com."
        response.evidence = []
        report = verify_response(response, self._state(profile, []))
        assert not report.passed

    def test_catches_a_leaked_reason_code(self, profile, loaded_db):
        from app.verify import verify_response

        response = _clean_response()
        response.summary = "Blocked by CIBIL_BELOW_COMPANY_MIN on this one."
        response.evidence = []
        report = verify_response(response, self._state(profile, []))
        assert not report.passed
        assert not next(
            c for c in report.checks if c["name"] == "no_internal_reason_codes"
        )["passed"]

    def test_catches_out_of_order_recommendations(self, profile, cards, loaded_db):
        from app.verify import verify_response

        response = _clean_response()
        response.recommendations = [_rec("A", score=0.2), _rec("B", score=0.9)]
        report = verify_response(response, self._state(profile, cards))
        assert not report.passed
        assert not next(c for c in report.checks if c["name"] == "ranking_order")["passed"]


class TestGraphFlow:
    def test_form_route_recommends(self, loaded_db, ingested, profile):
        from app.graph.run import run_profile
        from app.schemas import ProfileRequest

        r = run_profile(ProfileRequest(profile=profile))
        assert r.status == "recommendations_available"
        assert r.decision == "recommended"
        assert r.recommendations

    def test_incomplete_chat_asks_exactly_one_question(self, loaded_db, ingested):
        from app.graph.run import run_chat
        from app.schemas import NaturalLanguageRequest

        r = run_chat(NaturalLanguageRequest(message="I want a credit card", session_id="t"))
        assert r.status == "need_more_information"
        assert r.question
        assert r.question.count("?") == 1
        # Nothing ships with a pending question.
        assert r.recommendations == []

    def test_gate_rejection_skips_the_catalogue(self, loaded_db, ingested):
        from app.graph.run import run_profile
        from app.schemas import ProfileRequest

        r = run_profile(
            ProfileRequest(profile=make_profile(cibil_score=520, missed_payments_12m=2))
        )
        assert r.status == "profile_rejected"
        assert r.recommendations == []
        # The prefilter must not have run at all.
        assert "prefilter" not in r.trace

    def test_same_profile_twice_gives_the_same_answer(self, loaded_db, ingested, profile):
        from app.graph.run import run_profile
        from app.schemas import ProfileRequest

        a = run_profile(ProfileRequest(profile=profile))
        b = run_profile(ProfileRequest(profile=profile))
        assert [r.card_id for r in a.recommendations] == [r.card_id for r in b.recommendations]

    def test_response_exposes_a_per_node_trace(self, loaded_db, ingested, profile):
        from app.graph.run import run_profile
        from app.schemas import ProfileRequest

        r = run_profile(ProfileRequest(profile=profile))
        for key in ("gate", "prefilter", "eligibility", "rank", "retrieval", "total_ms"):
            assert key in r.trace, key

    def test_explanation_degrades_when_the_llm_fails(self, loaded_db, ingested, profile, monkeypatch):
        """No LLM must still produce a correct, readable answer."""
        monkeypatch.setattr(llm, "chat", lambda *a, **k: llm.LLMResult(used_llm=False, error="down"))
        from app.graph.run import run_profile
        from app.schemas import ProfileRequest

        r = run_profile(ProfileRequest(profile=profile))
        assert r.status == "recommendations_available"
        assert len(r.summary) > 60
        assert r.recommendations[0].name in r.summary
        assert r.verifier.used_fallback


def _clean_response():
    from app.schemas import PolicyEvidence, RecommendationResponse

    return RecommendationResponse(
        profile_id="T",
        status="recommendations_available",
        decision="recommended",
        profile=make_profile(),
        evidence=[
            PolicyEvidence(source="underwriting_policy.md", section="s", text="x", score=0.5)
        ],
    )


def _rec(card_id: str, score: float):
    from app.schemas import Recommendation

    return Recommendation(
        rank=1, card_id=card_id, name="X", bank="Y", tier="mid", score=score,
        net_annual_value_rs=5_000, est_credit_limit_rs=100_000, apr_pct=24.0,
        fee_payable_rs=0, sources=["x.md"],
    )
