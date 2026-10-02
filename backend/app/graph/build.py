"""Graph construction.

A deliberately small LangGraph. Eight nodes, four conditional edges. The
"agentic" behaviour is that the *workflow* decides what happens next from the
state, not that a model chooses a chain of agents.

Two entry routes converge on the same decision path:

    chat  -> intake  -> complete? -> [build_profile -> gate -> ... ]
    form  ->            build_profile -------------------------------> [gate -> ... ]
"""

from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
    node_build_profile,
    node_evaluate,
    node_evidence,
    node_explain,
    node_followup,
    node_gate,
    node_intake,
    node_prefilter,
    node_rank,
    node_respond,
)
from app.graph.state import GraphState

log = logging.getLogger(__name__)

_GRAPH = None


def _needs_followup(state: GraphState) -> str:
    if state.get("profile_complete"):
        return "profile"
    return "followup"


def _needs_gate(state: GraphState) -> str:
    # The gate rejects the customer outright, so we skip straight to evidence
    # and explanation rather than scanning the catalogue for cards we will
    # never recommend.
    return "continue" if state.get("gate_passed") else "explain"


def _has_candidates(state: GraphState) -> str:
    return "rank" if state.get("eligible_cards") else "explain"


def build_graph():
    g = StateGraph(GraphState)

    g.add_node("intake", node_intake)
    g.add_node("followup", node_followup)
    g.add_node("build_profile", node_build_profile)
    g.add_node("gate", node_gate)
    g.add_node("prefilter", node_prefilter)
    g.add_node("evaluate", node_evaluate)
    g.add_node("rank", node_rank)
    g.add_node("evidence", node_evidence)
    g.add_node("explain", node_explain)
    g.add_node("respond", node_respond)

    # The form route has an already-validated profile, so it must skip intake
    # entirely: running the extractor over an empty message would overwrite it
    # with nulls and burn an LLM call to do it.
    g.add_conditional_edges(
        START,
        lambda s: "intake" if not s.get("explicit_profile") else "build_profile",
        {"intake": "intake", "build_profile": "build_profile"},
    )
    g.add_conditional_edges(
        "intake",
        _needs_followup,
        {"profile": "build_profile", "followup": "followup"},
    )
    g.add_edge("followup", END)
    g.add_edge("build_profile", "gate")
    g.add_conditional_edges("gate", _needs_gate, {"continue": "prefilter", "explain": "evidence"})
    g.add_edge("prefilter", "evaluate")
    g.add_conditional_edges(
        "evaluate", _has_candidates, {"rank": "rank", "explain": "evidence"}
    )
    g.add_edge("rank", "evidence")
    g.add_edge("evidence", "explain")
    g.add_edge("explain", "respond")
    g.add_edge("respond", END)

    return g.compile()


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


def reset_graph() -> None:
    global _GRAPH
    _GRAPH = None
