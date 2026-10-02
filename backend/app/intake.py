"""Intake: natural language -> profile.

Two implementations behind one interface:

* `LLMExtractor` asks Groq for a `PartialProfile`. It is deliberately told to
  leave fields null rather than guess, because a wrong income produces a
  confident, wrong recommendation.
* `RegexExtractor` is the offline fallback. It handles the formats people
  actually use ("1.2L", "95k a month", "820 CIBIL") and returns null for
  everything else, which the graph turns into a follow-up question.

The regex path is not a stub. It is what runs in CI, and its accuracy is
measured in the evaluation harness.
"""

from __future__ import annotations

import logging
import re

from app import llm
from app.config import get_settings
from app import thresholds as T
from app.schemas import Employment, PartialProfile, SpendMix

log = logging.getLogger(__name__)

EXTRACT_SYSTEM = """You turn a customer's message into a credit-card profile.

Rules you must follow:
- Use ONLY what the customer actually said. Never guess, estimate, or fill in a
  value they did not give. A missing field must stay null.
- "1.2L", "1.2 lakh", "12 lakh" mean 1200000. "95k", "95k a month", "95,000"
  mean 95000. "1.5L per month" means 15000 monthly.
- "820 CIBIL", "CIBIL of 820", "credit score 820" -> cibil_score 820.
- city_tier: 1 for Delhi, Mumbai, Bengaluru, Bangalore, Hyderabad, Chennai,
  Kolkata, Pune, Gurgaon, Noida, Jaipur, Ahmedabad. 2 for other metros and
  state capitals. 3 for smaller towns.
- employment: salaried, self_employed, business_owner, student, retired, homemaker.
  "founder", "freelancer", "consultant" -> self_employed.
- monthly_spend is the split of their monthly card spend. Put anything they did
  not break down into "other".
- preferences can include: cashback, travel, lounge, fuel, lifetime_free.
  "no annual fee" or "lifetime free" -> lifetime_free.
- Reply with JSON only, no commentary.

JSON shape:
{
  "age": int|null, "monthly_income": int|null, "employment": string|null,
  "city_tier": 1|2|3|null, "cibil_score": int|null, "existing_cards": int|null,
  "missed_payments_12m": int|null, "recent_inquiries_6m": int|null,
  "utilization_pct": float|null, "max_annual_fee": int|null,
  "preferences": [string]|null,
  "monthly_spend": {
    "fuel": int, "dining": int, "groceries": int, "online_shopping": int,
    "travel": int, "utilities": int, "other": int
  }|null
}"""

FOLLOWUP_SYSTEM = """You write one short follow-up question for a credit-card
applicant. Ask for the single most important missing detail. Never ask for more
than one thing. Never ask for anything they already told you. Never ask for a
PAN, Aadhaar number, card number or CVV. Reply with the question only."""


# ---------------------------------------------------------------- money parsing
_LAKH = re.compile(r"(?:₹|rs\.?|inr)?\s*([\d.]+)\s*(?:l|lakh|lac)", re.I)
_CRORE = re.compile(r"(?:₹|rs\.?|inr)?\s*([\d.]+)\s*(?:cr|crore)", re.I)
_K = re.compile(r"(?:₹|rs\.?|inr)?\s*([\d.]+)\s*(k\b|k /|k per|k a month|thousand)", re.I)
_PLAIN = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)", re.I)
# "95,000 per month" with no currency marker. Anchored to the start of the
# string parse_amount was handed, and only reached from cue-anchored callers,
# so a bare number elsewhere in a message cannot be picked up as an amount.
_BARE = re.compile(r"^\s*([\d,]+(?:\.\d+)?)")
_PER_MONTH = re.compile(r"(?:per month|/ ?month|a month|monthly|pm\b)", re.I)
_PER_YEAR = re.compile(r"(?:per year|/ ?year|a year|annually|p\.?a\.?\b|pa\b|lpa\b)", re.I)


