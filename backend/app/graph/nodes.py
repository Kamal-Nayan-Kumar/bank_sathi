"""Graph nodes.

Each node is small, pure-ish and independently callable, so a failure can be
attributed to one step and the trace can say which one it was.

The decision path (gate → prefilter → evaluate → rank) contains no LLM call at
all. That is the property the whole project rests on.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app import intake
from app.config import get_settings
from app.db import get_all_cards, get_cards_by_ids, session_scope
from app.explain import explain_response, template_response
from app.rag.retriever import retrieve_for_cards, retrieve_for_reason_codes
from app.rules.engine import (
    evaluate_card,
    fetch_candidates,
    global_gate,
    improvement_steps,
    near_misses,
)
from app.rules.scoring import (
    est_credit_limit,
    fee_payable,
    net_annual_value,
    rank,
)
from app.schemas import (
    Card,
    CardEvaluation,
    PolicyEvidence,
    Recommendation,
    RecommendationResponse,
    UserProfile,
    VerifierReport,
)
from app.verify import verify_response

log = logging.getLogger(__name__)


class Timer:
    """Records per-node latency into the graph's trace dict."""

    def __init__(self, name: str, trace: dict[str, float]) -> None:
        self.name = name
        self.trace = trace

    def __enter__(self) -> Timer:
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self.trace[self.name] = round((time.perf_counter() - self._t0) * 1000, 1)


def _now_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


# ------------------------------------------------------------------ intake
def node_intake(state: dict[str, Any]) -> dict[str, Any]:
    """Message + memory -> partial profile.

    PII is masked *before* extraction, not after: the masked text is what the
    model sees, so a PAN typed into chat never leaves the process.
    """
    trace = state.setdefault("trace", {})
    message = state.get("user_message", "")
    prior = state.get("prior_profile")

    with Timer("intake", trace):
        clean, pii_labels = intake_mask(message)
        extractor = intake.get_extractor()
        partial = extractor.extract(clean, prior)

    missing = intake.missing_fields(partial)
    complete = not missing
    update: dict[str, Any] = {
        "partial": partial,
        "missing_fields": missing,
        "profile_complete": complete,
        "trace": trace,
    }
    if pii_labels:
        trace["pii_masked"] = float(len(pii_labels))
    if complete:
        update["followup_question"] = None
    return update


def intake_mask(message: str) -> tuple[str, list[str]]:
    from app.masking import mask

    return mask(message)


def node_followup(state: dict[str, Any]) -> dict[str, Any]:
    """One question, then end the turn.

    Not a loop back into the graph: the customer has to speak again, and
    re-entering would re-run extraction for no new information.
    """
    partial = state.get("partial")
    if partial is None:
        return {"followup_question": "Tell me a little about yourself first."}
    question = intake.ask_followup(partial, state.get("user_message", ""))
    return {"followup_question": question}


def node_build_profile(state: dict[str, Any]) -> dict[str, Any]:
    """Partial -> validated UserProfile. Fails loudly rather than defaulting."""
    explicit: UserProfile | None = state.get("explicit_profile")
    if explicit is not None:
        # The form route arrives here already validated by Pydantic. Re-running
        # it is harmless and cheap, and it means both routes share one exit.
        try:
            return {
                "profile": UserProfile.model_validate(explicit.model_dump()),
                "profile_complete": True,
                "missing_fields": [],
            }
        except Exception as exc:  # noqa: BLE001
            log.info("explicit profile failed revalidation: %s", exc)
            return {
                "profile_complete": False,
                "missing_fields": ["monthly_spend"],
                "followup_question": (
                    "Some of those numbers don't fit together. "
                    "Roughly how much do you spend on your card each month?"
                ),
            }

    partial = state.get("partial")
    if partial is None:
        return {
            "profile_complete": False,
            "missing_fields": ["monthly_income"],
            "followup_question": "What is your approximate monthly income?",
        }
    try:
        data = intake.to_profile(partial, state.get("session_id", "guest"))
        profile = UserProfile.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        # A validation failure here means the extracted numbers are internally
        # inconsistent (e.g. spend 10x income). Surface it as a question rather
        # than quietly clamping the profile.
        log.info("profile validation failed: %s", exc)
        return {
            "profile_complete": False,
            "missing_fields": ["monthly_spend"],
            "followup_question": (
                "That spend figure looks higher than your income. Roughly how much "
                "do you spend on your card each month?"
            ),
        }
    return {"profile": profile, "profile_complete": True, "missing_fields": []}


