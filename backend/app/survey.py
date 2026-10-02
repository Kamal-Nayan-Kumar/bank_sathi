"""Synthetic user profiles.

A seeded generator, not an LLM. Hand-written JSON gets repetitive in a way
that quietly flatters the system: every profile is clean, every number is
round, every answer is unambiguous. These deliberately include the awkward
cases the engine has to survive.

Ground truth is computed here with the same engine the graph uses, and that is
stated plainly in the README: the rule engine defines its own ground truth, so
its accuracy is *spec conformance*, not model accuracy.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

from app import thresholds as T
from app.config import get_settings
from app.rules.engine import evaluate_card, global_gate
from app.rules.scoring import rank
from app.schemas import Employment, SpendMix, UserProfile

SEGMENTS = [
    "student",
    "early_career",
    "mid_career",
    "senior",
    "self_employed",
    "new_to_credit",
    "risky",
]

SEGMENT_EMPLOYMENT = {
    "student": Employment.STUDENT,
    "early_career": Employment.SALARIED,
    "mid_career": Employment.SALARIED,
    "senior": Employment.SALARIED,
    "self_employed": Employment.SELF_EMPLOYED,
    "new_to_credit": Employment.SALARIED,
    "risky": Employment.SELF_EMPLOYED,
}

# Log-normal centre and spread of monthly income per segment.
SEGMENT_INCOME = {
    "student": (16_000, 0.25),
    "early_career": (48_000, 0.35),
    "mid_career": (110_000, 0.40),
    "senior": (190_000, 0.45),
    "self_employed": (85_000, 0.65),
    "new_to_credit": (32_000, 0.40),
    "risky": (55_000, 0.70),
}

SEGMENT_AGE = {
    "student": (20, 25),
    "early_career": (24, 31),
    "mid_career": (31, 44),
    "senior": (45, 62),
    "self_employed": (30, 52),
    "new_to_credit": (22, 34),
    "risky": (26, 45),
}


def _lognormal(rng: random.Random, median: float, sigma: float) -> int:
    value = rng.lognormvariate(math.log(median), sigma)
    return int(round(value / 500) * 500)


def _cibil(rng: random.Random, segment: str, missed: int, util: float) -> int | None:
    if segment == "new_to_credit":
        return rng.choice([None, None, 640, 680])
    base = {
        "student": 690,
        "early_career": 730,
        "mid_career": 780,
        "senior": 800,
        "self_employed": 715,
        "risky": 660,
    }[segment]
    score = base + rng.gauss(0, 35) - missed * 90 - util * 0.9
    return int(max(300, min(900, round(score))))


def _spend(rng: random.Random, segment: str, income: int) -> SpendMix:
    shares = T.SPEND_SHARES[segment]
    values = {c: int(round(income * shares.get(c, 0.0) * rng.uniform(0.7, 1.3) / 100) * 100)
              for c in T.SPEND_CATEGORIES}
    # Keep total spend inside the schema's plausibility bound.
    total = sum(values.values())
    cap = income * 2
    if total > cap and total > 0:
        scale = cap / total
        values = {c: int(v * scale / 100) * 100 for c, v in values.items()}
    return SpendMix(**values)


def generate_profiles(
    count: int = 300, seed: int | None = None, cards: list | None = None
) -> list[UserProfile]:
    seed = seed if seed is not None else get_settings().synthetic_seed
    rng = random.Random(seed)
    out: list[UserProfile] = []

    for i in range(count):
        segment = SEGMENTS[i % len(SEGMENTS)]
        income = _lognormal(rng, *SEGMENT_INCOME[segment])
        age = rng.randint(*SEGMENT_AGE[segment])
        missed = 0
        if segment == "risky" and rng.random() < 0.5:
            missed = rng.randint(1, 2)
        util = rng.uniform(5, 55) if segment != "risky" else rng.uniform(40, 88)

        prefs: list[str] = []
        if rng.random() < 0.6:
            prefs.append(rng.choice(["cashback", "travel", "lounge", "fuel"]))
        if rng.random() < 0.25:
            prefs.append("lifetime_free")

        out.append(
            UserProfile(
                profile_id=f"USER_{i + 1:04d}",
                age=age,
                monthly_income=income,
                employment=SEGMENT_EMPLOYMENT[segment],
                city_tier=rng.choices([1, 2, 3], weights=[0.5, 0.35, 0.15])[0],
                cibil_score=_cibil(rng, segment, missed, util),
                existing_cards=0 if segment in ("student", "new_to_credit") else rng.randint(1, 5),
                missed_payments_12m=missed,
                recent_inquiries_6m=rng.randint(0, 3),
                utilization_pct=round(util, 1),
                monthly_spend=_spend(rng, segment, income),
                preferences=prefs,
            )
        )
    return out


def edge_case_profiles(cards: list) -> list[UserProfile]:
    """Hand-written boundaries.

    Each one is a place the engine could plausibly be off by one. Generated
    profiles essentially never land exactly on a threshold, which is precisely
    the region worth testing.
    """
    from app.schemas import Card

    by_tier: dict[str, Card] = {}
    for c in cards:
        by_tier.setdefault(c.tier, c)

    entry = by_tier.get("entry")
    mid = by_tier.get("mid")
    premium = by_tier.get("premium")
    if not (entry and mid and premium):
        return []

    base = dict(
        employment=Employment.SALARIED,
        city_tier=1,
        existing_cards=2,
        missed_payments_12m=0,
        recent_inquiries_6m=0,
        utilization_pct=20.0,
        preferences=["cashback"],
    )
    spend = SpendMix(online_shopping=10_000, other=5_000)

    cases = [
        # income exactly on, one rupee under, one over
        ("EDGE_INCOME_EXACT", dict(age=30, monthly_income=mid.min_monthly_income,
                                   cibil_score=740, monthly_spend=spend, **base)),
        ("EDGE_INCOME_MINUS1", dict(age=30, monthly_income=mid.min_monthly_income - 1,
                                    cibil_score=740, monthly_spend=spend, **base)),
        ("EDGE_INCOME_PLUS1", dict(age=30, monthly_income=mid.min_monthly_income + 1,
                                   cibil_score=740, monthly_spend=spend, **base)),
        # credit score exactly on the card minimum
        ("EDGE_CIBIL_EXACT", dict(age=30, monthly_income=premium.min_monthly_income * 2,
                                  cibil_score=premium.min_cibil, monthly_spend=spend, **base)),
        ("EDGE_CIBIL_MINUS1", dict(age=30, monthly_income=premium.min_monthly_income * 2,
                                   cibil_score=premium.min_cibil - 1, monthly_spend=spend, **base)),
        # age window edges
        ("EDGE_AGE_AT_MIN", dict(age=mid.min_age, monthly_income=95_000,
                                 cibil_score=740, monthly_spend=spend, **base)),
        ("EDGE_AGE_MINUS1", dict(age=mid.min_age - 1, monthly_income=95_000,
                                 cibil_score=740, monthly_spend=spend, **base)),
        ("EDGE_AGE_AT_MAX", dict(age=mid.max_age, monthly_income=95_000,
                                 cibil_score=750, monthly_spend=spend, **base)),
        ("EDGE_AGE_MAXPLUS1", dict(age=mid.max_age + 1, monthly_income=95_000,
                                   cibil_score=750, monthly_spend=spend, **base)),
        # global gate edges
        ("EDGE_GATE_CIBIL_MIN", dict(age=35, monthly_income=80_000,
                                     cibil_score=550, monthly_spend=spend, **base)),
        ("EDGE_GATE_CIBIL_MINUS1", dict(age=35, monthly_income=80_000,
                                        cibil_score=549, monthly_spend=spend, **base)),
        ("EDGE_GATE_ONE_MISSED", dict(age=35, monthly_income=80_000, cibil_score=700,
                                      monthly_spend=spend, **{**base, "missed_payments_12m": 1})),
        ("EDGE_GATE_INQUIRIES_MAX", dict(age=35, monthly_income=80_000, cibil_score=700,
                                         monthly_spend=spend,
                                         **{**base, "recent_inquiries_6m": 4})),
        ("EDGE_GATE_INQUIRIES_OVER", dict(age=35, monthly_income=80_000, cibil_score=700,
                                          monthly_spend=spend,
                                          **{**base, "recent_inquiries_6m": 5})),
        ("EDGE_UTILISATION_MAX", dict(age=35, monthly_income=80_000, cibil_score=700,
                                      monthly_spend=spend, **{**base, "utilization_pct": 90.0})),
        ("EDGE_UTILISATION_OVER", dict(age=35, monthly_income=80_000, cibil_score=700,
                                       monthly_spend=spend, **{**base, "utilization_pct": 90.1})),
        # new to credit. These override `employment` and `existing_cards`, so
        # they are written as full overrides rather than `**base`.
        ("EDGE_NEW_TO_CREDIT", dict(age=22, monthly_income=24_000, cibil_score=None,
                                    existing_cards=0, employment=Employment.STUDENT,
                                    monthly_spend=SpendMix(online_shopping=6_000, other=3_000))),
        ("EDGE_NEW_TO_CREDIT_AT_THRESHOLD", dict(age=26, monthly_income=40_000,
                                                 cibil_score=650, existing_cards=0,
                                                 monthly_spend=spend)),
        # nothing eligible: poor and young with a thin file
        ("EDGE_NO_ELIGIBLE", dict(age=19, monthly_income=12_000, cibil_score=580,
                                  existing_cards=0, employment=Employment.STUDENT,
                                  monthly_spend=SpendMix(online_shopping=4_000))),
        # contradictory preferences: wants lounge and no fee at a low income
        ("EDGE_CONTRADICTORY", dict(age=27, monthly_income=32_000, cibil_score=690,
                                    monthly_spend=SpendMix(travel=3_000, other=2_000),
                                    preferences=["lounge", "lifetime_free", "cashback"])),
        # very high income, minimal spend
        ("EDGE_HIGH_INCOME_LOW_SPEND", dict(age=45, monthly_income=900_000, cibil_score=830,
                                            monthly_spend=SpendMix(other=5_000), **base)),
        # zero-ish spend
        ("EDGE_NO_SPEND", dict(age=30, monthly_income=60_000, cibil_score=730,
                               monthly_spend=SpendMix(), **base)),
    ]

    out = []
    for name, overrides in cases:
        # Per-case values win over the shared defaults, so a case can override
        # employment or existing_cards without colliding with **base.
        merged = {**base, **overrides}
        try:
            out.append(UserProfile(profile_id=name, **merged))
        except ValueError:
            # An edge case that fails schema validation is itself a finding, but
            # it must not stop the rest of the suite from running.
            continue
    return out


# ------------------------------------------------------------------ ground truth
def ground_truth(profile: UserProfile, cards: list) -> dict:
    """The engine's own answer, computed without the graph, LLM or RAG."""
    gate = global_gate(profile)
    # Pair each evaluation with its card *before* filtering. Filtering first and
    # zipping after silently misaligns the two lists, which produces a ground
    # truth that disagrees with the engine on exactly the withdrawn cards.
    evaluated = [(c, evaluate_card(profile, c)) for c in cards if c.is_active]
    eligible = [c for c, ev in evaluated if ev.eligible]
    rejected = [ev for _, ev in evaluated if not ev.eligible]
    ranked, _ = rank(eligible, profile)
    return {
        "profile_id": profile.profile_id,
        "gate_passed": gate.passed,
        "gate_reason_codes": [r.code for r in gate.reasons],
        "eligible_card_ids": sorted(c.card_id for c in eligible),
        "rejected_card_ids": sorted(ev.card_id for ev in rejected),
        "expected_top5": [c.card_id for c, _, _ in ranked],
        "expected_top1": ranked[0][0].card_id if ranked else None,
    }


