"""The verifier.

It answers one question: does this text contradict the engine? It cannot change
a decision, only block or repair the prose. That constraint is what stops the
verifier from becoming a second, less-tested source of truth.

Checks are deterministic string and number comparisons. The optional LLM check
(`ENABLE_VERIFIER_LLM_CHECK`) is additive and can only ever *add* a failure, so
turning it on cannot make a bad answer pass.
"""

from __future__ import annotations

import re

from app import thresholds as T
from app.config import get_settings
from app.masking import mask
from app.schemas import RecommendationResponse, VerifierReport

_NUM = re.compile(r"(?:rs\.?|₹|inr)?\s*([\d,]+(?:\.\d+)?)\s*(lakh|lac|l\b|crore|cr)?", re.I)


def _numbers_in(text: str) -> set[float]:
    """Every rupee-ish figure in the text, normalised to a plain number."""
    out: set[float] = set()
    for raw, unit in _NUM.findall(text or ""):
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if unit:
            # Indian units appear in prose as well as in numbers, so a figure
            # written "Rs 1.5 lakh" is checked against 150000, not 1.5.
            scale = {
                "lakh": 100_000, "lac": 100_000, "l": 100_000,
                "crore": 10_000_000, "cr": 10_000_000,
            }[unit.lower()]
            value *= scale
        out.add(value)
    return out


def _check(name: str, passed: bool, detail: str) -> dict:
    return {"name": name, "passed": passed, "detail": "" if passed else detail}


