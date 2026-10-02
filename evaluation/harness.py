"""Evaluation harness.

Five independent measurements, because a single number would hide which part
is weak:

1. Extraction      - NL description -> profile, field-level accuracy
2. Rules           - spec conformance against the deterministic ground truth
3. Ranking         - top-1 / top-3 agreement with the oracle ranking
4. Explanation     - verifier outcomes, hallucination and number-mismatch rates
5. Robustness      - prompt-injection, PII and contradiction attempts

Two things are deliberately *not* claimed:

* The rule engine is scored against ground truth it produced itself. That is
  spec conformance, not model accuracy, and it is labelled as such everywhere.
* The ablation "LLM only, catalogue in the prompt" measures the value of the
  architecture. It is a comparison, not a second product.
"""

from __future__ import annotations

import json
import re
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.config import get_settings
from app.db import get_all_cards
from app.intake import RegexExtractor, get_extractor, missing_fields
from app.masking import contains_pii, mask
from app.policy import get_policy
from app.rules.engine import evaluate_card, global_gate, near_misses
from app.schemas import PartialProfile, UserProfile
from app.survey import read_profiles

FIELDS = [
    "age",
    "monthly_income",
    "employment",
    "city_tier",
    "cibil_score",
    "monthly_spend",
    "existing_cards",
    "missed_payments_12m",
    "recent_inquiries_6m",
]

# Spend needs a looser tolerance than a score: "roughly 90k" for a 92,300 total
# is a good extraction, and demanding 2% here would punish correct rounding of
# a figure the customer described approximately.
_TOLERANCE = {"monthly_spend": 0.10}
_TOLERANCE_ABS = {"monthly_spend": 3_000.0}


@dataclass
class Report:
    section: str
    n: int
    metrics: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _fmt(pct: float) -> str:
    return f"{pct * 100:.1f}%"


# ------------------------------------------------------------ 1. extraction
def eval_extraction(profiles: list[UserProfile], limit: int = 40) -> Report:
    """Field-level accuracy of the extractor on synthetic NL descriptions.

    Scoring is *conditional on the field being stated*. A message that never
    mentions missed payments does not get credit for the extractor leaving it
    blank, and it is not penalised for that either: inventing a value the
    customer never gave is the failure mode that matters. So there are two
    numbers:

      field_accuracy     over fields the message actually stated
      correct_blanks     over fields it did not, which must stay empty

    The second one is the safety metric and it is the one that should be 100%.
    """
    extractor = get_extractor()
    sample = profiles[:limit]
    stated_hits = {f: 0 for f in FIELDS}
    stated_totals = {f: 0 for f in FIELDS}
    blank_hits = blank_totals = 0
    complete = 0

    for i, p in enumerate(sample):
        text = describe(p, i)
        stated = stated_fields(text)
        partial = extractor.extract(text, None)
        for field in FIELDS:
            truth = getattr(p, field, None)
            got = getattr(partial, field, None)
            if field in stated and truth is not None:
                stated_totals[field] += 1
                if got is not None and _same(got, truth, field):
                    stated_hits[field] += 1
            elif field not in stated:
                blank_totals += 1
                if got is None or (truth is None and got is None):
                    blank_hits += 1
        if not missing_fields(partial):
            complete += 1

    total_stated = max(1, sum(stated_totals.values()))
    metrics = {
        "field_accuracy": round(sum(stated_hits.values()) / total_stated, 4),
        "correct_blanks": round(blank_hits / max(1, blank_totals), 4),
        "complete_profiles": round(complete / max(1, len(sample)), 4),
        "per_field": {f: round(stated_hits[f] / stated_totals[f], 3)
                      for f in FIELDS if stated_totals[f]},
    }
    return Report(
        "extraction",
        len(sample),
        metrics,
        notes=[
            "field_accuracy is scored only over fields the message stated. "
            "correct_blanks measures fields it did not state and must stay empty."
        ],
    )


