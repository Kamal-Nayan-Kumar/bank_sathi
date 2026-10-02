"""Run the graph.

This is the only place a request enters the workflow. Both the chat route and
the form route go through `run_graph`, so they cannot drift apart in behaviour.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.graph.build import get_graph
from app.schemas import (
    NaturalLanguageRequest,
    PartialProfile,
    ProfileRequest,
    RecommendationResponse,
    UserProfile,
)

log = logging.getLogger(__name__)


def run_chat(req: NaturalLanguageRequest, prior: PartialProfile | None = None) -> RecommendationResponse:
    state: dict[str, Any] = {
        "session_id": req.session_id or "guest",
        "user_message": req.message,
        "prior_profile": prior,
        "mode": "chat",
        "trace": {},
    }
    return _invoke(state)


def run_profile(req: ProfileRequest) -> RecommendationResponse:
    """Form route. Pydantic has already validated the body, so intake is skipped."""
    profile: UserProfile = req.profile
    state: dict[str, Any] = {
        "session_id": profile.profile_id,
        "mode": "form",
        "explicit_profile": profile,
        "profile": profile,
        "profile_complete": True,
        "missing_fields": [],
        "trace": {},
    }
    return _invoke(state)


def _invoke(state: dict[str, Any]) -> RecommendationResponse:
    started = time.perf_counter()
    graph = get_graph()
    out = graph.invoke(state)
    response: RecommendationResponse | None = out.get("response")
    if response is None:
        # Should not happen, but an API returning null is worse than a clear
        # refusal, so we surface it rather than raising into the client.
        from app.policy import get_policy

        response = RecommendationResponse(
            profile_id=state.get("session_id", "guest"),
            status="need_more_information",
            decision="pending",
            summary="I could not complete that request. Please try again.",
            question="Could you tell me your monthly income and where you live?",
        )
        response.trace = {"error": 1.0}
    trace = dict(response.trace)
    trace["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
    response.trace = trace
    return response