def verify_response(response: RecommendationResponse, state: dict) -> VerifierReport:
    """Check that the prose does not contradict the engine.

    The verifier can block or repair text. It cannot change a decision, and no
    check here re-derives eligibility: it only compares what was written
    against what the engine already computed.
    """
    checks: list[dict] = []

    ranked_ids = [r.card_id for r in response.recommendations]
    eligible_ids = {c.card_id for c in state.get("eligible_cards", [])}
    gate_passed = state.get("gate_passed", True)

    # --- 1. every recommended card really passed eligibility ---------------
    # The single most important check: recommending an ineligible card is the
    # failure mode that makes an LLM recommender untrustworthy.
    leaked = [cid for cid in ranked_ids if cid not in eligible_ids]
    checks.append(
        _check(
            "eligibility_consistency",
            not leaked,
            f"recommended cards that failed eligibility: {leaked}",
        )
    )

    # --- 2. rejected customer is not told they were approved ----------------
    if not gate_passed:
        text = (response.summary or "").lower()
        approved_words = ("approved", "you are eligible", "you qualify", "you're eligible")
        bad = [w for w in approved_words if w in text]
        checks.append(
            _check(
                "no_approval_language_when_rejected",
                not bad,
                f"told a rejected customer they are approved: {bad}",
            )
        )
        checks.append(
            _check(
                "rejection_status_consistent",
                response.status == "profile_rejected",
                f"status is {response.status} but the gate rejected the profile",
            )
        )

    # --- 3. ranking order matches the computed order -----------------------
    scores = [r.score for r in response.recommendations]
    checks.append(
        _check(
            "ranking_order",
            scores == sorted(scores, reverse=True),
            "recommendations are not in descending score order",
        )
    )

    # --- 4. no invented card names or ids ---------------------------------
    # The model may only name cards that exist in the catalogue. Cheaper and
    # stricter than checking text similarity: an invented name is a hallucination.
    from app.db import get_all_cards

    known = {c.card_id for c in get_all_cards(include_inactive=True)}
    invented = [cid for cid in ranked_ids if cid not in known]
    checks.append(_check("known_card_ids", not invented, f"unknown card ids: {invented}"))

    known_names = {c.name for c in get_all_cards(include_inactive=True)}
    text = response.summary or ""
    capitalised = set(re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b", text))
    # Only flag name-shaped tokens that are absent from the whole catalogue and
    # from the policy product name. Prose nouns like "Monthly" are filtered by
    # requiring at least two capitalised words or a Card keyword.
    #
    # The product name itself is explicitly excluded: the model is required to
    # include the disclaimer ("Card Sathi is a demonstration system"), so
    # without this the verifier fails every explanation that complies with the
    # prompt. Found in production, not in a test.
    product_name = T.PRODUCT_NAME.lower()
    suspicious = [
        phrase
        for phrase in capitalised
        if phrase.lower() != product_name
        and (
            ("card" in phrase.lower() and phrase not in known_names)
            or (phrase.endswith("Card") and phrase not in known_names)
        )
    ]
    checks.append(
        _check(
            "no_invented_card_names",
            not suspicious,
            f"mentions cards that are not in the catalogue: {suspicious}",
        )
    )

    # --- 5. numbers must come from the engine ------------------------------
    # Whitelist = every number the engine computed plus the numbers appearing in
    # retrieved policy text. Anything else in the prose is unsupported.
    allowed = set()
    # The profile's own numbers are in the prompt, so quoting them back is
    # expected. Excluding them would make the check fire on any sentence that
    # mentions the customer's income, which is most of them.
    if response.profile:
        p = response.profile
        allowed |= {float(p.age), float(p.monthly_income)}
        if p.cibil_score is not None:
            allowed.add(float(p.cibil_score))
        if p.utilization_pct:
            allowed.add(float(p.utilization_pct))
        allowed |= {float(v) for v in p.monthly_spend.as_dict().values()}
        allowed.add(float(p.existing_cards))
        allowed.add(float(p.missed_payments_12m))
        allowed.add(float(p.recent_inquiries_6m))
    for rec in response.recommendations:
        allowed |= {
            float(rec.net_annual_value_rs),
            float(rec.fee_payable_rs),
            float(rec.est_credit_limit_rs),
            float(rec.apr_pct),
            float(rec.score),
        }
        allowed.add(float(rec.rank))
    for r in response.rejected_cards + [x for ev in response.rejections for x in ev.reasons]:
        for v in (r.actual, r.required, r.gap):
            if isinstance(v, (int, float)):
                allowed.add(float(v))
        for n in _numbers_in(r.message):
            allowed.add(n)
    for ev in response.evidence:
        allowed |= _numbers_in(ev.text)
    for step in response.improvement_steps:
        allowed |= _numbers_in(step)

    quoted = _numbers_in(text)
    # Tolerance: a model that rounds Rs 37,166 to Rs 37,000 is imprecise, not
    # hallucinating. A model that cites Rs 41,000 is inventing. 2% absolute,
    # or Rs 100 on small figures, whichever is looser, separates the two.
    def _supported(value: float) -> bool:
        return any(
            abs(value - a) <= max(100.0, abs(a) * 0.02) for a in allowed
        )

    unsupported = sorted(n for n in quoted if n > 3 and not _supported(n))
    checks.append(
        _check(
            "no_unsupported_numbers",
            not unsupported,
            f"cites figures that are not in the computed facts or the policy: {unsupported}",
        )
    )

    # --- 6. no PII in the output -------------------------------------------
    _, pii = mask(text)
    checks.append(_check("no_pii_in_output", not pii, f"output contains {pii}"))

    # --- 6b. no internal reason codes in customer-facing prose -------------
    # A leaked code is a support ticket. Cheap to catch here and impossible to
    # catch downstream, so it is checked at the boundary.
    leaked_codes = [c for c in T.REASON_LABELS if c in text]
    checks.append(
        _check(
            "no_internal_reason_codes",
            not leaked_codes,
            f"customer-facing text exposes internal codes: {leaked_codes}",
        )
    )

    # --- 7. there is actually something to read ---------------------------
    # Without this, a failed LLM call produces an empty summary that sails
    # through every other check and is reported as a clean run.
    if response.status == "need_more_information":
        checks.append(
            _check(
                "has_a_question",
                bool(response.question),
                "asked for more information but posed no question",
            )
        )
    else:
        checks.append(
            _check(
                "has_a_summary",
                len((response.summary or "").strip()) > 40,
                "produced no usable explanation",
            )
        )

    # --- 8. every claim has support when it cites policy -------------------
    if response.recommendations and not response.evidence:
        checks.append(
            _check(
                "has_evidence",
                False,
                "made a recommendation with no retrieved policy evidence",
            )
        )
    else:
        checks.append(_check("has_evidence", True, ""))

    # --- 9. status matches the outcome -------------------------------------
    expected = (
        "recommendations_available"
        if response.recommendations
        else ("no_matching_cards" if response.profile else "need_more_information")
    )
    if gate_passed:
        checks.append(
            _check("status_consistent", response.status == expected,
                   f"status is {response.status}, outcome implies {expected}")
        )

    notes: list[str] = []
    if get_settings().enable_verifier_llm_check and response.summary:
        llm_ok, note = _llm_grounding_check(response)
        checks.append(_check("llm_grounding", llm_ok, note))
        if note:
            notes.append(note)

    passed = all(c["passed"] for c in checks)
    return VerifierReport(passed=passed, checks=checks, notes=notes)


def _facts_digest(response: RecommendationResponse) -> str:
    """A compact statement of what the engine decided.

    Only used by the optional LLM grounding check, so it is deliberately short:
    the deterministic checks above already cover the numbers, and this judge is
    there to catch a claim that is true-sounding but not supported.
    """
    lines = []
    if response.profile:
        p = response.profile
        lines.append(
            f"customer: age {p.age}, income {p.monthly_income}, cibil {p.cibil_score}, "
            f"employment {p.employment.value}"
        )
    for r in response.recommendations:
        lines.append(
            f"{r.card_id} {r.name}: net {r.net_annual_value_rs}, fee paid "
            f"{r.fee_payable_rs}, limit {r.est_credit_limit_rs}"
        )
    for r in response.rejected_cards:
        lines.append(f"blocked by {r.code}: {r.message}")
    for ev in response.rejections:
        for r in ev.reasons:
            lines.append(f"{ev.card_id} not eligible: {r.message}")
    for e in response.evidence:
        lines.append(f"policy [{e.source}] {e.text[:200]}")
    return "\n".join(lines) or "(nothing)"


def _llm_grounding_check(response: RecommendationResponse) -> tuple[bool, str]:
    """Optional semantic check. Additive only: it can only fail, never pass.

    Off by default (`ENABLE_VERIFIER_LLM_CHECK=false`). Turning it on cannot make
    a bad answer pass, because a failure here only ever adds a failed check.
    """
    from app import llm

    result = llm.chat(
        "You check whether a summary is supported by the facts it cites. "
        "Reply with the single word GROUNDED or UNGROUNDED and nothing else.",
        f"FACTS:\n{_facts_digest(response)}\n\nSUMMARY:\n{response.summary}",
        json_mode=False,
    )
    if not result.used_llm:
        return True, ""
    grounded = "GROUNDED" in (result.text or "").upper()
    return grounded, "" if grounded else "LLM judge could not ground the summary"
