"""Explanation generation.

The prompt is assembled from computed facts only. There is no path by which
the model can see a number the engine did not produce, which is what makes the
verifier's numeric checks meaningful rather than decorative.

The template fallback is not a stub. It is the guaranteed-good output when the
LLM is unavailable or fails verification, and it is written to read like prose
rather than like a debug dump.
"""

from __future__ import annotations

import logging
from typing import Any

from app import llm, thresholds as T
from app.config import get_settings
from app.schemas import RecommendationResponse

log = logging.getLogger(__name__)

SYSTEM = """You explain a credit-card recommendation to the customer.

You will be given FACTS, computed by our rules engine. They are correct. Do not
recalculate, round differently, or adjust them.

Rules you must follow:
- Use only numbers that appear in FACTS. If a number is not in FACTS, do not
  mention it. Never invent a fee, a reward rate, a limit, or a benefit.
- Never say a customer is approved. Say a card is a fit, or that a requirement
  is not met.
- Never invent card names, bank names or card ids.
- Quote the reasons exactly as given, including the "Rs N" gaps.
- Never print a bracketed code such as [CIBIL_BELOW_MIN]. Those are internal
  identifiers; the customer reads the sentence after them.
- Ground every policy claim in the POLICY EXCERNTS provided.
- Plain English, second person, short paragraphs. No bullet lists longer than
  four items. No marketing language: no "unbeatable", "best in India", "huge
  savings".
- Currency is Indian rupees, written as "Rs 40,000" with a comma.
- Do not give financial advice beyond what the FACTS state. Do not suggest
  borrowing, taking a loan, or applying for more cards.
- Write 3 to 5 sentences for a recommendation, 2 to 4 for a rejection.
- Reply with the explanation text only. No preamble, no headings, no JSON."""


def build_prompt(response: RecommendationResponse, evidence, state) -> str:
    """The FACTS block. Everything the model is allowed to use."""
    profile = response.profile
    lines: list[str] = ["FACTS (from our rules engine, all values are exact):"]

    if profile:
        lines.append(f"- Customer age {profile.age}, monthly income Rs {profile.monthly_income:,}.")
        if profile.cibil_score is not None:
            lines.append(f"- Credit score {profile.cibil_score}.")
        else:
            lines.append("- No credit score on file (new to credit).")
        if profile.preferences:
            lines.append(f"- Stated preferences: {', '.join(profile.preferences)}.")
        top = sorted(profile.monthly_spend.as_dict().items(), key=lambda kv: -kv[1])[:3]
        if top:
            lines.append(
                "- Monthly spend: "
                + ", ".join(f"{k} Rs {v:,}" for k, v in top if v > 0)
                + "."
            )

    if response.rejected_cards:
        lines.append("")
        lines.append("REASONS WE COULD NOT RECOMMEND ANY CARD (use these verbatim):")
        for r in response.rejected_cards:
            lines.append(f"- [{r.code}] {r.message}")

    if response.recommendations:
        lines.append("")
        lines.append("RECOMMENDED CARDS, in this exact order:")
        for rec in response.recommendations:
            lines.append(
                f"- Rank {rec.rank}. {rec.name} ({rec.bank}, {rec.card_id}), "
                f"{rec.tier} tier. Net value to them over a year: Rs {rec.net_annual_value_rs:,}. "
                f"Annual fee they would pay: Rs {rec.fee_payable_rs:,}. "
                f"Indicative credit limit: about Rs {rec.est_credit_limit_rs:,}. "
                f"APR {rec.apr_pct}%. Score {rec.score:.2f}."
            )
            for b in rec.key_benefits:
                lines.append(f"    * benefit: {b}")

    if response.rejections:
        lines.append("")
        lines.append("CARDS THEY DID NOT QUALIFY FOR (top few):")
        for ev in response.rejections[:3]:
            for r in ev.reasons:
                lines.append(f"- {ev.card_id}: {r.message}")

    if response.near_miss:
        lines.append("")
        lines.append("NEAR MISSES (they were close on these):")
        for ev in response.near_miss:
            for r in ev.reasons:
                lines.append(f"- {ev.card_id}: {r.message}")

    if response.improvement_steps:
        lines.append("")
        lines.append("WHAT WOULD HELP (already checked wording, reuse or paraphrase):")
        for step in response.improvement_steps:
            lines.append(f"- {step}")

    lines.append("")
    lines.append("POLICY EXCERPTS:")
    if evidence:
        for e in evidence[:6]:
            lines.append(f"- [{e.source} / {e.section}] {e.text[:400]}")
    else:
        lines.append("- (none retrieved; rely only on FACTS)")

    lines.append("")
    lines.append(f"Required disclaimer to include: {T.DISCLAIMER.strip()}")
    lines.append("Do not repeat the disclaimer word for word if it clutters the reply; keep the substance.")
    return "\n".join(lines)