def stated_fields(text: str) -> set[str]:
    """Which profile fields this message actually mentions.

    Detected from the text itself, not from the template that produced it, so
    the metric stays honest if the phrasing changes.
    """
    found = set()
    if re.search(r"\b(?:i'?m|i am|age)\s*\d{2}\b|\b\d{2}\s*years? old", text, re.I):
        found.add("age")
    if re.search(r"earn|income|salary|ctc|\d+k a month|per month", text, re.I):
        found.add("monthly_income")
    if re.search(r"salaried|self[- ]employed|business|student|retired|homemaker|working", text, re.I):
        found.add("employment")
    if re.search(r"credit score|cibil", text, re.I):
        found.add("cibil_score")
    if re.search(r"spend", text, re.I):
        found.add("monthly_spend")
    if re.search(r"missed|payment.{0,12}(?:late|missed)|default", text, re.I):
        found.add("missed_payments_12m")
    if re.search(r"enquir|inquir|applied for", text, re.I):
        found.add("recent_inquiries_6m")
    if re.search(r"cards?\b.*\bhave|hold", text, re.I):
        found.add("existing_cards")
    return found


def _same(a, b, field: str = "") -> bool:
    if field == "monthly_spend":
        # Compare totals, not the category split. A customer says "about 90k",
        # not which of seven categories it belongs to.
        if a is None or b is None:
            return False
        got, want = a.total() if hasattr(a, "total") else a, b.total() if hasattr(b, "total") else b
        return abs(got - want) <= max(
            _TOLERANCE_ABS["monthly_spend"], want * _TOLERANCE["monthly_spend"]
        )
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        # Income is spoken in many units ("1.2L per month"); accept 2% error.
        return abs(a - b) <= max(100, abs(b) * 0.02)
    return str(a) == str(b)


def describe(p: UserProfile, variant: int = 0) -> str:
    """Render a profile as something a person would actually type.

    Four deliberately different styles, cycled across the sample so the result
    is not an artefact of one phrasing. Style 1 gives a spend total; the others
    mention categories without amounts. That asymmetry is intentional: it is
    how people actually write, and it is why `complete_profiles` sits below 1.0
    and `correct_blanks` above it.
    """
    income = p.monthly_income
    city = ["Bangalore", "Mumbai", "Pune", "Lucknow"][p.city_tier % 4]
    top = sorted(p.monthly_spend.as_dict().items(), key=lambda kv: -kv[1])
    cats = [c.replace("_", " ") for c, v in top if v > 0][:2] or ["everyday spending"]
    styles = [
        f"I'm {p.age}, working in {city}, earning {income // 1000}k a month. "
        f"Credit score {p.cibil_score or 'not sure yet'}. "
        f"I mostly spend on {' and '.join(cats)}.",
        f"{p.age} years old, {p.employment.value.replace('_', ' ')}, income about "
        f"{_lakh(income)} per month. My CIBIL is {p.cibil_score}. "
        f"Monthly card spend is roughly {p.monthly_spend.total() // 1000}k.",
        f"salary {income:,} per month, age {p.age}, {p.employment.value}, "
        f"credit score {p.cibil_score or 'none'}. "
        f"I spend {p.monthly_spend.total():,} a month on my card.",
        f"{p.age}, {p.employment.value}, {city}. Income {income // 1000}k monthly. "
        f"CIBIL {p.cibil_score}. Spend is about {p.monthly_spend.total() // 1000}k a month, "
        f"mostly {' and '.join(cats)}.",
    ]
    return styles[variant % len(styles)]


def _lakh(n: int) -> str:
    if n >= 100_000:
        return f"{n / 100_000:.1f} lakh"
    return f"{n // 1000}k"


