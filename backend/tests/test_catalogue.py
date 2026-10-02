"""Catalogue generation and policy-doc consistency."""

from __future__ import annotations

import copy
import re

import pytest

from app.catalogue import generate_cards
from app.docs_gen import assert_docs_consistent, generate_policy_docs
from app.policy import get_policy
from app.rag.ingest import _split_sections
from app.rag.ingest import chunk_markdown as cm
from app.schemas import Card, Employment


def test_generation_is_deterministic():
    a = generate_cards(40, seed=7)
    b = generate_cards(40, seed=7)
    assert [c.model_dump() for c in a] == [c.model_dump() for c in b]


def test_different_seeds_differ():
    a = generate_cards(40, seed=7)
    b = generate_cards(40, seed=8)
    assert [c.card_id for c in a] == [c.card_id for c in b]
    assert [c.name for c in a] != [c.name for c in b]


def test_card_ids_unique(cards):
    ids = [c.card_id for c in cards]
    assert len(ids) == len(set(ids))


def test_bank_and_name_pairs_unique(cards):
    pairs = [(c.bank, c.name) for c in cards]
    assert len(pairs) == len(set(pairs))


def test_all_tiers_present(cards):
    assert {c.tier for c in cards} == {"secured", "entry", "mid", "premium"}


def test_inactive_cards_exist(cards):
    # `is_active` must be exercised, otherwise the CARD_INACTIVE branch of the
    # rule engine is dead code that looks tested.
    assert any(not c.is_active for c in cards)


def test_fee_waiver_exceeds_fee(cards):
    """A waiver threshold at or below the fee would make waiving free money."""
    for c in cards:
        if c.fee_waiver_spend:
            assert c.fee_waiver_spend > c.annual_fee


def test_fee_free_cards_have_no_waiver_threshold(cards):
    """A waiver on a zero-fee card is meaningless and invites nonsense prose."""
    for c in cards:
        if c.annual_fee == 0:
            assert c.fee_waiver_spend is None, f"{c.card_id} has a waiver but no fee"


def test_reward_caps_are_meaningful(cards):
    for c in cards:
        for r in c.reward_rules:
            if r.monthly_cap_rs:
                assert r.monthly_cap_rs > 0
                # A cap below Rs 100 is not a plausible published cap.
                assert r.monthly_cap_rs >= 100


def test_assumed_fields_disclosed(cards):
    for c in cards:
        assert "apr_pct" in c.assumed_fields
        assert "reward_caps" in c.assumed_fields


def test_tier_income_minimum_is_respected(cards, policy):
    """Every card must sit at or above its tier's floor, or the tier table lies."""
    for c in cards:
        floor = policy.tier(c.tier)["min_monthly_income"]
        assert c.min_monthly_income >= floor, f"{c.card_id} {c.tier} below tier floor"


def test_tier_cibil_minimum_is_respected(cards, policy):
    for c in cards:
        expected = policy.tier(c.tier)["min_cibil"]
        assert c.min_cibil == expected


def test_premium_cards_have_some_reward(cards):
    for c in cards:
        if c.tier == "premium":
            assert c.reward_rules


# --------------------------------------------------------------- policy docs
def test_policy_docs_are_consistent_with_yaml():
    assert_docs_consistent(dict(generate_policy_docs()))


def test_policy_docs_have_no_unformatted_placeholders():
    """A "{actual}" in an indexed doc gets copied to the customer or guessed."""
    for name, body in generate_policy_docs():
        leftovers = re.findall(r"\{[a-z_]+\}", body)
        assert not leftovers, f"{name} still contains {leftovers}"


def test_card_policy_chunks_have_no_placeholders(cards):
    from app.rag.ingest import build_card_policy_chunks

    for chunk in build_card_policy_chunks(cards):
        assert not re.findall(r"\{[a-z_]+\}", chunk.text), chunk.id


def test_policy_docs_detect_a_changed_threshold(tmp_path):
    """The guard has to actually fail when the YAML moves, or it proves nothing."""
    import yaml

    # Deep copy: `get_policy().raw` is the cached live document and mutating it
    # would leak into every other test in the session.
    src = copy.deepcopy(get_policy().raw)
    src["global_gate"]["min_cibil"] = 999
    moved = type(get_policy())(src)
    with pytest.raises(AssertionError) as exc:
        assert_docs_consistent(dict(generate_policy_docs(moved)))
    assert "min_cibil" in str(exc.value)


def test_scoring_weights_sum_to_one(policy):
    assert sum(policy.scoring_weights.values()) == pytest.approx(1.0)


def test_every_reason_code_has_wording(policy):
    for code in (
        "AGE_BELOW_MIN",
        "INCOME_BELOW_MIN",
        "CIBIL_BELOW_MIN",
        "MISSED_PAYMENTS_PRESENT",
        "NEW_TO_CREDIT",
    ):
        assert policy.label(code)
        assert policy.improve(code, required=1, gap=1)


def test_improvement_copy_does_not_suggest_debt():
    """Guardrail: the advice layer must never advise taking on debt."""
    for code, r in get_policy().reasons.items():
        text = (str(r["improve"]) + str(r["message"])).lower()
        for banned in ("take a loan", "borrow", "personal loan", "apply for a loan"):
            assert banned not in text, f"{code} suggests debt: {text}"


# --------------------------------------------------------------- chunking
def test_split_sections_keeps_fences_intact():
    md = "# T\n\nintro text\n\n## A\n\nbody a\n\n## B\n\n```\n## not a heading\n```\n"
    sections = dict(_split_sections(md))
    assert set(sections) == {"T", "A", "B"}
    assert "## not a heading" in sections["B"]


def test_chunk_markdown_tags_metadata():
    chunks = cm("underwriting_policy", "# T\n\n## Hard requirements\n\n" + "word " * 60)
    assert chunks
    c = chunks[0]
    assert c.metadata["document_type"] == "underwriting"
    assert c.metadata["source"] == "underwriting_policy.md"
    assert "Hard requirements" in c.text


def test_card_chunks_carry_card_id(cards):
    from app.rag.ingest import build_card_policy_chunks

    chunks = build_card_policy_chunks(cards)
    assert len(chunks) == len(cards)
    assert {c.metadata["card_id"] for c in chunks} == {c.card_id for c in cards}


def test_card_chunk_states_the_engines_own_numbers(cards):
    """The RAG text must quote the same income minimum the engine will check."""
    from app.rag.ingest import build_card_policy_chunks

    chunk = build_card_policy_chunks(cards)[0]
    card = cards[0]
    stripped = chunk.text.replace(",", "").replace(" lakh", "00000")
    assert str(card.min_monthly_income) in stripped
    assert f"{card.annual_fee}" in chunk.text.replace(",", "")
