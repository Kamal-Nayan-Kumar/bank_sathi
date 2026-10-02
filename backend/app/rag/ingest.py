"""Ingest the generated policy markdown into the vector store.

Chunking is heading-aware: a section is the unit, not a fixed token window. A
query about the annual fee waiver should retrieve the fee section whole rather
than a fragment that straddles two topics.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.config import get_settings
from app.rag.store import Chunk, get_embedder, get_store

_H1 = re.compile(r"^# (.+)$")
_H2 = re.compile(r"^## (.+)$")
_H3 = re.compile(r"^### (.+)$")
_FENCE = re.compile(r"^```")
MIN_CHARS = 80  # below this a section is a fragment, not worth indexing alone


def _split_sections(md: str) -> list[tuple[str, str]]:
    """Yield (heading, body) pairs, keeping fenced code blocks intact."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    in_fence = False
    for line in md.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            sections[-1][1].append(line)
            continue
        if not in_fence:
            m = _H1.match(line) or _H2.match(line) or _H3.match(line)
            if m:
                sections.append([m.group(1), []])
                continue
        sections[-1][1].append(line)

    out = []
    for heading, lines in sections:
        body = "\n".join(lines).strip()
        if body:
            out.append((heading, body))
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:48]


def chunk_markdown(doc_name: str, md: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    doc_title = ""
    for heading, body in _split_sections(md):
        if heading and not doc_title:
            doc_title = heading
        section = _slug(heading) if heading else "intro"
        # Oversized sections are split on blank lines rather than hard-cut, so a
        # chunk never ends mid-sentence.
        pieces = [body]
        if len(body) > 1600:
            pieces, buf, size = [], [], 0
            for para in body.split("\n\n"):
                if size + len(para) > 1600 and buf:
                    pieces.append("\n\n".join(buf))
                    buf, size = [], 0
                buf.append(para)
                size += len(para)
            if buf:
                pieces.append("\n\n".join(buf))
        for idx, piece in enumerate(pieces):
            if len(piece.strip()) < MIN_CHARS:
                continue
            chunks.append(
                Chunk(
                    id=f"{_slug(doc_name)}__{section}__{idx}",
                    # The heading is prepended to the indexed text so a section
                    # like "Annual fee" matches a query about fees.
                    text=f"{heading}\n\n{piece}".strip(),
                    metadata={
                        "source": f"{doc_name}.md",
                        "section": section,
                        "heading": heading or doc_title,
                        "card_id": None,
                        "document_type": _doc_type(doc_name),
                    },
                )
            )
    return chunks


def _doc_type(doc_name: str) -> str:
    return {
        "underwriting_policy": "underwriting",
        "reward_valuation": "rewards",
        "rejection_reasons": "rejections",
        "improving_profile": "improvement",
    }.get(doc_name, "policy")


def build_card_policy_chunks(cards: list) -> list[Chunk]:
    """One chunk per card, rendered from the same row the engine will read.

    Generated from the card object rather than a separate document so a policy
    sentence cannot state an income minimum the engine does not use.
    """
    from app.thresholds import money as _money

    chunks: list[Chunk] = []
    for c in cards:
        rewards = "; ".join(
            f"{r.category}: {r.value_pct}% back"
            + (f", capped at {_money(r.monthly_cap_rs)} a month" if r.monthly_cap_rs else "")
            for r in c.reward_rules
        )
        waiver = (
            f"Waived on {_money(c.fee_waiver_spend)} of annual card spend."
            if c.fee_waiver_spend
            else "There is no spend-based fee waiver on this card."
        )
        text = (
            f"{c.name}, {c.tier} tier card from {c.bank} on the {c.network} network. "
            f"Minimum monthly income {_money(c.min_monthly_income)}. "
            f"Minimum credit score "
            f"{c.min_cibil if c.min_cibil is not None else 'not required'}. "
            f"Applicant age {c.min_age} to {c.max_age}. "
            f"Annual fee {_money(c.annual_fee)}, joining fee {_money(c.joining_fee)}. "
            f"{waiver} "
            f"Rewards: {rewards}. "
            f"Lounge visits included per year: {c.lounge_visits_per_year}. "
            f"APR {c.apr_pct}%. "
            f"Best for {c.segment} spending."
        )
        chunks.append(
            Chunk(
                id=f"card__{c.card_id}",
                text=text,
                metadata={
                    "source": f"{c.card_id}.md",
                    "card_id": c.card_id,
                    "section": "card_summary",
                    "heading": f"{c.name} ({c.card_id})",
                    "document_type": "card",
                },
            )
        )
    return chunks


def ingest(
    docs_dir: Path | None = None, cards: list | None = None, force: bool = False
) -> dict[str, int]:
    """Load the corpus into the vector store.

    Skips the upsert when the store already holds exactly what we would write.
    That is the normal production case: every boot would otherwise re-embed 144
    chunks, which means downloading and loading the model for no new content.
    Pass force=True (make data does) to rebuild unconditionally.
    """
    s = get_settings()
    docs_dir = docs_dir or s.resolve(s.policy_docs_dir)
    store = get_store()

    all_chunks: list[Chunk] = []
    for path in sorted(docs_dir.glob("*.md")):
        all_chunks.extend(chunk_markdown(path.stem, path.read_text()))
    if cards:
        all_chunks.extend(build_card_policy_chunks(cards))

    if not all_chunks:
        return {"chunks": 0, "docs": 0, "skipped": True}
    if not force:
        try:
            if store.count() == len(all_chunks):
                return {
                    "chunks": len(all_chunks),
                    "docs": len(list(docs_dir.glob("*.md"))),
                    "skipped": True,
                }
        except Exception:  # noqa: BLE001 - an unreadable store just gets rebuilt
            pass
    vectors = get_embedder().embed([c.text for c in all_chunks])
    store.upsert(all_chunks, vectors)
    return {
        "chunks": len(all_chunks),
        "docs": len(list(docs_dir.glob("*.md"))),
        "skipped": False,
    }
