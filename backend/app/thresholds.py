"""Every threshold the engine enforces.

This file is the single source of truth for numbers, and it is Python on
purpose. The earlier design kept them in policy.yaml and *generated* the RAG
policy documents from it. That solved one problem (docs and DB could not
disagree) and created a worse one: a policy owner who edited the markdown had
to know there was a YAML behind it, and edit both.

The split now:

  this file          numbers the engine enforces. Nobody hand-edits these.
  data/policies/*.md prose a human reads and edits. Contains no thresholds.

A reason message is composed at runtime from the threshold, so
`reason("INCOME_BELOW_MIN", required=40_000, actual=32_000)` cannot drift from
the check that produced it - they are the same value passed twice. The markdown
explains *why* a rule exists and never states its number, so the two artefacts
have nothing to disagree about. `test_docs_have_no_thresholds` enforces that.

Precedence: business rules > catalogue (Postgres) > policy prose (Qdrant) > LLM.
"""

from __future__ import annotations

from dataclasses import dataclass

CURRENCY = "INR"

PRODUCT_NAME = "Bank Sathi"
PRODUCT_TAGLINE = "Credit cards, matched to how you actually spend."
DISCLAIMER = (
    "Bank Sathi is a demonstration system. Cards, banks and rates are fictional. "
    "Recommendations are illustrative and are not financial advice, an approval, "
    "or an offer of credit."
)

# ---------------------------------------------------------------------------
# Global gate. These reject the customer for the whole catalogue, before any
# card is looked at.
# ---------------------------------------------------------------------------
MIN_AGE = 18
MAX_AGE = 70

# Company-wide credit-score floor. Below this we recommend nothing at any tier.
MIN_CIBIL = 550
# At or below this, or with no history at all, the customer is treated as new to
# credit and offered starter cards only.
NEW_TO_CREDIT_CIBIL = 650

MAX_MISSED_PAYMENTS_12M = 0
MAX_RECENT_INQUIRIES_6M = 4
MAX_UTILIZATION_PCT = 90.0

# ---------------------------------------------------------------------------
# Required profile fields. Missing any means we ask, never guess. Order is the
# order we ask in.
# ---------------------------------------------------------------------------
REQUIRED_PROFILE_FIELDS = [
    "age",
    "monthly_income",
    "employment",
    "city_tier",
    "monthly_spend",
]

SPEND_CATEGORIES = [
    "fuel",
    "dining",
    "groceries",
    "online_shopping",
    "travel",
    "utilities",
    "other",
]

# ---------------------------------------------------------------------------
# Tiers. Used for assumed-field defaults in the catalogue generator and for the
# credit-limit estimate.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Tier:
    name: str
    order: int
    min_monthly_income: int
    min_cibil: int | None
    min_age: int
    max_age: int
    apr_pct: float
    limit_income_multiplier: float
    allowed_employment: tuple[str, ...]
    annual_fee_range: tuple[int, int]
    reward_value_pct_range: tuple[float, float]


TIERS: dict[str, Tier] = {
    "secured": Tier(
        name="secured", order=1,
        min_monthly_income=0, min_cibil=None,
        min_age=18, max_age=70,
        apr_pct=17.5, limit_income_multiplier=1.0,
        allowed_employment=("salaried", "self_employed", "student", "retired", "homemaker"),
        annual_fee_range=(0, 500), reward_value_pct_range=(0.5, 1.0),
    ),
    "entry": Tier(
        name="entry", order=2,
        min_monthly_income=15_000, min_cibil=650,
        min_age=21, max_age=65,
        apr_pct=28.0, limit_income_multiplier=2.0,
        allowed_employment=("salaried", "self_employed", "student", "business_owner"),
        annual_fee_range=(0, 1_500), reward_value_pct_range=(1.0, 2.5),
    ),
    "mid": Tier(
        name="mid", order=3,
        min_monthly_income=40_000, min_cibil=700,
        min_age=23, max_age=65,
        apr_pct=24.0, limit_income_multiplier=3.5,
        allowed_employment=("salaried", "self_employed", "business_owner"),
        annual_fee_range=(500, 5_000), reward_value_pct_range=(2.0, 4.0),
    ),
    "premium": Tier(
        name="premium", order=4,
        min_monthly_income=100_000, min_cibil=780,
        min_age=25, max_age=65,
        apr_pct=18.0, limit_income_multiplier=6.0,
        allowed_employment=("salaried", "self_employed", "business_owner"),
        annual_fee_range=(2_500, 20_000), reward_value_pct_range=(2.5, 5.0),
    ),
}

TIER_ORDER = sorted(TIERS, key=lambda t: TIERS[t].order)

# ---------------------------------------------------------------------------
# Reward valuation.
# ---------------------------------------------------------------------------
POINT_VALUE_RS = 0.25
LOUNGE_VISIT_VALUE_RS = 1_500
# Lounge benefit only counts toward value if the customer said they want it.
LOUNGE_COUNTS_IF_PREFERRED = frozenset({"travel", "lounge"})
LOUNGE_ANNUAL_CREDIT_CAP_RS = 20_000