def parse_amount(text: str, context: str | None = None) -> tuple[int | None, str]:
    """Parse a rupee amount and its period. Returns (monthly_rupees, unit).

    `text` is the amount fragment ("1.2L", "60,000"). `context` is the clause it
    came from, used only to decide monthly vs annual, and defaults to `text`
    itself so a bare call still reads the period from whatever it was given.

    They are separate arguments because period cues appear on both sides of the
    amount ("monthly income is 45000" vs "1.2L per month") and gluing the two
    together would let the number's own leading digits absorb the cue words.

    Normalising to a monthly figure here means the rest of the system only ever
    deals in one unit, and "12 lakh per annum" cannot become an income of
    12,000 a month by accident.
    """
    context = text if context is None else context
    m = _CRORE.search(text)
    if m:
        annual = float(m.group(1)) * 10_000_000
        return _period(annual, context), "crore"
    m = _LAKH.search(text)
    if m:
        amount = float(m.group(1)) * 100_000
        return _period(amount, context), "lakh"
    m = _K.search(text)
    if m:
        amount = float(m.group(1)) * 1_000
        return _period(amount, context), "k"
    m = _PLAIN.search(text) or _BARE.match(text)
    if m:
        amount = float(m.group(1).replace(",", ""))
        return _period(amount, context), "plain"
    return None, "none"


def _period(amount: float, text: str) -> int:
    if _PER_MONTH.search(text):
        return int(round(amount))
    if _PER_YEAR.search(text):
        return int(round(amount / 12))
    # A bare "I earn 1.2L" is ambiguous; Indian salary conversation treats it
    # as annual, so annual is the safer default and it is what we normalise to.
    return int(round(amount / 12))


def _clause(text: str, end: int, width: int = 24) -> str:
    """The rest of the clause after a match, stopping at a natural boundary.

    Without this, a monthly cue belonging to a *different* number in the same
    sentence silently annualises or de-annualises this one.
    """
    tail = text[end : end + width]
    for stop in (",", ";", " and ", " but ", " while "):
        idx = tail.lower().find(stop)
        if idx != -1:
            tail = tail[:idx]
    return " ".join(tail.split())


_CIBIL = re.compile(r"(?:cibil|credit\s*score)\D{0,12}(\d{3})", re.I)
# An age needs an explicit cue. A bare "24" in a sentence is far more likely to
# be a spend or a score, and guessing an age is not recoverable downstream.
_AGE = re.compile(
    r"\b(?:i\s*'?m|i\s*am|age\s*is|age)\s*(\d{2})\b"
    r"|\b(\d{2})\s*(?:years?\s*old|yrs?\s*old|y/?o\b)",
    re.I,
)
_CARDS = re.compile(r"\b(?:have|having|hold|holding)\s+(\d+)\s+cards?\b", re.I)

TIER1 = {
    "delhi", "mumbai", "bengaluru", "bangalore", "hyderabad", "chennai",
    "kolkata", "pune", "gurgaon", "gurugram", "noida", "jaipur", "ahmedabad",
}
TIER2 = {
    "lucknow", "kanpur", "nagpur", "indore", "bhopal", "patna", "surat",
    "jaipur", "kochi", "coimbatore", "thiruvananthapuram", "guwahati",
    "chandigarh", "bhubaneswar", "mysuru", "mysore", "visakhapatnam",
}

EMPLOYMENT_PATTERNS = [
    ("business_owner", r"\bbusiness\s*owner\b|\bproprietor\b|\bpartner\b|\bdirector\b"),
    ("self_employed", r"\bself[\s-]?employed\b|\bfreelanc|\bconsultant\b|\bcontractor\b"),
    ("student", r"\bstudent\b|\buniversity\b|\bcollege\b|\bschool\b"),
    ("retired", r"\bretired\b|\bpension"),
    ("homemaker", r"\bhomemaker\b|\bhousewife\b|\bhousehusband\b"),
    ("salaried", r"\bsalaried\b|\bemployee\b|\bworking\s+as\b|\bjob\b|\bin\s+my\s+job\b"),
]


