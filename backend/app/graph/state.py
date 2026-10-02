"""Graph state.

Everything the workflow can hold is declared here, so a reader can see the
full shape of a run without tracing control flow. Fields are Optional because
LangGraph merges partial updates: a node that returns only `response` must not
erase `profile`.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from app.schemas import (
    Card,
    CardEvaluation,
    PartialProfile,
    PolicyEvidence,
    Recommendation,
    RecommendationResponse,
    ScoreBreakdown,
    UserProfile,
    VerifierReport,
)


def _replace(_: Any, new: Any) -> Any:
    return new


class GraphState(TypedDict, total=False):
    # --- input ---
    session_id: str
    user_message: str
    prior_profile: PartialProfile | None
    mode: str  # "chat" | "form"
    explicit_profile: UserProfile | None

    # --- intake ---
    partial: PartialProfile | None
    missing_fields: list[str]
    followup_question: str | None
    profile_complete: bool

    # --- decision ---
    profile: UserProfile | None
    gate_reasons: list
    gate_passed: bool | None
    candidate_ids: list[str]
    candidates: list[Card]
    evaluations: list[CardEvaluation]
    eligible_cards: list[Card]
    rejected: list[CardEvaluation]
    near: list[CardEvaluation]
    improvement_steps: list[str]

    # --- ranking ---
    ranked: list[Recommendation]
    score_breakdown: list[ScoreBreakdown]
    dropped_card_ids: list[str]

    # --- evidence + prose ---
    evidence: list[PolicyEvidence]
    summary: str
    used_llm: bool

    # --- output ---
    response: RecommendationResponse | None
    verifier: VerifierReport | None
    trace: dict[str, float]
    # Set by the evaluation harness to bypass the explanation node. Never set on
    # a request path.
    skip_explain: bool
    chat_history: Annotated[list[dict], _replace]