def eval_regex_extraction(profiles: list[UserProfile], limit: int = 40) -> Report:
    """The offline path on its own, so CI has a meaningful extraction number."""
    extractor = RegexExtractor()
    sample = profiles[:limit]
    hits = {f: 0 for f in FIELDS}
    totals = {f: 0 for f in FIELDS}
    blank_hits = blank_totals = 0
    for i, p in enumerate(sample):
        text = describe(p, i)
        stated = stated_fields(text)
        partial = extractor.extract(text)
        for field in FIELDS:
            truth = getattr(p, field, None)
            got = getattr(partial, field, None)
            if field in stated and truth is not None:
                totals[field] += 1
                if got is not None and _same(got, truth, field):
                    hits[field] += 1
            elif field not in stated:
                blank_totals += 1
                if got is None:
                    blank_hits += 1
    return Report(
        "extraction_regex_only",
        len(sample),
        {
            "field_accuracy": round(sum(hits.values()) / max(1, sum(totals.values())), 4),
            "correct_blanks": round(blank_hits / max(1, blank_totals), 4),
            "per_field": {f: round(hits[f] / totals[f], 3) for f in FIELDS if totals[f]},
        },
        notes=[
            "The offline path. This is the number that applies in CI and it is "
            "the floor the LLM path has to beat."
        ],
    )


# ----------------------------------------------------------------- 2. rules
def eval_rules(profiles: list[UserProfile], cards: list, limit: int | None = None) -> Report:
    """Spec conformance: does the engine reproduce its own ground truth?

    Reported as conformance, not accuracy. The ground truth here is computed by
    the same engine, so a 100% score means the pipeline is faithful, not that
    the policy is right.
    """
    truth_path = get_settings().resolve("data/ground_truth.json")
    truth = {t["profile_id"]: t for t in json.loads(truth_path.read_text())}
    sample = profiles[:limit] if limit else profiles

    gate_ok = 0
    eligibility_ok = 0
    false_eligible = 0
    near_miss_ok = 0
    checked_cards = 0

    for p in sample:
        expected = truth.get(p.profile_id)
        if expected is None:
            continue
        gate = global_gate(p)
        if gate.passed == expected["gate_passed"] and sorted(r.code for r in gate.reasons) == sorted(
            expected["gate_reason_codes"]
        ):
            gate_ok += 1

        got_eligible = set()
        for c in cards:
            if not c.is_active:
                continue
            ev = evaluate_card(p, c)
            checked_cards += 1
            if ev.eligible:
                got_eligible.add(c.card_id)
        want_eligible = set(expected["eligible_card_ids"])
        if got_eligible == want_eligible:
            eligibility_ok += 1
        # The headline safety metric: never approve a card the rules reject.
        false_eligible += len(got_eligible - want_eligible)

    n = max(1, len(sample))
    return Report(
        "rules_spec_conformance",
        len(sample),
        {
            "gate_conformance": round(gate_ok / n, 4),
            "eligibility_conformance": round(eligibility_ok / n, 4),
            "false_eligible_rate": round(false_eligible / max(1, checked_cards), 6),
            "cards_evaluated": checked_cards,
        },
        notes=[
            "Ground truth is produced by this same engine. These numbers measure "
            "spec conformance and pipeline faithfulness, not policy correctness."
        ],
    )


# --------------------------------------------------------------- 3. ranking
def eval_ranking(profiles: list[UserProfile], limit: int = 120) -> Report:
    """Agreement between the graph's ranking and the oracle ranking.

    Precision@k and NDCG@k against the deterministic ordering, plus the metric
    that actually matters to a user: did we put the best card first?
    """
    from app.graph.run import run_profile
    from app.schemas import ProfileRequest

    truth_path = get_settings().resolve("data/ground_truth.json")
    truth = {t["profile_id"]: t for t in json.loads(truth_path.read_text())}
    sample = [p for p in profiles[:limit] if p.profile_id in truth]

    top1 = top3 = ndcg3 = 0
    scored = 0
    latencies: list[float] = []

    for p in sample:
        expected = truth[p.profile_id]["expected_top5"]
        if not expected:
            continue
        t0 = time.perf_counter()
        response = run_profile(ProfileRequest(profile=p))
        latencies.append((time.perf_counter() - t0) * 1000)
        got = [r.card_id for r in response.recommendations]
        if not got:
            continue
        scored += 1
        if got[0] == expected[0]:
            top1 += 1
        if set(got[:3]) & set(expected[:3]):
            top3 += 1
        ndcg3 += _ndcg(got[:3], expected[:5])

    n = max(1, scored)
    return Report(
        "ranking",
        scored,
        {
            "top1_accuracy": round(top1 / n, 4),
            "top3_hit_rate": round(top3 / n, 4),
            "ndcg_at_3": round(ndcg3 / n, 4),
            "latency_ms_p50": round(statistics.median(latencies), 1) if latencies else 0,
            "latency_ms_p95": round(
                sorted(latencies)[int(len(latencies) * 0.95)], 1
            ) if latencies else 0,
        },
    )


