"""Policy retrieval.

Retrieval always returns *evidence*, never an answer. What the retriever does
not do is more important: it never decides which card is eligible. A policy
chunk saying "minimum income Rs 40,000" is only used to explain a decision the
rule engine already made.
"""

from __future__ import annotations

from app.config import get_settings
from app import thresholds as T
from app.rag.store import get_embedder, get_store
from app.schemas import PolicyEvidence


def _query_for_card(card, profile) -> str:
    """A retrieval query built from computed facts, not from LLM prose.

    Using the card's own numbers makes retrieval precise, and it removes any
    opportunity for a prompt injection in a customer message to steer what
    policy gets surfaced.
    """
    bits = [
        card.segment,
        f"{card.tier} tier",
        f"minimum monthly income {card.min_monthly_income}",
        f"annual fee {card.annual_fee}",
    ]
    if card.min_cibil is not None:
        bits.append(f"minimum credit score {card.min_cibil}")
    if card.fee_waiver_spend:
        bits.append(f"fee waiver on {card.fee_waiver_spend} spend")
    if card.lounge_visits_per_year:
        bits.append("lounge access")
    return ", ".join(bits)


def _query_for_rejection(reason_code: str) -> str:
    return f"{T.label(reason_code).lower()}, {reason_code.replace('_', ' ').lower()}"


def _query_global(profile) -> str:
    bits = ["underwriting policy"]
    if profile.cibil_score is None:
        bits.append("no credit history, new to credit")
    else:
        bits.append(f"credit score {profile.cibil_score}")
    bits.append(f"monthly income {profile.monthly_income}")
    if profile.missed_payments_12m:
        bits.append("missed payments")
    if profile.utilization_pct:
        bits.append(f"card usage {round(profile.utilization_pct)} percent")
    return ", ".join(bits)


def _to_evidence(hit) -> PolicyEvidence:
    chunk, score = hit
    md = chunk.metadata
    return PolicyEvidence(
        source=str(md.get("source", "unknown")),
        card_id=(str(md["card_id"]) if md.get("card_id") else None),
        section=str(md.get("section", "")),
        text=chunk.text,
        score=round(float(score), 4),
    )


def retrieve_for_cards(cards, profile, per_card: int = 2) -> list[PolicyEvidence]:
    s = get_settings()
    store = get_store()
    embedder = get_embedder()
    out: list[PolicyEvidence] = []
    seen: set[str] = set()

    if cards:
        # One embedding call for the whole batch. Encoding five queries
        # individually meant five model invocations on the request path, and
        # measured ~5s of the request.
        queries = [_query_for_card(card, profile) for card in cards]
        vectors = embedder.embed(queries)
        for card, vec in zip(cards, vectors, strict=True):
            # Filtered by card_id so the explanation for card A can never be
            # grounded in card B's terms.
            hits = store.search(vec, limit=per_card, must={"card_id": card.card_id})
            for hit in hits:
                if hit[1] < s.rag_min_score:
                    continue
                ev = _to_evidence(hit)
                key = f"{ev.card_id}:{ev.section}"
                if key not in seen:
                    seen.add(key)
                    out.append(ev)

    if not out:
        # Fall back to the general reward-valuation page so an explanation is
        # never left with zero support.
        q = "how we value a card, net annual value, annual fee waiver"
        vec = embedder.embed([q])[0]
        for hit in store.search(vec, limit=s.rag_top_k):
            if hit[1] >= s.rag_min_score:
                ev = _to_evidence(hit)
                key = f"{ev.card_id}:{ev.section}"
                if key not in seen:
                    seen.add(key)
                    out.append(ev)
    return out


def retrieve_for_reason_codes(reason_codes: list[str], profile=None) -> list[PolicyEvidence]:
    s = get_settings()
    store = get_store()
    embedder = get_embedder()
    out: list[PolicyEvidence] = []
    seen: set[str] = set()
    queries = [_query_for_rejection(c) for c in reason_codes[:4]]
    if profile is not None:
        queries.append(_query_global(profile))
    if not queries:
        return out
    vectors = embedder.embed(queries)
    per = max(1, s.rag_top_k // max(1, len(queries)))
    for vec in vectors:
        for hit in store.search(vec, limit=per):
            if hit[1] < s.rag_min_score:
                continue
            ev = _to_evidence(hit)
            key = f"{ev.card_id}:{ev.section}"
            if key not in seen:
                seen.add(key)
                out.append(ev)
    return out[: s.rag_top_k * 2]