# ---------------------------------------------------------------- extractors
class RegexExtractor:
    """Offline fallback. Returns null rather than inventing a value."""

    name = "regex"

    def extract(self, message: str, prior: PartialProfile | None = None) -> PartialProfile:
        base = prior or PartialProfile()
        out = base.model_copy()
        text = message.lower()

        age = _AGE.search(message)
        if age:
            found = age.group(1) or age.group(2)
            if found:
                out.age = int(found)

        # Income: prefer an explicitly-labelled mention over a bare amount.
        income = self._income(message)
        if income is not None:
            out.monthly_income = income

        cibil = _CIBIL.search(message)
        if cibil:
            out.cibil_score = int(cibil.group(1))

        cards = _CARDS.search(text)
        if cards:
            out.existing_cards = int(cards.group(1))

        for value, pattern in EMPLOYMENT_PATTERNS:
            if re.search(pattern, text):
                out.employment = Employment(value)
                break

        for city in TIER1:
            if city in text:
                out.city_tier = 1
                break
        else:
            for city in TIER2:
                if city in text:
                    out.city_tier = 2
                    break

        fee = re.search(r"(?:no|without|zero|nil)\s*(?:annual\s*)?fee|lifetime[\s-]*free", text)
        if fee:
            out.max_annual_fee = 0
            if out.preferences is None:
                out.preferences = []
            if "lifetime_free" not in out.preferences:
                out.preferences.append("lifetime_free")

        prefs = out.preferences or []
        if re.search(r"\blounge\b|\bairport\b", text) and "lounge" not in prefs:
            prefs.append("lounge")
        if re.search(r"\btravel|\bflight|\bmiles\b|\bhotels?\b", text) and "travel" not in prefs:
            prefs.append("travel")
        if re.search(r"\bcashback\b", text) and "cashback" not in prefs:
            prefs.append("cashback")
        if re.search(r"\bfuel\b|\bpetrol\b|\bpump\b", text) and "fuel" not in prefs:
            prefs.append("fuel")
        if prefs:
            out.preferences = prefs

        spend = self._spend(message)
        if spend is not None:
            out.monthly_spend = spend
        return out

    @staticmethod
    def _income(message: str) -> int | None:
        # An income cue ("earn", "salary", "income", "CTC") makes a bare amount
        # trustworthy; without one, an amount is far more likely to be a spend.
        cues = (
            # The optional period prefix must sit *inside* the match. Otherwise
            # a generic cue starting at "income" wins the race, "monthly" never
            # reaches the context, and a monthly figure gets annualised by the
            # bare-number default.
            r"(?:(?:monthly|per\s+month|annual|per\s+annum|yearly|p\.?\s?a\.?|lpa)\s+)?"
            r"(?:earn|earning|salary|income|ctc|pay|paid|my\s+\w+\s+is|package)"
        )
        amount = r"((?:₹|rs\.?|inr)?\s*[\d,.]+\s*(?:l|lakh|lac|cr|crore|k\b|thousand)?)"
        patterns = [
            rf"(?:monthly\s+income|per\s+month\s+(?:income|salary)|monthly\s+salary)"
            rf"[^\d₹]{{0,15}}{amount}",
            rf"{cues}[^\d₹]{{0,25}}{amount}",
            rf"(?:i\s+make|i\s+get|i\s+earn)\s*{amount}",
        ]
        for pattern in patterns:
            for m in re.finditer(pattern, message, re.I):
                # The cue words inside the match are part of the context: they
                # can carry the period ("monthly income is 45000"). The forward
                # window stops at a clause boundary so that in "I earn 8.5 LPA
                # and spend 60k monthly" the "monthly" reads as the spend's
                # period, not the salary's.
                lead = m.group(0)[: m.group(0).rfind(m.group(1))]
                context = " ".join(
                    f"{lead} {_clause(message, m.end())}".split()
                )
                value, _ = parse_amount(m.group(1), context)
                if value is not None:
                    return value
        return None

    @staticmethod
    def _spend(message: str) -> SpendMix | None:
        """Category spends. Only returns a mix if something was explicit.

        Nothing here infers a total: splitting an unspecified budget across
        categories would be a guess wearing a number's clothes.
        """
        # Each category is a non-capturing group: without the wrapper the
        # alternation binds looser than the amount pattern and `group(1)`
        # returns the keyword instead of the amount.
        cats = {
            "fuel": r"(?:\bfuel\b|\bpetrol\b|\bpump\b|\bdiesel\b)",
            "dining": r"(?:\bdining\b|\beating out\b|\brestaurants?\b|\bcaf[eé]s?\b|\bcoffee\b)",
            "groceries": r"(?:\bgrocer|\bsupermarket\b|\bvegetables\b|\bgrocery\b)",
            "online_shopping": r"(?:\bonline\s*shop|\bamazon\b|\bflipkart\b|\bmyntra\b|\bshopping\b)",
            "travel": r"(?:\btravel\b|\bflights?\b|\bhotels?\b|\btaxis?\b|\buber\b|\bola\b)",
            "utilities": r"(?:\butilit|\belectric|\bbill\b|\bgas\b|\bbroadband\b|\bwifi\b)",
        }
        values: dict[str, int] = {}
        for cat, pattern in cats.items():
            m = re.search(
                rf"{pattern}[^\d₹]{{0,25}}((?:₹|rs\.?)?\s*[\d,.]+\s*(?:k\b|l|lakh|lac)?)",
                message,
                re.I,
            )
            if m:
                amount, period = parse_amount(m.group(1))
                if amount is not None:
                    ctx = message[max(0, m.start() - 40) : m.end() + 15]
                    if _PER_YEAR.search(ctx) and not _PER_MONTH.search(ctx):
                        amount = int(round(amount / 12))
                    values[cat] = amount
        total = re.search(
            r"(?:spend|spending)\D{0,20}((?:₹|rs\.?)?\s*[\d,.]+\s*(?:k\b|l|lakh|lac)?)",
            message,
            re.I,
        )
        if total:
            amount, _ = parse_amount(total.group(1))
            if amount is not None:
                claimed = sum(values.values())
                values["other"] = max(0, amount - claimed)
        if not values:
            return None
        return SpendMix(**values)


