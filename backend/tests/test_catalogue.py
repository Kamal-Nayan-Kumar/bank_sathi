"""Catalogue generation and the policy-document invariant."""

from __future__ import annotations

import re

import pytest

from app import thresholds as T
from app.catalogue import generate_cards
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


def test_tier_income_minimum_is_respected(cards):
    """Every card must sit at or above its tier's floor, or the tier table lies."""
    for c in cards:
        floor = T.TIERS[c.tier].min_monthly_income
        assert c.min_monthly_income >= floor, f"{c.card_id} {c.tier} below tier floor"


def test_tier_cibil_minimum_is_respected(cards):
    for c in cards:
        expected = T.TIERS[c.tier].min_cibil
        assert c.min_cibil == expected


def test_premium_cards_have_some_reward(cards):
    for c in cards:
        if c.tier == "premium":
            assert c.reward_rules


# --------------------------------------------------------------- policy docs
#
# The docs are hand-written markdown, not generated. The invariant is therefore
# the opposite of the old one: they must NOT contain thresholds. That is what
# makes a policy edit safe - the owner changes prose, the engine keeps its
# numbers, and nothing can drift because they were never in the same place.

# Rupee amounts, score thresholds and percentages, which the engine owns.
# The digit run must end in a digit: "[\d,]+" alone matches the comma in
# "cards," and flags ordinary prose.
_NUMERIC_CLAIM = re.compile(
    r"(?:\brs\.?\s*\d[\d,]*|\b\d{3,}\b|\b\d+(?:\.\d+)?\s*%)", re.IGNORECASE
)

# Numbers that are not thresholds: years as an age in prose ("a year of
# on-time payments"), and the one-litre/12-month style durations.
_ALLOWED_CONTEXT = re.compile(
    r"(twelve|one|about a|roughly a|a year|a month|per year)", re.IGNORECASE
)


def _policy_doc_text() -> dict[str, str]:
    from app.bootstrap import policy_dir

    return {
        p.stem: p.read_text()
        for p in sorted(policy_dir().glob("*.md"))
    }


def test_policy_docs_exist():
    from app.bootstrap import REQUIRED_POLICY_DOCS

    docs = _policy_doc_text()
    for name in REQUIRED_POLICY_DOCS:
        assert name in docs, f"missing policy document: {name}"


def test_policy_docs_contain_no_thresholds():
    """The whole point of hand-written docs.

    If a doc says "minimum credit score 550" it becomes a second source of
    truth, and the day a threshold moves in code the RAG layer will still
    quote the old number to a customer.
    """
    offenders = {}
    for name, body in _policy_doc_text().items():
        bad = []
        for line in body.split("\n"):
            for m in _NUMERIC_CLAIM.finditer(line):
                context = line
                if _ALLOWED_CONTEXT.search(context):
                    continue
                bad.append(m.group(0))
        if bad:
            offenders[name] = bad
    assert not offenders, (
        "policy markdown must not restate engine thresholds: "
        f"{offenders}. Put the number in backend/app/thresholds.py."
    )


def test_policy_docs_have_no_unformatted_placeholders():
    """A "{actual}" in an indexed doc gets copied to the customer or guessed."""
    for name, body in _policy_doc_text().items():
        assert not re.findall(r"\{[a-z_]+\}", body), name


def test_card_policy_chunks_have_no_placeholders(cards):
    from app.rag.ingest import build_card_policy_chunks

    for chunk in build_card_policy_chunks(cards):
        assert not re.findall(r"\{[a-z_]+\}", chunk.text), chunk.id


def test_scoring_weights_sum_to_one():
    assert sum(T.SCORING_WEIGHTS.values()) == pytest.approx(1.0)


def test_every_reason_code_has_a_label_and_a_message():
    for code in T.REASON_LABELS:
        assert T.label(code)
        # Codes with figures need both; informational ones need neither.
        assert isinstance(T.reason(code, required=1, actual=2, gap=1), str)


def test_unknown_reason_code_raises():
    """An unlabelled code reaching a customer is a bug, not a cosmetic problem."""
    with pytest.raises(KeyError):
        T.reason("NOT_A_REAL_CODE")


def test_improvement_copy_does_not_suggest_debt():
    """Guardrail: the advice layer must never advise taking on debt."""
    for code, text in T.IMPROVEMENT.items():
        lowered = text.lower()
        for banned in ("take a loan", "borrow", "personal loan", "apply for a loan"):
            assert banned not in lowered, f"{code} suggests debt: {lowered}"


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