# ------------------------------------------------------------------ persistence
def write_profiles(profiles: list[UserProfile], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for p in profiles:
            fh.write(json.dumps(p.model_dump(mode="json"), ensure_ascii=False) + "\n")


def read_profiles(path: Path) -> list[UserProfile]:
    if not path.exists():
        return []
    out = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(UserProfile.model_validate_json(line))
    return out


def load_examples() -> dict:
    """A small, curated set for the demo UI.

    Curated rather than sampled: a demo whose first profile is rejected teaches
    a viewer nothing about what the system does well.
    """
    settings = get_settings()
    path = settings.resolve("data/profiles.jsonl")
    profiles = read_profiles(path)
    if not profiles:
        return {"profiles": [], "note": "Run `make data` to generate sample profiles."}

    from app.db import get_all_cards

    cards = get_all_cards()
    buckets: dict[str, list[dict]] = {
        "recommended": [],
        "borderline": [],
        "rejected": [],
        "edge_case": [],
    }
    for p in profiles:
        if p.profile_id.startswith("EDGE_"):
            buckets["edge_case"].append(_example(p))
            continue
        gt = ground_truth(p, cards)
        if not gt["gate_passed"] or not gt["eligible_card_ids"]:
            buckets["rejected"].append(_example(p))
        elif _is_borderline(p, cards):
            buckets["borderline"].append(_example(p))
        else:
            buckets["recommended"].append(_example(p))
        if all(len(v) >= 4 for v in buckets.values()):
            break

    return {
        "note": "Synthetic profiles generated from a fixed seed. No real people.",
        "buckets": {k: v[:6] for k, v in buckets.items()},
    }


def _is_borderline(profile: UserProfile, cards: list) -> bool:
    """Close to a card's requirement, not comfortably past it."""
    for c in cards:
        if not c.is_active:
            continue
        ev = evaluate_card(profile, c)
        if ev.eligible:
            continue
        if any(r.near_miss for r in ev.reasons):
            return True
    return False


def _example(p: UserProfile) -> dict:
    return {"profile_id": p.profile_id, "profile": p.model_dump(mode="json")}