class LLMExtractor:
    name = "llm"

    def __init__(self, fallback: RegexExtractor | None = None) -> None:
        self.fallback = fallback or RegexExtractor()

    def extract(self, message: str, prior: PartialProfile | None = None) -> PartialProfile:
        s = get_settings()
        prior_hint = prior.model_dump(exclude_none=True) if prior else {}
        user = (
            f"Already known: {json_dumps(prior_hint)}\n\n"
            f"Customer says: {message}"
        )
        result = llm.chat(
            EXTRACT_SYSTEM, user, model=s.groq_model_extract, json_mode=True
        )
        if result.json_value is None:
            # A failed extraction must not lose what the regex path can read.
            log.info("LLM extraction failed (%s); using regex fallback", result.error)
            return self.fallback.extract(message, prior)
        try:
            parsed = PartialProfile.model_validate(_coerce(result.json_value))
        except Exception as exc:  # noqa: BLE001
            # Losing an otherwise good extraction because one preference was
            # spelled "online_shopping" instead of "cashback" is a bad trade:
            # age, income, score and spend are the fields that matter. Retry
            # once with the offending field dropped, then fall back.
            log.warning("LLM profile invalid (%s); retrying without extras", exc)
            relaxed = _drop_unknown(result.json_value)
            try:
                parsed = PartialProfile.model_validate(relaxed)
            except Exception:
                return self.fallback.extract(message, prior)
        if prior:
            parsed = prior.merged_with(parsed)
        return parsed


def json_dumps(value) -> str:
    import json

    return json.dumps(value, default=str)


VALID_PREFERENCES = {"cashback", "travel", "lounge", "fuel", "lifetime_free"}
VALID_EMPLOYMENT = {e.value for e in Employment}

