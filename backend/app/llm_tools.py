"""Chat tools available to the follow-up LLM.

Every tool here is read-only. There is deliberately no tool that can mark a
card eligible, change a rank or write to the database, because "agentic" must
not mean "the model gets to overrule the rules".

The tool layer reads through the same `db` and `policy` modules the engine
uses, so a tool answer cannot disagree with a decision.
"""

from __future__ import annotations

from app.db import get_all_cards, get_card
from app.policy import get_policy
from app.schemas import UserProfile

POLICY = get_policy()


def get_card_details(card_id: str) -> dict:
    card = get_card(card_id)
    if card is None:
        return {"error": f"No card with id {card_id}."}
    return {
        "card_id": card.card_id,
        "name": card.name,
        "bank": card.bank,
        "tier": card.tier,
        "annual_fee": card.annual_fee,
        "joining_fee": card.joining_fee,
        "fee_waiver_spend": card.fee_waiver_spend,
        "min_monthly_income": card.min_monthly_income,
        "min_cibil": card.min_cibil,
        "min_age": card.min_age,
        "max_age": card.max_age,
        "apr_pct": card.apr_pct,
        "lounge_visits_per_year": card.lounge_visits_per_year,
        "reward_rules": [r.model_dump() for r in card.reward_rules],
        "benefit_highlights": card.benefit_highlights,
    }


def list_cards(tier: str | None = None, limit: int = 20) -> list[dict]:
    cards = get_all_cards()
    if tier:
        cards = [c for c in cards if c.tier == tier]
    return [
        {"card_id": c.card_id, "name": c.name, "bank": c.bank, "tier": c.tier,
         "annual_fee": c.annual_fee}
        for c in cards[:limit]
    ]


def why_not_eligible(card_id: str, profile: UserProfile | None) -> dict:
    """Re-run the engine for one card. Never re-derives its own answer."""
    from app.rules.engine import evaluate_card, near_misses

    card = get_card(card_id)
    if card is None:
        return {"error": f"No card with id {card_id}."}
    if profile is None:
        return {"error": "No profile yet. Ask for the customer's details first."}
    ev = evaluate_card(profile, card)
    return {
        "card_id": card_id,
        "eligible": ev.eligible,
        "reasons": [r.model_dump() for r in ev.reasons],
        "near_miss": bool(near_misses([ev], {}, POLICY)),
    }


def compare_cards(card_ids: list[str], profile: UserProfile | None) -> dict:
    """Side-by-side value. Uses the scoring engine, not the model's opinion."""
    from app.rules.scoring import fee_payable, net_annual_value

    cards = [get_card(cid) for cid in card_ids]
    cards = [c for c in cards if c is not None]
    if not cards:
        return {"error": "None of those card ids exist."}
    rows = []
    for c in cards:
        net = net_annual_value(c, profile) if profile else None
        rows.append(
            {
                "card_id": c.card_id,
                "name": c.name,
                "tier": c.tier,
                "annual_fee": c.annual_fee,
                "fee_you_pay": fee_payable(c, profile) if profile else None,
                "net_annual_value_rs": net,
            }
        )
    return {"cards": rows}


def search_policy(query: str, limit: int = 4) -> list[dict]:
    from app.rag.retriever import retrieve_for_reason_codes

    # Free-text policy search is just the reason-code retriever with the query
    # treated as its own code list, so there is one retrieval path to reason
    # about rather than two.
    ev = retrieve_for_reason_codes([query], None)
    return [
        {"source": e.source, "section": e.section, "text": e.text[:600]}
        for e in ev[:limit]
    ]


TOOLS = {
    "get_card": get_card_details,
    "list_cards": list_cards,
    "why_not_eligible": why_not_eligible,
    "compare_cards": compare_cards,
    "search_policy": search_policy,
}

TOOL_DESCRIPTIONS = """You can call these read-only tools to answer a customer's
question about their recommendation. You cannot and must not decide whether
someone is eligible or change the order of the cards - those come from our
rules engine and are already fixed.

- get_card(card_id): full published terms of one card
- list_cards(tier?, limit?): browse the catalogue
- why_not_eligible(card_id): why this customer did not get a specific card
- compare_cards(card_ids): side-by-side value for this customer
- search_policy(query): our underwriting and reward policy pages

Use them when the customer asks about a specific card or asks "why not X?".
Never state a number that a tool did not give you."""
