"""Ranking.

Recommendation is a separate problem from eligibility, and it is solved here
in plain arithmetic so the answer is reproducible, inspectable and cheap to
explain. If the ordering of two cards cannot be justified by a number in
`score_breakdown`, it is a bug.

    net annual value = capped category rewards x 12
                     + lounge credit (only if wanted)
                     - annual fee actually payable
"""

from __future__ import annotations

from app import thresholds as T
from app.schemas import Card, ScoreBreakdown, UserProfile


def fee_payable(card: Card, profile: UserProfile) -> int:
    """The fee this customer would actually pay, waiver applied."""
    if not T.FEE_WAIVED_IF_SPEND_MEETS_THRESHOLD:
        return card.annual_fee
    if card.fee_waiver_spend is None:
        return card.annual_fee
    if profile.monthly_spend.total() * 12 >= card.fee_waiver_spend:
        return 0
    return card.annual_fee


def _wants_lounge(profile: UserProfile) -> bool:
    """Lounge value only counts for a customer who said they want it.

    Crediting it unconditionally would push travel cards up the list for people
    who never intend to use an airport lounge.
    """
    if not T.COUNT_LOUNGE_ONLY_IF_PREFERRED:
        return True
    return bool(T.LOUNGE_COUNTS_IF_PREFERRED & set(profile.preferences))


def category_rewards(card: Card, profile: UserProfile) -> float:
    """Annual rupee value of category rewards, caps respected."""
    monthly_total = 0.0
    for rule in card.reward_rules:
        spend = getattr(profile.monthly_spend, rule.category, 0)
        if spend <= 0:
            continue
        earned = spend * rule.value_pct / 100.0
        if rule.monthly_cap_rs is not None:
            earned = min(earned, rule.monthly_cap_rs)
        monthly_total += earned
    return monthly_total * 12


def lounge_credit(card: Card, profile: UserProfile) -> float:
    if card.lounge_visits_per_year <= 0 or not _wants_lounge(profile):
        return 0.0
    raw = card.lounge_visits_per_year * T.LOUNGE_VISIT_VALUE_RS
    return min(raw, T.LOUNGE_ANNUAL_CREDIT_CAP_RS)


def net_annual_value(card: Card, profile: UserProfile) -> int:
    value = category_rewards(card, profile) + lounge_credit(card, profile)
    value -= fee_payable(card, profile)
    # Never negative. A customer is not worse off for holding a card, and a
    # negative floor would put junk at the top of a lowest-value sort.
    return int(round(max(value, 0.0)))


def est_credit_limit(card: Card, profile: UserProfile) -> int:
    """Indicative limit = income x the tier's multiplier.

    Rounded down to the nearest 5,000, because that is how limits are granted
    and the rounding makes the number look like a sanction rather than a
    multiplication.
    """
    mult = T.TIERS[card.tier].limit_income_multiplier
    return int(profile.monthly_income * mult // 5000 * 5000)


# ------------------------------------------------------------- sub-scores
def _spend_alignment(card: Card, profile: UserProfile) -> float:
    """How much of the customer's spend this card actually rewards.

    Weighted by where the money goes, not by how many categories the card
    covers: a card with a 6% dining rate is worth nothing to someone who does
    not eat out.
    """
    spend = profile.monthly_spend.as_dict()
    total = sum(spend.values())
    if total <= 0:
        return 0.0
    best_rate = 0.0
    for cat, amount in spend.items():
        if amount <= 0:
            continue
        rule = card.rule_for(cat)
        if rule is None:
            continue
        # Cap-adjusted rate: a 5% rate capped at Rs 1,000 is not a 5% rate for a
        # customer spending Rs 40,000 in that category.
        cap = rule.monthly_cap_rs
        effective = rule.value_pct
        if cap is not None and amount * rule.value_pct / 100.0 > cap:
            effective = cap / amount * 100.0
        best_rate = max(best_rate, effective)
    # 5% back on your main category is a strong card; treat that as ~1.0.
    return min(best_rate / 5.0, 1.0)


def _matches_pref(pref: str, card: Card, profile: UserProfile) -> bool:
    if pref == "cashback":
        return any(r.category in ("other", "online_shopping") for r in card.reward_rules)
    if pref == "travel":
        return card.rule_for("travel") is not None or card.lounge_visits_per_year > 0
    if pref == "lounge":
        return card.lounge_visits_per_year > 0
    if pref == "fuel":
        return card.rule_for("fuel") is not None
    if pref == "lifetime_free":
        # Fee-free outright, or waived by this customer's own annual spend.
        if card.annual_fee == 0:
            return True
        annual_spend = profile.monthly_spend.total() * 12
        return bool(card.fee_waiver_spend and annual_spend >= card.fee_waiver_spend)
    return False


def _preference_match(card: Card, profile: UserProfile) -> float:
    """Did we recommend what they asked for? Neutral if they asked for nothing."""
    prefs = set(profile.preferences)
    if not prefs:
        return 0.5
    return sum(1 for p in prefs if _matches_pref(p, card, profile)) / len(prefs)


def _fee_fit(card: Card, profile: UserProfile) -> float:
    """Is the fee comfortable against the value the customer gets?

    A free card is a perfect fit; a card whose fee exceeds its value scores zero.
    """
    fee = fee_payable(card, profile)
    if fee == 0:
        return 1.0
    value = category_rewards(card, profile) + lounge_credit(card, profile)
    if value <= 0:
        return 0.0
    return max(0.0, min(1.0, value / fee / 2.0))


def _normalised_net(card: Card, profile: UserProfile) -> float:
    """Net value on 0..1, with no ceiling below 1.

    An earlier version clipped at the anchor, which made every strong card score
    exactly 1.0 and left the final order decided by a card_id tie-break.
    Saturating stops the ranking reflecting what the customer actually gains.

    net / (net + anchor): strictly increasing, always below 1, half a point at
    the anchor ("excellent").
    """
    net = net_annual_value(card, profile)
    return float(net / (net + T.NET_VALUE_ANCHOR_RS))


def score_breakdown(card: Card, profile: UserProfile) -> ScoreBreakdown:
    w = T.SCORING_WEIGHTS
    parts = {
        "net_value": _normalised_net(card, profile),
        "spend_alignment": _spend_alignment(card, profile),
        "preference_match": _preference_match(card, profile),
        "fee_fit": _fee_fit(card, profile),
    }
    total = sum(w[k] * v for k, v in parts.items())
    return ScoreBreakdown(card_id=card.card_id, total=round(total, 4), **parts)


def rank(
    cards: list[Card], profile: UserProfile
) -> tuple[list[tuple[Card, float, ScoreBreakdown]], list[Card]]:
    """Return (ranked cards with scores, cards dropped as not worth showing)."""
    scored: list[tuple[Card, float, ScoreBreakdown]] = []
    dropped: list[Card] = []

    for card in cards:
        if net_annual_value(card, profile) < T.MIN_NET_VALUE_RS:
            dropped.append(card)
            continue
        bd = score_breakdown(card, profile)
        scored.append((card, bd.total, bd))

    scored.sort(key=lambda t: (-t[1], t[0].card_id))

    if scored:
        floor = scored[0][1] * T.RELATIVE_SCORE_FLOOR
        dropped.extend(t[0] for t in scored if t[1] < floor)
        scored = [t for t in scored if t[1] >= floor]

    return scored[: T.MAX_RECOMMENDATIONS], dropped