# ------------------------------------------------------------------- gate
def node_gate(state: dict[str, Any]) -> dict[str, Any]:
    """Company-wide screen. No LLM, no catalogue scan."""
    profile: UserProfile = state["profile"]
    trace = state.setdefault("trace", {})
    with Timer("gate", trace):
        result = global_gate(profile)
    return {
        "gate_passed": result.passed,
        "gate_reasons": result.reasons,
        "trace": trace,
    }


# ---------------------------------------------------------------- prefilter
def node_prefilter(state: dict[str, Any]) -> dict[str, Any]:
    """SQL narrows the catalogue to the cards worth checking. No LLM involved."""
    profile: UserProfile = state["profile"]
    trace = state.setdefault("trace", {})
    with Timer("prefilter", trace):
        # One session for both queries: opening one costs ~750ms against a remote
        # database, and this step is the first thing on the request path.
        with session_scope() as db:
            ids = fetch_candidates(db, profile)
            # Only the survivors are loaded. Re-reading the entire catalogue
            # here cost ~3s per request, for rows nobody looked at.
            candidates = get_cards_by_ids(ids, db)
    trace["candidates_returned"] = float(len(candidates))
    return {"candidate_ids": ids, "candidates": candidates, "trace": trace}


# ---------------------------------------------------------------- evaluate
def node_evaluate(state: dict[str, Any]) -> dict[str, Any]:
    """Every candidate, every rule, in Python.

    Re-checks what SQL already filtered. The duplication is deliberate: it is
    cheap, and it means a bug in the SQL cannot by itself approve a customer.
    """
    profile: UserProfile = state["profile"]
    candidates: list[Card] = state.get("candidates", [])
    trace = state.setdefault("trace", {})

    with Timer("eligibility", trace):
        evaluations = [evaluate_card(profile, c) for c in candidates]

    eligible = [
        c for c, ev in zip(candidates, evaluations, strict=True) if ev.eligible
    ]
    rejected = [ev for ev in evaluations if not ev.eligible]
    near = near_misses(rejected)

    trace["eligible_count"] = float(len(eligible))
    trace["rejected_count"] = float(len(rejected))
    return {
        "evaluations": evaluations,
        "eligible_cards": eligible,
        "rejected": rejected,
        "near": near,
        "improvement_steps": improvement_steps(near, profile),
        "trace": trace,
    }


# ------------------------------------------------------------------- rank
def node_rank(state: dict[str, Any]) -> dict[str, Any]:
    """Deterministic ordering. Every score is a number in the trace."""
    profile: UserProfile = state["profile"]
    eligible: list[Card] = state.get("eligible_cards", [])
    trace = state.setdefault("trace", {})

    with Timer("rank", trace):
        ranked, dropped = rank(eligible, profile)
        recommendations = [
            Recommendation(
                rank=i + 1,
                card_id=c.card_id,
                name=c.name,
                bank=c.bank,
                tier=c.tier,
                score=round(score, 4),
                net_annual_value_rs=net_annual_value(c, profile),
                est_credit_limit_rs=est_credit_limit(c, profile),
                apr_pct=c.apr_pct,
                fee_payable_rs=fee_payable(c, profile),
                key_benefits=c.benefit_highlights[:2],
                sources=[f"{c.card_id}.md"],
            )
            for i, (c, score, _) in enumerate(ranked)
        ]

    return {
        "ranked": recommendations,
        "score_breakdown": [bd for _, _, bd in ranked],
        "dropped_card_ids": [c.card_id for c in dropped],
        "trace": trace,
    }


# ---------------------------------------------------------------- evidence
def node_evidence(state: dict[str, Any]) -> dict[str, Any]:
    """RAG for the top cards and the rejection reasons.

    Ranked first, retrieved second: running retrieval for every eligible card
    would spend latency and tokens on cards the customer never sees.
    """
    profile: UserProfile = state.get("profile")
    trace = state.setdefault("trace", {})
    ranked: list[Recommendation] = state.get("ranked", [])
    rejected: list[CardEvaluation] = state.get("rejected", [])

    with Timer("retrieval", trace):
        evidence: list[PolicyEvidence] = []
        if ranked:
            top_ids = {r.card_id for r in ranked}
            all_cards = {c.card_id: c for c in get_all_cards()}
            evidence += retrieve_for_cards(
                [all_cards[i] for i in top_ids if i in all_cards], profile
            )
        codes = list({r.code for r in rejected[:6]})
        codes += [r.code for r in state.get("gate_reasons", [])]
        if codes:
            evidence += retrieve_for_reason_codes(sorted(set(codes)), profile)

    seen: set[tuple] = set()
    unique = []
    for e in evidence:
        key = (e.card_id, e.section)
        if key not in seen:
            seen.add(key)
            unique.append(e)
    trace["evidence_chunks"] = float(len(unique))
    return {"evidence": unique[:12], "trace": trace}