def explain_response(
    response: RecommendationResponse, evidence, state: dict[str, Any], attempt: int = 0
) -> RecommendationResponse:
    """Generate prose. Returns the response with `summary` filled in."""
    if response.status == "need_more_information":
        response.summary = response.question or ""
        return response

    prompt = build_prompt(response, evidence, state)

    if attempt > 0 and response.verifier is not None:
        problems = [
            c.get("detail", "") for c in response.verifier.checks if not c.get("passed")
        ]
        if problems:
            prompt += (
                "\n\nA checker rejected your last attempt for these reasons. "
                "Rewrite fixing exactly these:\n- "
                + "\n- ".join(problems)
            )

    result = llm.chat(SYSTEM, prompt, model=get_settings().groq_model_explain)
    if not result.used_llm or not result.text.strip():
        return response  # summary stays empty; caller takes the template path

    text = " ".join(result.text.strip().split())
    response.summary = text
    return response


# ------------------------------------------------------------------ template
def _money(n: int | float) -> str:
    return f"Rs {int(round(n)):,}"


def template_response(response: RecommendationResponse, state) -> RecommendationResponse:
    """Deterministic prose built from the engine's own numbers.

    Kept genuinely readable: this is what a user sees when the LLM is down, and
    it is what a reviewer sees when checking that the facts are right.
    """
    profile = response.profile

    if response.status == "need_more_information":
        response.summary = response.question or "Tell me a little about yourself first."
        return response

    if response.status == "profile_rejected":
        # Every blocking reason, not just the first: fixing one blocker usually
        # reveals the next, and a customer who hears only the first will come
        # back after fixing it.
        reasons = " ".join(r.message for r in response.rejected_cards[:3])
        steps = response.improvement_steps[:2]
        response.summary = (
            f"I can't recommend a card right now. {reasons} " + " ".join(steps)
        ).strip()
        return response

    if not response.recommendations:
        if response.near_miss:
            gaps = response.near_miss[0].reasons[0].message
            response.summary = (
                "Nothing in our current catalogue lines up with your profile right now. "
                f"The closest was {response.near_miss[0].card_id}: {gaps} "
                + " ".join(response.improvement_steps[:2])
            ).strip()
        else:
            reasons = " ".join(
                r.message for ev in response.rejections[:2] for r in ev.reasons
            )
            response.summary = (
                "I couldn't find a card that fits your profile at the moment. "
                f"{reasons}".strip()
            )
        return response

    top = response.recommendations[0]
    second = response.recommendations[1] if len(response.recommendations) > 1 else None
    spend_line = ""
    if profile:
        top_cat = sorted(profile.monthly_spend.as_dict().items(), key=lambda kv: -kv[1])[0]
        if top_cat[1] > 0:
            spend_line = f" That lines up with where you actually spend, {_money(top_cat[1])} a month on {top_cat[0].replace('_', ' ')}."

    sentence = (
        f"Based on what you've told me, {top.name} from {top.bank} is the strongest fit. "
        f"On your spending it works out to about {_money(top.net_annual_value_rs)} of value over a year, "
        f"after the {_money(top.fee_payable_rs)} annual fee you'd actually pay."
    )
    if second:
        sentence += (
            f" {second.name} is the next best option at about "
            f"{_money(second.net_annual_value_rs)} a year."
        )
    response.summary = sentence + spend_line
    return response