# A model that answers "online_shopping" when asked for a preference is
# describing a spend category, not a preference. Mapping the closest valid
# value is better than discarding the whole extraction, and better than
# passing an unvalidated string into a Literal field.
_PREF_ALIASES = {
    "online_shopping": "cashback",
    "shopping": "cashback",
    "cash_back": "cashback",
    "miles": "travel",
    "flights": "travel",
    "hotels": "travel",
    "airport": "lounge",
    "no_annual_fee": "lifetime_free",
    "lifetime": "lifetime_free",
    "free_card": "lifetime_free",
}


def _coerce(data: dict) -> dict:
    """Nudge a plausible-but-off-schema model response into the schema.

    Only ever *narrows* what the model said. It never fills a blank and never
    invents a value, because guessing is the failure mode that matters.
    """
    out = dict(data)
    prefs = out.get("preferences")
    if isinstance(prefs, list):
        cleaned = []
        for p in prefs:
            key = str(p).strip().lower().replace(" ", "_").replace("-", "_")
            key = _PREF_ALIASES.get(key, key)
            if key in VALID_PREFERENCES and key not in cleaned:
                cleaned.append(key)
        out["preferences"] = cleaned or None
    emp = out.get("employment")
    if isinstance(emp, str):
        key = emp.strip().lower().replace(" ", "_").replace("-", "_")
        if key not in VALID_EMPLOYMENT:
            out["employment"] = None
        else:
            out["employment"] = key
    return out


def _drop_unknown(data: dict) -> dict:
    """Last resort: keep only fields whose schema accepts them."""
    allowed = set(PartialProfile.model_fields)
    return {k: v for k, v in data.items() if k in allowed}


def get_extractor() -> LLMExtractor | RegexExtractor:
    return LLMExtractor() if get_settings().has_llm else RegexExtractor()


# ---------------------------------------------------------------- follow-ups



def missing_fields(partial: PartialProfile) -> list[str]:
    """Required fields still unknown, in the order we ask for them.

    Order matters: income is the single most useful thing to ask for, because
    it changes both eligibility and the ranking, while preferences barely
    change the answer.
    """
    return [
        field
        for field in T.REQUIRED_PROFILE_FIELDS
        if getattr(partial, field, None) in (None, 0)
    ]


def next_question(partial: PartialProfile) -> str | None:
    missing = missing_fields(partial)
    if missing:
        return T.FOLLOWUP_QUESTIONS[missing[0]]
    if partial.preferences is None:
        return T.PREFERENCE_QUESTION
    return None


def ask_followup(partial: PartialProfile, user_message: str) -> str | None:
    """One question. LLM-written when available, fixed text otherwise.

    Falls back on a validation error as well as on no-API-key, because a
    follow-up question is never a good place to fail a request.
    """
    if not llm.available():
        return next_question(partial)
    missing = missing_fields(partial)
    ask_field = missing[0] if missing else "preferences"
    result = llm.chat(
        FOLLOWUP_SYSTEM,
        f"Missing field: {ask_field}\n"
        f"Known so far: {json_dumps(partial.model_dump(exclude_none=True))}\n"
        f"Latest message: {user_message}",
        model=get_settings().groq_model_extract,
    )
    if not result.used_llm or not result.text.strip():
        return next_question(partial)
    question = " ".join(result.text.strip().split())
    return question[:220]


def to_profile(partial: PartialProfile, profile_id: str = "guest") -> dict:
    """The only bridge from a partial intake to a full engine input.

    It fails loudly. Silently defaulting an unknown income to zero would send
    the profile down the rejection path and look like a real decision.
    """
    missing = missing_fields(partial)
    if missing:
        raise ValueError(f"cannot build a profile, missing {missing}")
    return {
        "profile_id": profile_id,
        "age": partial.age,
        "monthly_income": partial.monthly_income,
        "employment": partial.employment,
        "city_tier": partial.city_tier,
        "cibil_score": partial.cibil_score,
        "existing_cards": partial.existing_cards or 0,
        "missed_payments_12m": partial.missed_payments_12m or 0,
        "recent_inquiries_6m": partial.recent_inquiries_6m or 0,
        "utilization_pct": partial.utilization_pct,
        "monthly_spend": partial.monthly_spend,
        "preferences": partial.preferences or [],
    }