# ----------------------------------------------------------------- explain
def node_explain(state: dict[str, Any]) -> dict[str, Any]:
    """Ground every sentence in computed facts and retrieved chunks.

    Retries with the verifier's complaints, then falls back to a template. The
    ladder exists because a recommendation with awkward prose is far better
    than a 500.
    """
    trace = state.setdefault("trace", {})
    s = get_settings()
    response = _assemble(state, summary="")
    evidence = state.get("evidence", [])
    used_llm = False

    with Timer("explain", trace):
        for attempt in range(s.max_explain_retries + 1):
            attempt_response = explain_response(response, evidence, state, attempt)
            report = verify_response(attempt_response, state)
            report.retries = attempt
            if report.passed:
                response = attempt_response
                response.verifier = report
                used_llm = attempt_response.summary != "" and not report.used_fallback
                break
            log.info(
                "verifier rejected attempt %s: %s",
                attempt,
                "; ".join(c.get("detail", "") for c in report.checks if not c.get("passed")),
            )
            response = attempt_response
            if attempt == s.max_explain_retries:
                # Out of retries: repair the text from the engine's own numbers
                # rather than shipping an unsupported claim.
                fallback = template_response(response, state)
                fallback.verifier = VerifierReport(
                    passed=True,
                    checks=report.checks,
                    notes=[*report.notes, "Fell back to the deterministic template."],
                    retries=attempt,
                    used_fallback=True,
                )
                response = fallback
                break

    return {
        "response": response,
        "summary": response.summary,
        "used_llm": used_llm,
        "verifier": response.verifier,
        "trace": trace,
    }


def _assemble(state: dict[str, Any], summary: str):
    """Build the response object from state. Numbers come from state only."""
    profile: UserProfile | None = state.get("profile")
    ranked: list[Recommendation] = state.get("ranked", [])
    rejected: list[CardEvaluation] = state.get("rejected", [])
    gate_reasons = state.get("gate_reasons", [])
    gate_passed = state.get("gate_passed", True)

    if not profile:
        status = "need_more_information"
        decision = "pending"
    elif profile and not state.get("profile_complete", True):
        # Intake asked for more information but a profile exists anyway. The
        # question is what the customer must read; do not also ship
        # recommendations they cannot act on yet.
        status = "need_more_information"
        decision = "pending"
    elif not gate_passed:
        status = "profile_rejected"
        decision = "rejected"
    elif ranked:
        status = "recommendations_available"
        decision = "recommended"
    else:
        status = "no_matching_cards"
        decision = "rejected"

    pending = status == "need_more_information"
    return RecommendationResponse(
        profile_id=profile.profile_id if profile else "guest",
        status=status,
        decision=decision,
        profile=profile,
        # Nothing ships alongside a pending question: a half-filled form must
        # not look like a recommendation.
        recommendations=[] if pending else ranked,
        rejections=[] if pending else sorted(rejected, key=lambda e: e.card_id)[:5],
        rejected_cards=[] if pending else gate_reasons,
        near_miss=[] if pending else [ev for ev in state.get("near", [])][:3],
        improvement_steps=[] if pending else state.get("improvement_steps", []),
        missing_fields=state.get("missing_fields", []),
        question=state.get("followup_question"),
        summary=summary,
        evidence=state.get("evidence", []),
        score_breakdown=state.get("score_breakdown", []),
        trace=state.get("trace", {}),
    )


# ------------------------------------------------------------------ output
def node_respond(state: dict[str, Any]) -> dict[str, Any]:
    """Final node. For the missing-info path this just returns the question."""
    response = state.get("response")
    if response is None:
        response = _assemble(state, summary="")
    if not response.summary.strip():
        # The explain node left nothing readable, which happens when the LLM
        # is unavailable. Fill it from the engine's own numbers and say so, so
        # the UI can distinguish "the model wrote this" from "the template did".
        response = template_response(response, state)
        if response.verifier is not None and not response.verifier.used_fallback:
            response.verifier.used_fallback = True
            response.verifier.notes.append(
                "Fell back to the deterministic template; no model was available."
            )
    return {"response": response}
