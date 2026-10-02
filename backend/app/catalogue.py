"""Card catalogue generation.

A template-plus-parameter generator, not an LLM. Handing a language model "make
me 120 credit cards" produces cards whose fee contradicts their own reward
caps. Templates guarantee internal consistency; the seed guarantees the same
120 cards on every machine.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from app import thresholds as T
from app.config import REPO_ROOT
from app.schemas import Card, CardTier, Employment, RewardRule

BANKS = [
    "Sundara Bank",
    "Meridian Co-operative Bank",
    "Kanchan Credit Union",
    "Northwind Financial",
    "Ashoka Union Bank",
    "Triveni Bank",
    "Rajpath Financial",
    "Coastline Bank",
]

SEGMENTS = [
    "cashback",
    "travel",
    "fuel",
    "dining",
    "shopping",
    "premium",
    "beginner",
    "business",
    "lifestyle",
    "student",
]

NAME_PREFIX = {
    "cashback": ["Cash", "Cashback", "Everyday"],
    "travel": ["Skywards", "Voyager", "Miles", "Aero"],
    "fuel": ["Fuel", "Petrol", "Drive"],
    "dining": ["Dine", "Table", "Bistro"],
    "shopping": ["Shop", "Bazaar", "Cart"],
    "premium": ["Signature", "Platinum", "Reserve"],
    "beginner": ["Starter", "First", "Nova"],
    "business": ["Biz", "Enterprise", "Trade"],
    "lifestyle": ["Life", "Urban", "Vibe"],
    "student": ["Campus", "Scholar", "Buddy"],
}

NAME_SUFFIX = ["Card", "Rewards", "One", "Prime", "Select", "Edge", "Max"]

# Value of a reward point, declared per card so the point-value arithmetic is
# visible in the data rather than buried in the scoring engine.
POINT_VALUES = [0.2, 0.25, 0.25, 0.3, 0.4, 0.5]

# Realistic-ish rates (percent of spend) for a reward category.
SEGMENT_RATES = {
    "cashback": {"other": (0.75, 1.5), "online_shopping": (1.5, 3.0), "groceries": (0.75, 1.5)},
    "travel": {"travel": (3.0, 6.0), "online_shopping": (1.0, 2.0)},
    "fuel": {"fuel": (2.5, 5.0), "groceries": (0.5, 1.0)},
    "dining": {"dining": (3.0, 6.0), "other": (0.5, 1.0)},
    "shopping": {"online_shopping": (3.0, 7.0), "other": (0.5, 1.0)},
    "premium": {"travel": (2.0, 4.0), "dining": (2.0, 4.0), "online_shopping": (1.5, 3.0)},
    "beginner": {"other": (1.0, 2.0), "online_shopping": (2.0, 3.5), "fuel": (1.0, 2.0)},
    "business": {"utilities": (1.5, 3.0), "travel": (2.0, 3.5), "other": (0.5, 1.0)},
    "lifestyle": {"dining": (2.0, 4.0), "online_shopping": (1.5, 3.0), "travel": (1.0, 2.5)},
    "student": {"online_shopping": (1.5, 3.0), "food_delivery_placeholder": (0.0, 0.0)},
}
# The student template above is deliberately left with a spare key; drop it so
# only real categories survive into the catalogue.
SEGMENT_RATES["student"] = {"online_shopping": (1.5, 3.0), "other": (0.5, 1.5)}

# Which tier a segment sits in, and how much of the tier range it may use.
SEGMENT_TIER = {
    "cashback": ("entry", "mid"),
    "travel": ("mid", "premium"),
    "fuel": ("entry", "mid"),
    "dining": ("entry", "mid"),
    "shopping": ("entry", "mid"),
    "premium": ("premium", "premium"),
    "beginner": ("secured", "entry"),
    "business": ("mid", "premium"),
    "lifestyle": ("mid", "premium"),
    "student": ("secured", "entry"),
}

BENEFITS = {
    "cashback": ["Unlimited cashback on partner merchants", "Auto-pay discount on every statement"],
    "travel": ["Airport lounge access", "Free flight seat upgrades", "Zero foreign transaction markup"],
    "fuel": ["5% cashback on fuel up to Rs 5,000/month", "Free vehicle insurance add-on"],
    "dining": ["2x points at partner restaurants", "Free dining reservations above Rs 4,000"],
    "shopping": ["Instant discount on partner app", "Extended warranty on electronics"],
    "premium": ["Dedicated relationship manager", "Complimentary airport lounge", "Concierge travel desk"],
    "beginner": ["No joining fee", "One month fee waiver each year", "Free virtual card"],
    "business": ["Expense insights dashboard", "Higher cash withdrawal limit", "Working capital offers"],
    "lifestyle": ["Movie and streaming benefits", "Health and fitness partner discounts"],
    "student": [
        "No annual fee for the first year",
        "Fee waiver on Rs 20,000 annual spend",
        "Budget EMI options",
    ],
}


def _lerp(low: float, high: float, t: float) -> float:
    return low + (high - low) * t


def _build_card(idx: int, rng: random.Random) -> Card:
    segment = SEGMENTS[idx % len(SEGMENTS)]
    tier_lo, tier_hi = SEGMENT_TIER[segment]
    tiers_in_order = ["secured", "entry", "mid", "premium"]
    lo_i, hi_i = tiers_in_order.index(tier_lo), tiers_in_order.index(tier_hi)

    # Vary the tier within the segment's allowed band so a segment is not a
    # single tier, and record where in the band this card sits.
    tier_idx = rng.choice(range(lo_i, hi_i + 1))
    tier: CardTier = tiers_in_order[tier_idx]
    t = (
        0.5
        if lo_i == hi_i
        else (tier_idx - lo_i + rng.uniform(0.15, 0.85)) / (hi_i - lo_i + 1)
    )
    tier_cfg = T.TIERS[tier]

    # --- Identity -----------------------------------------------------------
    bank = rng.choice(BANKS)
    prefix = rng.choice(NAME_PREFIX[segment])
    suffix = rng.choice(NAME_SUFFIX)
    name = f"{prefix} {suffix}"
    card_id = f"CARD_{idx + 1:03d}"
    network = rng.choice(["Visa", "Mastercard", "RuPay", "Amex"])

    # --- Money --------------------------------------------------------------
    fee_lo, fee_hi = tier_cfg.annual_fee_range
    annual_fee = int(round(_lerp(fee_lo, fee_hi, t) / 50.0) * 50)
    joining_fee = 0 if rng.random() < 0.55 else int(round(annual_fee * rng.uniform(0.2, 0.5) / 50) * 50)

    # A waiver threshold is only meaningful on a card that charges a fee. On a
    # zero-fee card it invites the explanation "your fee is waived because you
    # spend Rs 19,000", which is nonsense the model will happily produce.
    if annual_fee == 0:
        fee_waiver_spend = None
    elif rng.random() < 0.7:
        # Threshold must sit comfortably above the fee, or waiving is free money.
        spend_mult = rng.uniform(6.0, 12.0)
        fee_waiver_spend = int(round(annual_fee * spend_mult / 1000.0) * 1000)
    else:
        fee_waiver_spend = None

    income_lo, income_hi = (
        tier_cfg.min_monthly_income,
        tier_cfg.min_monthly_income * 3.5 if tier_cfg.min_monthly_income > 0 else 0,
    )
    min_income = (
        int(round(_lerp(income_lo, income_hi, t) / 1000.0) * 1000)
        if income_hi > 0
        else 0
    )
    min_cibil = tier_cfg.min_cibil
    apr = round(_lerp(tier_cfg.apr_pct, tier_cfg.apr_pct + 6.0, rng.random()), 1)
    lounge = 0
    if segment in ("travel", "premium") and tier in ("mid", "premium"):
        lounge = rng.choice([2, 4, 6, 8, 12])

    # --- Rewards ------------------------------------------------------------
    point_value = rng.choice(POINT_VALUES)
    reward_rules: list[RewardRule] = []
    for category, (rate_lo, rate_hi) in SEGMENT_RATES[segment].items():
        if rate_hi <= 0:
            continue
        value_pct = round(_lerp(rate_lo, rate_hi, t) * point_value / 0.25, 3)
        cap = None
        if rng.random() < 0.65:
            cap = int(round(_lerp(3_000, 25_000, rng.random()) / 500.0) * 500)
        reward_rules.append(
            RewardRule(category=category, value_pct=value_pct, monthly_cap_rs=cap)
        )
    # Every card earns something in "other" so no card is literally inert on a
    # customer who spends mostly uncategorised.
    if not any(r.category == "other" for r in reward_rules):
        reward_rules.append(
            RewardRule(
                category="other",
                value_pct=round(rng.uniform(0.4, 1.2), 2),
                monthly_cap_rs=rng.choice([None, 5_000, 10_000]),
            )
        )

    # These three have no published analogue in a template catalogue; tier
    # defaults and the segment bands above are our assumption, so they are
    # disclosed on every card rather than only in the README.
    assumed = ["apr_pct", "lounge_visits_per_year", "reward_caps"]

    card = Card(
        card_id=card_id,
        name=name,
        bank=bank,
        network=network,
        segment=segment,
        tier=tier,
        annual_fee=annual_fee,
        joining_fee=joining_fee,
        fee_waiver_spend=fee_waiver_spend,
        min_monthly_income=min_income,
        min_cibil=min_cibil,
        min_age=tier_cfg.min_age,
        max_age=tier_cfg.max_age,
        allowed_employment=[Employment(e) for e in tier_cfg.allowed_employment],
        apr_pct=apr,
        lounge_visits_per_year=lounge,
        reward_rules=reward_rules,
        benefit_highlights=rng.sample(BENEFITS[segment], k=min(2, len(BENEFITS[segment]))),
        assumed_fields=sorted(set(assumed)),
        source="synthetic:tier-template",
    )
    return card


def generate_cards(count: int = 120, seed: int = 20_240_607) -> list[Card]:
    """Deterministic: same seed and count gives byte-identical output."""
    rng = random.Random(seed)
    cards = [_build_card(i, rng) for i in range(count)]

    # A handful of cards are withdrawn, so `is_active` is exercised.
    for card in rng.sample(cards, k=max(2, count // 30)):
        card.is_active = False

    # Roughly a quarter sit exactly on a tier minimum income, so boundary tests
    # have something realistic to sit on.
    for card in cards:
        if card.min_monthly_income > 0 and rng.random() < 0.25:
            card.min_monthly_income = int(card.min_monthly_income / 1000) * 1000

    seen: set[str] = set()
    for card in cards:
        key = f"{card.bank}|{card.name}"
        while key in seen:  # deterministic disambiguation
            card.name = f"{card.name} {card.segment.title()}"
            key = f"{card.bank}|{card.name}"
        seen.add(key)
    return cards


def main(count: int = 120, seed: int = 20_240_607) -> None:
    cards = generate_cards(count, seed=seed)
    out: Path = REPO_ROOT / "data" / "cards.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps([c.model_dump(mode="json") for c in cards], indent=2, ensure_ascii=False)
    )
    by_tier: dict[str, int] = {}
    by_seg: dict[str, int] = {}
    for c in cards:
        by_tier[c.tier] = by_tier.get(c.tier, 0) + 1
        by_seg[c.segment] = by_seg.get(c.segment, 0) + 1
    print(f"wrote {len(cards)} cards -> {out}")
    print("by tier :", dict(sorted(by_tier.items())))
    print("by segment:", dict(sorted(by_seg.items())))


if __name__ == "__main__":
    import sys

    main(int(sys.argv[1]) if len(sys.argv) > 1 else 120)