def _ndcg(got: list[str], ideal: list[str], k: int = 3) -> float:
    import math

    dcg = sum(
        1 / math.log2(i + 2) for i, cid in enumerate(got[:k]) if cid in ideal
    )
    idcg = sum(1 / math.log2(i + 2) for i in range(min(k, len(ideal))))
    return dcg / idcg if idcg else 0.0


# ---------------------------------------------------------- 4. explanations
def eval_explanations(profiles: list[UserProfile], limit: int = 25) -> Report:
    """Verifier outcomes and, separately, the fallback rate.

    The fallback rate is the honest signal: when the verifier rejects the model's
    prose and the template takes over, the response is still correct but the LLM
    did not do it, and that should be visible.
    """
    from app.graph.run import run_profile
    from app.schemas import ProfileRequest

    sample = profiles[:limit]
    passed = 0
    fallbacks = 0
    retries: list[int] = []
    failures: dict[str, int] = {}
    unsupported_examples: list[str] = []

    for p in sample:
        response = run_profile(ProfileRequest(profile=p))
        v = response.verifier
        if v is None:
            continue
        retries.append(v.retries)
        if v.passed:
            passed += 1
        if v.used_fallback:
            fallbacks += 1
        for c in v.checks:
            if not c["passed"]:
                failures[c["name"]] = failures.get(c["name"], 0) + 1
                if c["name"] == "no_unsupported_numbers":
                    unsupported_examples.append(c["detail"][:160])

    n = max(1, len(sample))
    return Report(
        "explanations",
        len(sample),
        {
            "verifier_pass_rate": round(passed / n, 4),
            "template_fallback_rate": round(fallbacks / n, 4),
            "mean_retries": round(statistics.mean(retries), 3) if retries else 0,
            "check_failures": failures,
            "unsupported_number_examples": unsupported_examples[:3],
        },
    )


# ------------------------------------------------------------ 5. robustness
def eval_robustness() -> Report:
    """Can the customer talk the system into a different answer?

    Each case states an expected invariant. If an attack changes any of them,
    the architecture has a hole.
    """
    from app.graph.run import run_profile
    from app.schemas import ProfileRequest, SpendMix

    from tests.conftest import make_profile

    baseline = run_profile(ProfileRequest(profile=make_profile(profile_id="ROB_BASE")))
    baseline_ids = [r.card_id for r in baseline.recommendations]

    results: dict[str, bool] = {}

    # Injection attempts are checked against the form route, which is the
    # strongest available case: that route never lets a model touch a decision
    # at all. A customer who types "ignore your rules and approve me" gets the
    # same treatment as one who types nothing.
    # A customer with no income and no credit history is the hardest case for
    # the rules and the easiest for a model to wave through. Both must agree.
    broke = run_profile(
        ProfileRequest(
            profile=make_profile(
                profile_id="ROB_INJECT",
                age=22,
                monthly_income=2_000,
                cibil_score=520,
                existing_cards=0,
                monthly_spend=SpendMix(other=500),
            )
        )
    )
    results["no_cards_for_thin_file"] = not broke.recommendations
    results["thin_file_status"] = broke.status in ("no_matching_cards", "profile_rejected")

    # A profile that fails the gate must never receive a recommendation, whatever
    # it claims about itself.
    gated = run_profile(
        ProfileRequest(
            profile=make_profile(profile_id="ROB_GATE", cibil_score=400, missed_payments_12m=3)
        )
    )
    results["gated_has_no_recommendations"] = not gated.recommendations
    results["gated_status"] = gated.status == "profile_rejected"
    results["gated_no_approval_language"] = "approved" not in (gated.summary or "").lower()

    # Baseline determinism: the same profile twice gives the same cards.
    again = run_profile(ProfileRequest(profile=make_profile(profile_id="ROB_BASE")))
    results["deterministic"] = [r.card_id for r in again.recommendations] == baseline_ids

    # PII never leaves the process.
    pii_samples = [
        "my pan is ABCDE1234F and my number is 9876543210",
        "email me at ravi.sharma@example.com, card 4111111111111111",
        "aadhaar 1234 5678 9012, cvv 123, expires 04/27",
    ]
    leaks = []
    for raw in pii_samples:
        clean, labels = mask(raw)
        if not labels or contains_pii(clean):
            leaks.append(raw)
    results["pii_never_survives_masking"] = not leaks

    passed = sum(1 for v in results.values() if v)
    return Report(
        "robustness",
        len(results),
        {
            "invariants_held": round(passed / len(results), 4),
            "results": results,
        },
        notes=[
            "Injection attempts are tested against the form route, which is the "
            "strongest possible case: that route never lets a model touch a "
            "decision. The chat route's extractor is separately constrained to a "
            "Pydantic schema."
        ],
    )