# ---------------------------------------------------------------------------
# Net annual value and ranking. Weights must sum to 1.0; a test enforces it.
# ---------------------------------------------------------------------------
FEE_WAIVED_IF_SPEND_MEETS_THRESHOLD = True
MIN_NET_VALUE_RS = 500
# Drop anything scoring below this share of the top card.
RELATIVE_SCORE_FLOOR = 0.55
MAX_RECOMMENDATIONS = 5
# Net value on this scale counts as a full 0.5 in the net_value component.
NET_VALUE_ANCHOR_RS = 20_000.0
# Lounge value only counts toward a card's value if the customer wants it.
COUNT_LOUNGE_ONLY_IF_PREFERRED = True

WEIGHT_NET_VALUE = 0.55
WEIGHT_SPEND_ALIGNMENT = 0.20
WEIGHT_PREFERENCE_MATCH = 0.15
WEIGHT_FEE_FIT = 0.10

SCORING_WEIGHTS = {
    "net_value": WEIGHT_NET_VALUE,
    "spend_alignment": WEIGHT_SPEND_ALIGNMENT,
    "preference_match": WEIGHT_PREFERENCE_MATCH,
    "fee_fit": WEIGHT_FEE_FIT,
}

# ---------------------------------------------------------------------------
# Near miss. A "near miss" is a card the customer failed on exactly one hard
# rule, within this slack. It is how we turn a rejection into advice.
# ---------------------------------------------------------------------------
NEAR_MISS_ENABLED = True
NEAR_MISS_MAX_FAILED_RULES = 1
NEAR_MISS_INCOME_SLACK_RATIO = 0.75
NEAR_MISS_INCOME_SLACK_ABSOLUTE = 0
NEAR_MISS_CIBIL_SLACK = 25
NEAR_MISS_AGE_SLACK_YEARS = 3
# Only these failures are treated as "you were close". An on-file default is not.
NEAR_MISS_REASON_CODES = (
    "AGE_BELOW_MIN",
    "INCOME_BELOW_MIN",
    "CIBIL_BELOW_MIN",
    "EMPLOYMENT_NOT_ALLOWED",
)

# ---------------------------------------------------------------------------
# Reason codes. Label + message template + improvement advice.
#
# `message` is composed at runtime from the threshold that produced it, so it
# cannot disagree with the rule that generated it. The prose lives here rather
# than in a data file because it is code-adjacent: it is only ever correct
# alongside the comparison that fills it in.
# ---------------------------------------------------------------------------
REASON_LABELS = {
    "AGE_BELOW_MIN": "Age",
    "AGE_ABOVE_MAX": "Age limit",
    "INCOME_BELOW_MIN": "Minimum income",
    "CIBIL_BELOW_MIN": "Credit score",
    "CIBIL_BELOW_COMPANY_MIN": "Credit score",
    "EMPLOYMENT_NOT_ALLOWED": "Employment type",
    "CARD_INACTIVE": "Availability",
    "MISSED_PAYMENTS_PRESENT": "Payment history",
    "TOO_MANY_RECENT_INQUIRIES": "Recent applications",
    "UTILIZATION_TOO_HIGH": "Card usage",
    "INCOME_NOT_DECLARED": "Income",
    "NEW_TO_CREDIT": "Credit history",
}


def money(amount: float) -> str:
    """Rupee amount the way an Indian bank writes it."""
    return f"Rs {int(round(amount)):,}"


def reason(code: str, *, required=None, actual=None, gap=None) -> str:
    """Human-readable explanation of a failure.

    Every message names both the requirement and the customer's actual figure,
    because "you don't qualify" with no numbers is the single most useless
    sentence in this product. The numbers arrive from the rule that failed, so
    they are always the ones actually compared - there is no second copy of a
    threshold to fall out of date.
    """
    if code == "AGE_BELOW_MIN":
        return f"Applicants must be at least {required} years old, and you are {actual}."
    if code == "AGE_ABOVE_MAX":
        return f"This card is offered up to {required} years of age, and you are {actual}."
    if code == "INCOME_BELOW_MIN":
        return (
            f"This card needs a monthly income of at least {money(required)}, "
            f"and yours is {money(actual)}."
        )
    if code == "CIBIL_BELOW_MIN":
        return f"This card needs a credit score of at least {required}, and yours is {actual}."
    if code == "CIBIL_BELOW_COMPANY_MIN":
        return (
            f"We need a credit score of at least {MIN_CIBIL} before we can "
            f"recommend any card, and yours is {actual}."
        )
    if code == "EMPLOYMENT_NOT_ALLOWED":
        return f"This card is limited to {required} applicants, and you are {actual}."
    if code == "CARD_INACTIVE":
        return "This card is not currently open for applications."
    if code == "MISSED_PAYMENTS_PRESENT":
        return (
            f"Your credit file shows {actual} missed payment(s) in the last 12 "
            "months, and we can only assess applications with none."
        )
    if code == "TOO_MANY_RECENT_INQUIRIES":
        return (
            f"Your file shows {actual} credit enquiries in the last 6 months, "
            f"and our limit is {required}."
        )
    if code == "UTILIZATION_TOO_HIGH":
        return (
            f"Card usage is {actual}%, above the {MAX_UTILIZATION_PCT}% level "
            "we can assess an application at."
        )
    if code == "INCOME_NOT_DECLARED":
        return "We need your approximate monthly income before we can assess a card."
    if code == "NEW_TO_CREDIT":
        return (
            "You have a limited credit history, so this recommendation is "
            "limited to starter cards."
        )
    raise KeyError(f"Unknown reason code {code!r}")


