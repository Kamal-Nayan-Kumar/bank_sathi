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

from app.policy import Policy, get_policy
from app.schemas import Card, ScoreBreakdown, UserProfile


def fee_payable(card: Card, profile: UserProfile, policy: Policy | None = None) -> int:
    """The fee this customer would actually pay, waiver applied."""
    policy = policy or get_policy()
    if not policy.fee_waived_if_spend_meets_threshold:
        return card.annual_fee
    if card.fee_waiver_spend is None:
        return card.annual_fee
    annual_spend = profile.monthly_spend.total() * 12
    if annual_spend >= card.fee_waiver_spend:
        return 0
    return card.annual_fee


def _wants_lounge(profile: UserProfile, policy: Policy) -> bool:
    """Lounge value only counts for a customer who said they want it.

    Crediting it unconditionally would push travel cards up the list for people
    who never intend to use an airport lounge.
    """
    if not policy.count_lounge_only_if_preferred:
        return True
    return bool(policy.lounge_counts_if_preferred & set(profile.preferences))


def category_rewards(card: Card, profile: UserProfile, policy: Policy | None = None) -> float:
    """Annual rupee value of category rewards, caps respected."""
    policy = policy or get_policy()
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


def lounge_credit(card: Card, profile: UserProfile, policy: Policy | None = None) -> float:
    policy = policy or get_policy()
    if card.lounge_visits_per_year <= 0 or not _wants_lounge(profile, policy):
        return 0.0
    raw = card.lounge_visits_per_year * policy.lounge_visit_value_rs
    return min(raw, policy.lounge_annual_credit_cap_rs)


def net_annual_value(card: Card, profile: UserProfile, policy: Policy | None = None) -> int:
    policy = policy or get_policy()
    value = category_rewards(card, profile, policy) + lounge_credit(card, profile, policy)
    value -= fee_payable(card, profile, policy)
    # A card can never be negative value. A customer is not worse off for
    # holding it, and a negative floor would put junk at the top of a
    # lowest-net-value sort.
    return int(round(max(value, 0.0)))


def est_credit_limit(card: Card, profile: UserProfile, policy: Policy | None = None) -> int:
    """Indicative limit = income x the tier's multiplier.

    Rounded down to the nearest 5,000 because that is how limits are actually
    granted, and the rounding makes the estimate look like a sanction rather
    than a multiplication.
    """
    policy = policy or get_policy()
    mult = float(policy.tier(card.tier)["limit_income_multiplier"])
    raw = profile.monthly_income * mult
    return int(raw // 5000 * 5000)


# ------------------------------------------------------------- sub-scores
def _spend_alignment(card: Card, profile: UserProfile, policy: Policy) -> float:
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
        # Cap-adjusted rate: a 5% rate capped at Rs 1,000 is not a 5% rate for
        # a customer spending Rs 40,000 in that category.
        cap = rule.monthly_cap_rs
        effective = rule.value_pct
        if cap is not None and amount * rule.value_pct / 100.0 > cap:
            effective = cap / amount * 100.0
        best_rate = max(best_rate, effective)
    # 5% back on your main category is a strong card; treat that as ~1.0.
    return min(best_rate / 5.0, 1.0)


def _preference_match(card: Card, profile: UserProfile, policy: Policy) -> float:
    """Did we recommend what they asked for? 0 if they asked for nothing."""
    prefs = set(profile.preferences)
    if not prefs:
        return 0.5  # neutral, not penalised and not rewarded
    hits = 0
    for pref in prefs:
        if _matches_pref(pref, card, profile, policy):
            hits += 1
    return hits / len(prefs)


def _matches_pref(pref: str, card: Card, profile: UserProfile, policy: Policy) -> bool:
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


def _fee_fit(card: Card, profile: UserProfile, policy: Policy) -> float:
    """Is the fee comfortable against the value the customer gets?

    A free card is a perfect fit. A card whose fee exceeds the value it returns
    scores zero.
    """
    fee = fee_payable(card, profile, policy)
    if fee == 0:
        return 1.0
    value = category_rewards(card, profile, policy) + lounge_credit(card, profile, policy)
    if value <= 0:
        return 0.0
    return max(0.0, min(1.0, value / fee / 2.0))


def score_breakdown(
    card: Card, profile: UserProfile, policy: Policy | None = None
) -> ScoreBreakdown:
    policy = policy or get_policy()
    w = policy.scoring_weights
    parts = {
        "net_value": _normalised_net(card, profile, policy),
        "spend_alignment": _spend_alignment(card, profile, policy),
        "preference_match": _preference_match(card, profile, policy),
        "fee_fit": _fee_fit(card, profile, policy),
    }
    total = sum(w[k] * v for k, v in parts.items())
    return ScoreBreakdown(card_id=card.card_id, total=round(total, 4), **parts)


def _normalised_net(card: Card, profile: UserProfile, policy: Policy) -> float:
    """Net value on a 0..1 scale, with no ceiling below 1.0.

    An earlier version clipped at the anchor, which made every strong card
    score exactly 1.0 and left the final order decided by a card_id
    tie-break. Saturating means the ranking stops reflecting what the customer
    gains, so the curve keeps rising past the anchor and flattens instead.

    Rs 20,000/yr is treated as "excellent"; 60,000 as exceptional. The
    asymptotic curve reaches ~0.75 at the anchor, leaving headroom for the
    genuinely better card to win.
    """
    net = net_annual_value(card, profile, policy)
    anchor = 20_000.0
    # net / (net + anchor): strictly increasing, always below 1. Half a point at
    # Rs 20,000/yr ("excellent"), three quarters at Rs 60,000 ("exceptional").
    return float(net / (net + anchor))


def rank(
    cards: list[Card], profile: UserProfile, policy: Policy | None = None
) -> tuple[list[tuple[Card, float, ScoreBreakdown]], list[Card]]:
    """Return (ranked cards with scores, cards dropped as not worth showing)."""
    policy = policy or get_policy()
    scored: list[tuple[Card, float, ScoreBreakdown]] = []
    dropped: list[Card] = []

    for card in cards:
        net = net_annual_value(card, profile, policy)
        if net < policy.min_net_value_rs:
            dropped.append(card)
            continue
        bd = score_breakdown(card, profile, policy)
        scored.append((card, bd.total, bd))

    scored.sort(key=lambda t: (-t[1], t[0].card_id))

    if scored:
        top = scored[0][1]
        floor = top * policy.relative_score_floor
        kept = [t for t in scored if t[1] >= floor]
        dropped.extend(t[0] for t in scored if t[1] < floor)
        scored = kept

    return scored[: policy.max_recommendations], dropped
