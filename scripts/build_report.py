#!/usr/bin/env python
"""Build the submission report PDF.

    python scripts/build_report.py

Writes report/Card_Sathi_Report.pdf plus the mermaid sources for every
diagram, so the figures stay reproducible and editable.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO_ROOT / "report"
REPORT_DIR.mkdir(exist_ok=True)

INK = "#12211F"
GREEN = "#1F4B3F"
LIGHT = "#DDE7E3"
RED = "#B03A2E"
OCHRE = "#9A7414"
GREY = "#4A5A56"
PAPER = "#F7F9F7"


# ---------------------------------------------------------------- diagrams
def _fig(w: float = 7.5, h: float = 4.0):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")
    fig.patch.set_facecolor("white")
    return fig, ax, FancyBboxPatch, FancyArrowPatch


def _box(ax, Box, x, y, w, h, text, *, fill="white", edge=INK, tcolor=INK,
         size=8.5, bold=False, sub=None):
    ax.add_patch(Box((x, y), w, h, boxstyle="round,pad=0.6,rounding_size=1.2",
                     facecolor=fill, edgecolor=edge, linewidth=1.4))
    ax.text(x + w / 2, y + h / 2 + (2.5 if sub else 0), text, ha="center", va="center",
            fontsize=size, color=tcolor, weight="bold" if bold else "normal",
            family="sans-serif")
    if sub:
        ax.text(x + w / 2, y + h / 2 - 4.5, sub, ha="center", va="center",
                fontsize=6.8, color=tcolor if tcolor != "white" else "white",
                alpha=0.85 if tcolor == "white" else 0.75, family="sans-serif")


def _arrow(ax, Arrow, x1, y1, x2, y2, label=None):
    ax.add_patch(Arrow((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=12,
                       color=INK, linewidth=1.2, shrinkA=1, shrinkB=3))
    if label:
        ax.text((x1 + x2) / 2, (y1 + y2) / 2 + 2.2, label, ha="center", va="bottom",
                fontsize=6.5, color=GREY, family="sans-serif")


def _save(fig, name: str) -> str:
    import matplotlib.pyplot as plt

    path = REPORT_DIR / f"{name}.png"
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def trust_diagram() -> str:
    """The trust hierarchy: who is allowed to decide what."""
    fig, ax, Box, Arrow = _fig(7.5, 4.6)
    levels = [
        ("Business rules  (plain Python)", "highest — decides eligibility, ranking, every number", GREEN, "white"),
        ("Catalogue  (PostgreSQL / Neon)", "structured facts: income floors, fees, reward rates", "white", INK),
        ("Policy prose  (Qdrant + MiniLM)", "why a rule exists — explains, never decides", "white", INK),
        ("LLM  (Groq, OpenRouter fallback)", "reads messages, writes sentences — prose only", "#F1EFE9", INK),
    ]
    y = 78
    levels_list = list(levels)
    for i, (title, sub, fill, tc) in enumerate(levels_list):
        _box(ax, Box, 8, y, 84, 13, title, sub=sub, fill=fill, tcolor=tc, bold=True)
        if i < len(levels_list) - 1:
            _arrow(ax, Arrow, 50, y - 1, 50, y - 8)
        y -= 20
    ax.text(50, 93, "Nothing below a level can overrule the level above it",
            ha="center", fontsize=8, style="italic", color=GREY)
    return _save(fig, "fig_trust")


def pipeline_diagram() -> str:
    """The LangGraph workflow."""
    fig, ax, Box, Arrow = _fig(7.5, 6.4)
    # top row: entry
    _box(ax, Box, 2, 84, 22, 11, "chat message", sub="natural language", size=7.5)
    _box(ax, Box, 76, 84, 22, 11, "form profile", sub="validated JSON", size=7.5)
    ax.text(50, 89.5, "two doors, one hallway", ha="center", fontsize=7, style="italic", color=GREY)
    _arrow(ax, Arrow, 13, 83, 38, 74)
    _arrow(ax, Arrow, 87, 83, 62, 74)
    # intake
    _box(ax, Box, 30, 63, 40, 11, "intake  +  follow-up?", sub="LLM extracts; missing → ask one question", size=7.5)
    _arrow(ax, Arrow, 50, 62, 50, 55)
    _box(ax, Box, 30, 44, 40, 11, "global gate", sub="code: reject outright, or continue", size=7.5, fill=LIGHT, edge=GREEN)
    _arrow(ax, Arrow, 50, 43, 50, 36)
    _box(ax, Box, 30, 25, 40, 11, "prefilter → evaluate → rank", sub="SQL narrows · Python checks · code scores", size=7.5, fill=LIGHT, edge=GREEN)
    _arrow(ax, Arrow, 50, 24, 50, 17)
    _box(ax, Box, 30, 6, 40, 11, "evidence → explain → verifier", sub="RAG grounds · LLM writes · checks block", size=7.5)
    # side notes
    ax.text(4, 40, "decided\nin code", ha="center", fontsize=6.5, color=GREEN, weight="bold")
    ax.text(96, 11.5, "model writes\nchecks block", ha="center", fontsize=6.5, color=GREY)
    return _save(fig, "fig_pipeline")


def data_diagram() -> str:
    """How the dataset is built from a seed."""
    fig, ax, Box, Arrow = _fig(7.5, 3.4)
    _box(ax, Box, 1, 40, 20, 20, "tier templates", sub="+ fixed seed", size=7.5)
    _arrow(ax, Arrow, 22, 50, 28, 50)
    _box(ax, Box, 29, 40, 22, 20, "120 cards", sub="validated by Pydantic", size=7.5)
    _arrow(ax, Arrow, 52, 55, 58, 55)
    _arrow(ax, Arrow, 52, 45, 58, 45)
    _box(ax, Box, 59, 55, 40, 15, "PostgreSQL — the catalogue", sub="income floors, fees, rates", size=7.5, fill=LIGHT, edge=GREEN)
    _box(ax, Box, 59, 32, 40, 15, "Qdrant — one chunk per card", sub="rendered from the same row", size=7.5)
    ax.text(50, 82, "the card row is the single source of truth; both stores derive from it",
            ha="center", fontsize=7.5, style="italic", color=GREY)
    ax.text(50, 14, "300 profiles + 22 hand-written edge cases → ground truth computed by the engine itself",
            ha="center", fontsize=7.5, style="italic", color=GREY)
    return _save(fig, "fig_data")


def rag_diagram() -> str:
    """What retrieval is for: evidence, not answers."""
    fig, ax, Box, Arrow = _fig(7.5, 3.6)
    _box(ax, Box, 1, 40, 24, 20, "4 policy pages", sub="hand-written markdown", size=7.5)
    _box(ax, Box, 1, 12, 24, 20, "120 card summaries", sub="generated from card rows", size=7.5)
    _arrow(ax, Arrow, 26, 50, 34, 50, label="chunk + MiniLM")
    _box(ax, Box, 35, 38, 28, 24, "Qdrant", sub="keyword index on card_id", size=8, fill=LIGHT, edge=GREEN, bold=True)
    _arrow(ax, Arrow, 64, 50, 72, 50, label="top-k, filtered")
    _box(ax, Box, 73, 38, 26, 24, "evidence only", sub="the decision is already made", size=7.5)
    ax.text(50, 84, "retrieval never answers — it only supplies the sentences the answer cites",
            ha="center", fontsize=7.5, style="italic", color=GREY)
    return _save(fig, "fig_rag")


def deploy_diagram() -> str:
    fig, ax, Box, Arrow = _fig(7.5, 3.2)
    _box(ax, Box, 1, 40, 22, 20, "browser", sub="Vercel static build", size=7.5)
    _arrow(ax, Arrow, 24, 50, 30, 50)
    _box(ax, Box, 31, 40, 26, 20, "FastAPI + graph", sub="Render web service", size=7.5, fill=LIGHT, edge=GREEN, bold=True)
    _arrow(ax, Arrow, 58, 55, 64, 55)
    _arrow(ax, Arrow, 58, 45, 64, 45)
    _box(ax, Box, 65, 55, 34, 15, "Neon Postgres", sub="120-card catalogue", size=7.5)
    _box(ax, Box, 65, 32, 34, 15, "Qdrant Cloud", sub="144 policy chunks", size=7.5)
    ax.text(44, 50, "  ", ha="center", fontsize=7)
    ax.text(50, 82, "every dependency degrades: no key still serves, on SQLite + local fallbacks",
            ha="center", fontsize=7.5, style="italic", color=GREY)
    return _save(fig, "fig_deploy")


MERMAID = {
    "trust": """flowchart TD
    subgraph Trust["Trust hierarchy — nothing below overrules above"]
      RULES["Business rules<br/>(plain Python)<br/>decides everything"]
      DB[("Catalogue<br/>(PostgreSQL)")]
      RAG[("Policy prose<br/>(Qdrant)")]
      LLM["LLM (Groq → OpenRouter)<br/>prose only"]
      RULES --> DB --> RAG --> LLM
    end""",
    "pipeline": """flowchart TD
    A[chat message] --> I[intake: LLM extracts profile]
    F[form profile] --> G[build profile: validate]
    I -->|incomplete| Q[ask one question]
    I -->|complete| G
    G --> GATE{global gate}
    GATE -->|fail| EVID[evidence]
    GATE -->|pass| PRE[prefilter: SQL narrows catalogue]
    PRE --> EVA[evaluate: Python checks every card]
    EVA -->|none eligible| EVID
    EVA -->|some eligible| RANK[rank: deterministic scoring]
    RANK --> EVID[retrieve policy evidence]
    EVID --> EXP[explain: LLM writes from facts]
    EXP --> VER{verifier}
    VER -->|fail| EXP
    VER -->|pass| OUT[final response]
    VER -->|2 failures| TPL[template fallback]""",
    "data": """flowchart LR
    T[tier templates + fixed seed] --> C[120 cards<br/>Pydantic-validated]
    C --> PG[(PostgreSQL<br/>catalogue)]
    C --> QD[(Qdrant<br/>one chunk per card)]
    C --> P[300 profiles + 22 edge cases]
    P --> GT[ground truth<br/>computed by the engine]""",
}


def main() -> None:
    import sys

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from report_text import (
        appendix_mermaid,
        architecture,
        cover,
        dataset,
        deploy,
        evaluation,
        langgraph,
        limits,
        market,
        problem,
        rag_llm,
        styles,
    )

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate

    s = styles()

    date = time.strftime("%d %B %Y")
    links = {
        "github": "https://github.com/kamal-nayan/card-sathi",
        "demo": "https://card-sathi.vercel.app",
        "api": "https://card-sathi-api.onrender.com",
    }
    for key in ("GITHUB_URL", "DEMO_URL", "API_URL"):
        pass
    import os

    links["github"] = os.environ.get("GITHUB_URL", links["github"])
    links["demo"] = os.environ.get("DEMO_URL", links["demo"])
    links["api"] = os.environ.get("API_URL", links["api"])

    figs = {
        "trust": trust_diagram(),
        "pipeline": pipeline_diagram(),
        "data": data_diagram(),
        "rag": rag_diagram(),
        "deploy": deploy_diagram(),
    }
    (REPORT_DIR / "mermaid_sources.md").write_text(
        "\n\n".join(f"## {k}\n```mermaid\n{v}\n```" for k, v in MERMAID.items())
    )

    story = []
    story += cover(s, date, links)
    story += market(s)
    story += problem(s)
    story += architecture(s, figs["trust"])
    story += langgraph(s, figs["pipeline"])
    story += rag_llm(s, figs["rag"])
    story += dataset(s, figs["data"])
    story += evaluation(s, None)
    story += deploy(s, figs["deploy"], links)
    story += limits(s)
    story += appendix_mermaid(s, MERMAID)

    out = REPORT_DIR / "Card_Sathi_Report.pdf"
    doc = BaseDocTemplate(
        str(out), pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title="Card Sathi — project report", author="Kamal Nayan",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor("#7D8B87")
        canvas.drawString(doc.leftMargin, 12 * mm, "Card Sathi — project report")
        canvas.drawRightString(doc.pagesize[0] - doc.rightMargin, 12 * mm,
                               f"Page {doc.page}")
        canvas.restoreState()

    doc.addPageTemplates([PageTemplate(id="body", frames=[frame], onPage=footer)])
    doc.build(story)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