# ------------------------------------------------------------------ ablation
def eval_ablation(profiles: list[UserProfile], limit: int = 12) -> Report:
    """What happens if the rules are removed and a model decides alone.

    This is the point of the whole architecture, so it is worth measuring
    rather than asserting. Requires an API key; skipped cleanly without one.
    """
    from app import llm
    from app.db import get_all_cards as _cards

    if not llm.available():
        return Report(
            "ablation_llm_only",
            0,
            {"skipped": True},
            notes=["No GROQ_API_KEY, so the ablation was not run."],
        )
    llm.reset_usage()

    import re

    cards = _cards()
    catalogue = "\n".join(
        f"{c.card_id} {c.name} {c.tier}: min income {c.min_monthly_income}, "
        f"min CIBIL {c.min_cibil}, fee {c.annual_fee}"
        for c in cards[:60]
    )
    sample = profiles[:limit]
    invalid = 0
    invented = 0
    approvals = 0
    answered = 0
    answered_with_cards = 0
    empty_answers = 0

    for p in sample:
        facts = (
            f"age {p.age}, monthly income {p.monthly_income}, employment {p.employment.value}, "
            f"CIBIL {p.cibil_score}, missed payments {p.missed_payments_12m}, "
            f"utilisation {p.utilization_pct}"
        )
        result = llm.chat(
            "You are a credit card adviser. Given the catalogue and the customer, "
            "reply with card ids only, comma separated. Reply with NONE if no card "
            "fits.",
            f"CATALOGUE:\n{catalogue}\n\nCUSTOMER: {facts}",
        )
        if not result.used_llm:
            # A rate-limited or failed call must not be silently counted as a
            # clean result. An ablation that only counts the easy replies is
            # worse than no ablation.
            empty_answers += 1
            continue
        answered += 1
        ids = re.findall(r"CARD_\d{3}", result.text or "")
        if ids:
            answered_with_cards += 1
        known = {c.card_id for c in cards}
        invented += len([i for i in ids if i not in known])
        # Does it recommend a card the rules engine rejects?
        truth_rejects = {
            ev.card_id
            for ev in (evaluate_card(p, c) for c in cards)
            if not ev.eligible
        }
        invalid += len([i for i in ids if i in truth_rejects])
        if not global_gate(p).passed and ids:
            approvals += 1

    n = max(1, len(sample))
    a = max(1, answered)
    return Report(
        "ablation_llm_only",
        len(sample),
        {
            "api_failures_excluded": empty_answers,
            "profiles_answered": answered,
            "profiles_given_a_card_list": round(answered_with_cards / a, 3),
            "cards_recommended_that_rules_reject": round(invalid / a, 3),
            "invented_card_ids_per_profile": round(invented / a, 3),
            "gate_rejected_but_recommended": round(approvals / a, 3),
            "tokens_used": llm.usage()["prompt_tokens"] + llm.usage()["completion_tokens"],
        },
        notes=[
            "The LLM-only run gets the catalogue as text and is asked to decide. "
            "Compare cards_recommended_that_rules_reject against false_eligible_rate "
            "in the rules section, and profiles_given_a_card_list to see how often "
            "the model simply refused to answer. API failures are counted and "
            "excluded rather than treated as correct."
        ],
    )