def label(code: str) -> str:
    return REASON_LABELS[code]


def improve(code: str, *, required=None, gap=None) -> str:
    """Advice for clearing a failure. Never in IMPROVEMENT means no advice."""
    template = IMPROVEMENT.get(code)
    if template is None:
        return ""
    if code == "INCOME_BELOW_MIN" and gap is not None:
        return template.format(gap=money(gap))
    if code == "CIBIL_BELOW_MIN" and gap is not None:
        return template.format(gap=int(gap))
    return template.format(required=required, gap=gap)


# Generic, non-prescriptive advice. Deliberately never suggests taking on debt:
# borrowing to move a credit score works against the borrower and this product
# should not advise it.
IMPROVEMENT = {
    "AGE_BELOW_MIN": "This card becomes available to you at {required} years.",
    "INCOME_BELOW_MIN": (
        "You would need about {gap} more a month to meet this card's requirement."
    ),
    "CIBIL_BELOW_MIN": (
        "Your credit score is {gap} points short. Paying bills on time and keeping "
        "card usage low are the usual ways a score moves."
    ),
    "CIBIL_BELOW_COMPANY_MIN": (
        "Start with one of our secured cards, use it lightly and pay the full "
        "balance each month. About a year of on-time payments is usually enough "
        "to move up a tier."
    ),
    "NEW_TO_CREDIT": (
        "Start with a secured card, use it lightly, and pay the full balance "
        "every month. Roughly a year of on-time payments is usually enough to "
        "move up a tier."
    ),
    "MISSED_PAYMENTS_PRESENT": (
        "Set up auto-pay for at least the minimum due amount on every card. "
        "Missed payments are the heaviest factor holding a score down."
    ),
    "TOO_MANY_RECENT_INQUIRIES": (
        "Applying to several cards at once is itself a risk signal. Space "
        "applications a few months apart."
    ),
    "UTILIZATION_TOO_HIGH": (
        "Paying your statement balance in full each month, rather than revolving "
        "it, lowers usage and your score benefits."
    ),
}

FOLLOWUP_QUESTIONS = {
    "age": "How old are you?",
    "monthly_income": "What is your approximate monthly income?",
    "employment": (
        "Are you salaried, self-employed, a business owner, a student, retired, "
        "or a homemaker?"
    ),
    "city_tier": "Which city do you live in?",
    "monthly_spend": "Roughly how much do you spend on your card each month?",
}

PREFERENCE_QUESTION = (
    "Anything you care about more, like cashback, travel, lounge access or fuel? "
    "You can skip this."
)

FIELD_HELP = {
    "monthly_income": (
        "Take-home or gross, whichever you are comfortable sharing. We use it "
        "only to check minimum income."
    ),
    "cibil_score": (
        "Leave blank if you have never used credit. We will only suggest starter cards."
    ),
    "utilization_pct": "Card balance as a percentage of your limit.",
    "monthly_spend": "Rough figures are fine. We reward what you actually spend on.",
}

# Typical share of monthly income per spend category, per customer segment.
# Used only by the synthetic-profile generator, never by the engine: real
# spending comes from the customer. Shares sum to roughly 0.6, with the
# remainder left unallocated rather than forced into "other".
SPEND_SHARES = {
    "student": {
        "fuel": 0.02, "dining": 0.10, "groceries": 0.22, "online_shopping": 0.14,
        "travel": 0.06, "utilities": 0.08, "other": 0.38,
    },
    "early_career": {
        "fuel": 0.05, "dining": 0.09, "groceries": 0.14, "online_shopping": 0.18,
        "travel": 0.07, "utilities": 0.09, "other": 0.38,
    },
    "mid_career": {
        "fuel": 0.06, "dining": 0.08, "groceries": 0.13, "online_shopping": 0.12,
        "travel": 0.10, "utilities": 0.09, "other": 0.42,
    },
    "senior": {
        "fuel": 0.05, "dining": 0.07, "groceries": 0.15, "online_shopping": 0.06,
        "travel": 0.08, "utilities": 0.11, "other": 0.48,
    },
    "self_employed": {
        "fuel": 0.08, "dining": 0.09, "groceries": 0.12, "online_shopping": 0.09,
        "travel": 0.08, "utilities": 0.10, "other": 0.44,
    },
    "new_to_credit": {
        "fuel": 0.04, "dining": 0.08, "groceries": 0.18, "online_shopping": 0.12,
        "travel": 0.04, "utilities": 0.10, "other": 0.44,
    },
    "risky": {
        "fuel": 0.09, "dining": 0.07, "groceries": 0.16, "online_shopping": 0.10,
        "travel": 0.05, "utilities": 0.10, "other": 0.43,
    },
}