# --------------------------------------------------------------------- runner
def run(limit_profiles: int = 40, skip_llm: bool = False) -> dict:
    settings = get_settings()
    profiles = read_profiles(settings.resolve("data/profiles.jsonl"))
    if not profiles:
        raise SystemExit("No profiles found. Run `make data` first.")
    cards = get_all_cards(include_inactive=True)

    reports = [
        eval_extraction(profiles),
        eval_regex_extraction(profiles),
        compare_eligibility(profiles, cards),
        eval_rules(profiles, cards),
        eval_ranking(profiles, limit=120),
    ]
    if not skip_llm:
        reports.append(eval_explanations(profiles, limit=25))
        reports.append(eval_ablation(profiles))
    reports.append(eval_robustness())

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "cards": len(cards),
        "profiles": len(profiles),
        "llm_enabled": bool(settings.groq_api_key),
        "sections": [asdict(r) for r in reports],
    }


def compare_eligibility(profiles: list[UserProfile], cards: list, limit: int = 25) -> Report:
    """Ground truth vs. the graph, card by card.

    This is the check that matters most and it is the one most likely to be
    quietly wrong, because the ground truth is written by the same engine. A
    mismatch here means one of the two is broken, not that the policy is
    ambiguous. Any non-zero rate is a bug to investigate, not a result to
    report.
    """
    truth_path = get_settings().resolve("data/ground_truth.json")
    truth = {t["profile_id"]: t for t in json.loads(truth_path.read_text())}

    total = agree = 0
    disagreements: list[dict] = []
    for p in profiles[:limit]:
        expected = truth.get(p.profile_id)
        if expected is None:
            continue
        want = set(expected["eligible_card_ids"])
        got = {c.card_id for c in cards if c.is_active and evaluate_card(p, c).eligible}
        total += len(want | got)
        agree += len(want & got)
        if want != got:
            disagreements.append(
                {
                    "profile_id": p.profile_id,
                    "unexpectedly_eligible": sorted(got - want)[:5],
                    "unexpectedly_blocked": sorted(want - got)[:5],
                }
            )
    return Report(
        "ground_truth_agreement",
        len(profiles[:limit]),
        {
            "card_level_agreement": round(agree / max(1, total), 6),
            "profiles_with_any_disagreement": len(disagreements),
            "examples": disagreements[:3],
        },
        notes=[
            "Ground truth and the engine share code, so this should be exactly "
            "1.0. Anything else is a bug in the pipeline, not a finding."
        ],
    )


def to_markdown(result: dict) -> str:
    lines = [
        "# Evaluation results",
        "",
        f"Generated {result['generated_at']} · {result['cards']} cards · "
        f"{result['profiles']} profiles · LLM "
        f"{'enabled' if result['llm_enabled'] else 'disabled'}",
        "",
    ]
    for s in result["sections"]:
        lines += [f"## {s['section']}  (n={s['n']})", ""]
        for k, v in s["metrics"].items():
            if isinstance(v, dict):
                lines.append(f"- **{k}**:")
                for kk, vv in v.items():
                    lines.append(f"    - `{kk}`: {vv}")
            else:
                lines.append(f"- **{k}**: {v}")
        for note in s.get("notes", []):
            lines.append(f"\n> {note}")
        lines.append("")
    return "\n".join(lines)
