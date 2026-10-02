"""Report body. Kept separate from build_report.py so the writing stays readable.

Tone rules I held myself to: first person, concrete, no padding. Every claim
should survive an evaluator asking "how do you know that".
"""

from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Image,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

INK = "#12211F"
GREEN = "#1F4B3F"
GREY = "#4A5A56"
RED = "#B03A2E"
RULE = "#C9D2CE"


def styles():
    from reportlab.lib.styles import getSampleStyleSheet

    base = getSampleStyleSheet()
    s = {}
    s["cover_title"] = ParagraphStyle(
        "cover_title", parent=base["Title"], fontSize=30, leading=34,
        textColor=INK, fontName="Helvetica-Bold", spaceAfter=4 * mm,
    )
    s["cover_sub"] = ParagraphStyle(
        "cover_sub", parent=base["Normal"], fontSize=12, leading=16,
        textColor=GREY, fontName="Helvetica", spaceAfter=8 * mm,
    )
    s["h1"] = ParagraphStyle(
        "h1", parent=base["Heading1"], fontSize=16, leading=20,
        textColor=GREEN, fontName="Helvetica-Bold",
        spaceBefore=10 * mm, spaceAfter=4 * mm, keepWithNext=True,
    )
    s["h2"] = ParagraphStyle(
        "h2", parent=base["Heading2"], fontSize=12, leading=15,
        textColor=INK, fontName="Helvetica-Bold",
        spaceBefore=6 * mm, spaceAfter=2.5 * mm, keepWithNext=True,
    )
    s["body"] = ParagraphStyle(
        "body", parent=base["Normal"], fontSize=10, leading=14.5,
        textColor=INK, fontName="Helvetica", spaceAfter=2.5 * mm,
        alignment=4,
    )
    s["bullet"] = ParagraphStyle(
        "bullet", parent=s["body"], leftIndent=10 * mm, bulletIndent=5 * mm,
        spaceAfter=1.5 * mm,
    )
    s["caption"] = ParagraphStyle(
        "caption", parent=base["Normal"], fontSize=8.5, leading=11,
        textColor=GREY, fontName="Helvetica-Oblique", alignment=1,
        spaceBefore=2 * mm, spaceAfter=5 * mm,
    )
    s["mono"] = ParagraphStyle(
        "mono", parent=base["Code"], fontSize=8, leading=10.5,
        textColor=INK, fontName="Courier", backColor="#F2F4F2",
        borderPadding=3 * mm, spaceAfter=3 * mm,
    )
    s["table_cell"] = ParagraphStyle(
        "table_cell", parent=base["Normal"], fontSize=8.5, leading=11,
        textColor=INK, fontName="Helvetica",
    )
    s["table_head"] = ParagraphStyle(
        "table_head", parent=s["table_cell"], fontName="Helvetica-Bold",
        textColor="white",
    )
    return s


def rule():
    return HRFlowable(width="100%", thickness=0.6, color=RULE, spaceAfter=3 * mm)


def bullets(s, items):
    return ListFlowable(
        [ListItem(Paragraph(x, s["bullet"]), bulletColor=GREEN) for x in items],
        bulletType="bullet", leftIndent=10 * mm,
    )


def table(s, header, rows, widths=None):
    data = [[Paragraph(h, s["table_head"]) for h in header]]
    for row in rows:
        data.append([Paragraph(str(c), s["table_cell"]) for c in row])
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), GREEN),
        ("TEXTCOLOR", (0, 0), (-1, 0), "white"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), ["white", "#F2F4F2"]),
        ("GRID", (0, 0), (-1, -1), 0.5, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def img(path, width_mm=150):
    return Image(path, width=width_mm * mm, height=width_mm * 0.55 * mm,
                 hAlign="CENTER")


# ------------------------------------------------------------------ sections
def cover(s, date, links):
    story = [Spacer(1, 28 * mm)]
    story.append(Paragraph("Card Sathi", s["cover_title"]))
    story.append(Paragraph(
        "A credit-card recommendation engine that shows its working:<br/>"
        "rules decide, retrieval explains, a verifier checks.", s["cover_sub"]))
    story.append(rule())
    story.append(Paragraph(
        "Built as an agentic AI application over a structured catalogue, "
        "a policy corpus, and two language models. This report covers the market "
        "context, the architecture and why I chose it, the dataset I generated, "
        "how I evaluated it, and where it runs.", s["body"]))
    story.append(Spacer(1, 6 * mm))
    story.append(table(s, ["", ""], [
        ["Author", "Kamal Nayan"],
        ["Date", date],
        ["Code", links["github"]],
        ["Live demo", links["demo"]],
        ["API", links["api"]],
    ], widths=[28 * mm, 130 * mm]))
    return story


def market(s):
    story = [Paragraph("1 &nbsp; The market I built for", s["h1"])]
    story.append(Paragraph(
        "India had more than 100 million credit cards in force by 2024, and the "
        "number keeps climbing as banks push issuance beyond the metros. Yet only "
        "a single-digit share of adults holds one. The growth story and the "
        "penetration story point in opposite directions: cards are being sold "
        "aggressively into a population that mostly has no credit history, a thin "
        "file, or a score it does not understand.", s["body"]))
    story.append(Paragraph(
        "Three facts about this market shaped every design decision in this project:",
        s["body"]))
    story.append(bullets(s, [
        "<b>Eligibility is opaque.</b> Banks publish minimum income and score "
        "requirements per card, but a first-time applicant cannot tell which of "
        "fifty similar cards they qualify for — or why a rejection happened. "
        "The result is scattered applications, and every application leaves a "
        "hard enquiry on the file, which lowers the score it was meant to use.",
        "<b>The score system is unforgiving and slow-moving.</b> CIBIL scores run "
        "300–900. A missed payment stays on file for years; a thin file means "
        "starting from secured and starter products. Advice like “take a loan to "
        "build your score” circulates widely and usually hurts the borrower. Any "
        "system giving guidance here has to be careful about what it suggests.",
        "<b>The catalogue is wide and samey.</b> Cashback, travel, fuel, dining, "
        "shopping, premium tiers — the differences that matter (caps, waiver "
        "thresholds, lounge conditions) hide in terms documents nobody reads. "
        "Comparing two cards honestly requires arithmetic on the customer's own "
        "spending, not a star rating.",
    ]))
    story.append(Paragraph(
        "That is the gap Card Sathi sits in: not another chatbot that chats about "
        "cards, but a decision engine that checks every card against the "
        "applicant's numbers, ranks what survives by rupee value, and shows the "
        "working. The audience I had in mind is the applicant with a thin file "
        "and real spending — exactly the customer the current market serves worst.",
        s["body"]))
    return story


def problem(s):
    story = [Paragraph("2 &nbsp; Problem and what I built", s["h1"])]
    story.append(Paragraph(
        "The problem, stated plainly: <b>a customer cannot answer “which card can "
        "I actually get, and which of those is worth most to me?”</b> without "
        "either applying blindly or reading fifty terms documents. Existing "
        "comparison sites rank by sponsorship and ask nothing about spending; "
        "bank chatbots answer questions but make no decisions.", s["body"]))
    story.append(Paragraph("Card Sathi answers it in two parts:", s["body"]))
    story.append(bullets(s, [
        "<b>Can you get it?</b> — a deterministic eligibility engine checks the "
        "company-wide rules once, then every card's published requirements "
        "against the profile. No model is involved. Failures carry reason codes "
        "and exact gaps (“Rs 6,000 more a month”, “25 score points short”).",
        "<b>Is it worth it to you?</b> — a scoring function reduces each eligible "
        "card to net annual rupee value on <i>your</i> spending, plus three "
        "smaller fit terms, and ranks them. The interface draws the four "
        "components as a proportional bar, so the order is visibly derived.",
    ]))
    story.append(Paragraph(
        "Around that core sit the language parts: an extractor that turns free "
        "text into a profile (asking one question at a time when something is "
        "missing), a retriever that grounds explanations in policy text, a "
        "generator that writes the summary, and a verifier that blocks anything "
        "the engine did not say. Two doors in — describe yourself in words, or "
        "fill the form — one hallway through.", s["body"]))
    return story


def architecture(s, fig_trust):
    story = [Paragraph("3 &nbsp; Architecture and why I chose it", s["h1"])]
    story.append(Paragraph(
        "The single most important decision in this project is the trust "
        "hierarchy. Everything else follows from it:", s["body"]))
    story.append(img(fig_trust))
    story.append(Paragraph(
        "Figure 1 — nothing below a level can overrule the level above it. "
        "The model sits at the bottom on purpose.", s["caption"]))
    story.append(Paragraph("Why it looks like this:", s["h2"]))
    story.append(bullets(s, [
        "<b>Rules in plain Python, not in a model.</b> “Minimum income ≤ 45,000” "
        "is an exact numeric comparison. Embeddings are fuzzy by design and "
        "cannot reliably do it — and when they get it wrong there is nothing to "
        "inspect. The engine is ~600 lines I can test at every boundary.",
        "<b>PostgreSQL for the catalogue, Qdrant for the prose.</b> Card facts "
        "(income floors, fees, reward rates) live in SQL where they can be "
        "filtered exactly. Terms and policy guidance live in the vector store, "
        "where semantic search is genuinely useful. Mixing the two — embedding "
        "card rows and hoping the retriever does arithmetic — is the failure "
        "mode I designed this split to avoid.",
        "<b>The LLM handles language, never decisions.</b> It extracts profiles, "
        "asks follow-ups, and writes summaries from computed facts. It cannot "
        "mark a card eligible, change a rank, or write SQL — there is simply no "
        "tool or prompt path that lets it.",
        "<b>Thresholds live in one Python module, not a YAML file.</b> I started "
        "with policy.yaml as the source of truth and generated the RAG documents "
        "from it. That solved consistency but created a worse problem: a policy "
        "owner editing the markdown had to know about the YAML behind it. Now "
        "backend/app/thresholds.py holds every number, the markdown holds prose "
        "with no numbers at all, and a test fails the build if a number ever "
        "appears in the docs. Two artefacts that cannot disagree because they "
        "were never in the same place.",
        "<b>Embeddings run locally.</b> all-MiniLM-L6-v2 through fastembed — "
        "22M parameters, fast on CPU. No embedding API key, no per-request cost, "
        "and retrieval quality never depends on a third-party key being present.",
    ]))
    return story


def langgraph(s, fig_pipeline):
    story = [Paragraph("4 &nbsp; The LangGraph pipeline", s["h1"])]
    story.append(Paragraph(
        "I used LangGraph for the orchestration because the workflow has "
        "genuine branches: the profile may be incomplete, the gate may reject "
        "outright, there may be no eligible cards, and the final text must pass "
        "verification. Eight nodes, four conditional edges — a fixed graph, not "
        "an agent choosing tools. The “agentic” behaviour is that the <i>workflow</i> "
        "decides what happens next from the state.", s["body"]))
    story.append(img(fig_pipeline))
    story.append(Paragraph(
        "Figure 2 — the decision path. The model appears in exactly two nodes, "
        "neither of which can change a number.", s["caption"]))
    story.append(Paragraph("What each node owns:", s["h2"]))
    story.append(table(s,
        ["Node", "Owns", "Must never"],
        [
            ["intake", "Message → partial profile. Masks PII first, leaves blanks null.", "invent a value"],
            ["followup", "One question, then ends the turn.", "ask for two things"],
            ["build_profile", "Partial → validated UserProfile. Fails loudly.", "default an income to zero"],
            ["gate", "Company-wide screen: age, score floor, defaults, enquiries, usage.", "consult a model"],
            ["prefilter", "Parameterised SQL narrows 120 cards to ~60.", "take model-written SQL"],
            ["evaluate", "Every rule for every survivor, in Python. Returns reason codes + gaps.", "stop at the first failure"],
            ["rank", "Net-value scoring, deterministic order.", "break ties opaquely"],
            ["evidence", "RAG for the top cards and the rejection reasons only.", "retrieve for cards nobody sees"],
            ["explain", "Prose from computed facts + chunks. Retries ≤2, then template.", "use a number not in the facts"],
            ["verifier", "12 checks: eligibility, order, numbers, PII, codes, evidence.", "change a decision"],
        ], widths=[28 * mm, 68 * mm, 62 * mm]))
    story.append(Paragraph(
        "State is a single TypedDict every node reads and partially updates, so a "
        "failure can be attributed to exactly one step — and the interface shows "
        "each step with its real timing on every result page.", s["body"]))
    return story


def rag_llm(s, fig_rag):
    story = [Paragraph("5 &nbsp; LLM and RAG design", s["h1"])]
    story.append(Paragraph(
        "Two models, two jobs. Groq's openai/gpt-oss-120b is the primary for both "
        "extraction and explanation; when its free tier rate-limits — which it "
        "does, often — OpenRouter's free Nemotron endpoint takes over. I probed "
        "all 17 free OpenRouter endpoints before choosing it: only five honoured "
        "JSON mode, which extraction depends on. Failover on a 429 is immediate; "
        "retrying a saturated free tier just adds the backoff to the customer's "
        "wait, which I measured at ~40 wasted seconds before changing it.",
        s["body"]))
    story.append(Paragraph("Extraction is built to under-claim.", s["h2"]))
    story.append(bullets(s, [
        "The prompt says to leave fields null rather than guess. A wrong income "
        "produces a confident wrong recommendation; a blank produces one question.",
        "The regex fallback handles real formats (“1.2L”, “95k a month”, “8.5 LPA”) "
        "and runs the whole pipeline offline — which is also what CI tests.",
        "Model output is coerced into the Pydantic schema (“online_shopping” as a "
        "preference becomes “cashback”); what cannot be coerced is dropped, and "
        "only then does the regex path take over.",
    ]))
    story.append(Paragraph("Retrieval supplies evidence, never answers.", s["h2"]))
    story.append(img(fig_rag))
    story.append(Paragraph(
        "Figure 3 — the corpus and what retrieval is allowed to do with it.",
        s["caption"]))
    story.append(bullets(s, [
        "Four hand-written policy pages plus one chunk per card, rendered from "
        "the card's own database row — so a policy sentence cannot state an "
        "income minimum the engine does not enforce.",
        "Card queries are filtered by card_id (with a Qdrant keyword index — "
        "missing it 400s every filtered query, which I found out in production). "
        "The explanation for card A can never be grounded in card B's terms.",
        "The verifier's number check whitelists only engine-computed figures, "
        "retrieved policy text, and the customer's own numbers, with a 2% "
        "tolerance for honest rounding.",
    ]))
    return story


def dataset(s, fig_data):
    story = [Paragraph("6 &nbsp; Dataset preparation", s["h1"])]
    story.append(Paragraph(
        "There was no catalogue to download, so I generated one — but not by "
        "asking a model for “120 credit cards”, which produces fees that "
        "contradict their own reward caps. Templates per segment (cashback, "
        "travel, fuel, dining, shopping, premium, beginner, business, "
        "lifestyle, student) crossed with four tiers, drawn from a fixed seed. "
        "Same seed, byte-identical catalogue, on any machine.", s["body"]))
    story.append(img(fig_data))
    story.append(Paragraph(
        "Figure 4 — one source of truth, two stores, one evaluation set.",
        s["caption"]))
    story.append(bullets(s, [
        "<b>120 cards, 10 segments × 4 tiers.</b> Every row validated by Pydantic. "
        "A few are withdrawn so the inactive-card path is real. Fields with no "
        "published analogue (APR, lounge counts, caps) are disclosed per card in "
        "assumed_fields and counted in the README.",
        "<b>300 profiles + 22 hand-written edge cases.</b> Segments from student "
        "to senior with lognormal incomes and score/payment correlations — plus "
        "the awkward cases generators never land on: income exactly on a "
        "minimum, ±1 rupee, age window edges, gate boundaries, no history, "
        "contradictory preferences.",
        "<b>Ground truth computed by the engine itself.</b> Said plainly: this "
        "measures spec conformance and pipeline faithfulness, not policy "
        "correctness. A 100% here means the pipeline reproduces its own rules "
        "through SQL, Python, ranking and the API — which is worth knowing, as "
        "long as nobody calls it model accuracy. I label it as conformance "
        "everywhere it appears.",
    ]))
    return story


def evaluation(s, metrics):
    story = [Paragraph("7 &nbsp; How I evaluated it", s["h1"])]
    story.append(Paragraph(
        "Five independent measurements, because one number would hide which part "
        "is weak. n=322 profiles over 120 cards unless stated.", s["body"]))
    story.append(table(s,
        ["What", "Metric", "Result"],
        [
            ["Rules (spec conformance)", "Gate agreement", "100.0%"],
            ["", "Eligibility agreement (37,352 card checks)", "100.0%"],
            ["", "False eligible rate", "0.0 — never approved a card the rules reject"],
            ["Ranking vs. deterministic oracle", "Top-1 accuracy", "98.2%"],
            ["", "Top-3 hit rate", "99.1%"],
            ["", "NDCG@3", "98.4%"],
            ["Explanations (n=25)", "Verifier pass rate", "100%, 0 retries, 0 fallbacks"],
            ["Extraction (n=40)", "Field accuracy, stated fields", "73% (LLM) / 71% (regex)"],
            ["", "Correct blanks — must stay empty", "88%"],
            ["Robustness", "7/7 invariants", "injection, PII, determinism all hold"],
            ["Latency, no prose", "p50 / p95", "161ms / 2.3s"],
        ], widths=[42 * mm, 58 * mm, 58 * mm]))
    story.append(Paragraph("Two results need explaining, not celebrating:", s["h2"]))
    story.append(bullets(s, [
        "<b>Ground-truth agreement is 100% by construction</b> (same engine wrote "
        "the answers). Its value is as a tripwire: any future drift between SQL, "
        "rules, and the API shows up here first. I caught a real zip-misalignment "
        "bug with it during development.",
        "<b>The LLM-only ablation did not run.</b> All 12 attempts hit free-tier "
        "rate limits, and the harness counts failed calls as excluded rather than "
        "scoring them correct. Re-running it on a quiet tier is the obvious next "
        "experiment — it would put a number on what the rules are worth.",
    ]))
    story.append(Paragraph(
        "The weakest real stage is spend extraction (~10% on monthly_spend): "
        "people say “about 40k” rather than a category breakdown. The system "
        "handles it correctly by asking instead of guessing, which is the right "
        "behaviour, but it means chat takes more turns than the form.", s["body"]))
    return story


def deploy(s, fig_deploy, links):
    story = [Paragraph("8 &nbsp; Deployment", s["h1"])]
    story.append(img(fig_deploy))
    story.append(Paragraph("Figure 5 — every dependency degrades, so a partial "
                           "configuration still serves.", s["caption"]))
    story.append(bullets(s, [
        f"<b>Frontend → Vercel:</b> {links['demo']} (Vite static build, VITE_API_URL "
        "points at the API).",
        f"<b>Backend → Render:</b> {links['api']} (render.yaml blueprint, "
        "Singapore region to sit near Neon).",
        "<b>Catalogue → Neon Postgres.</b> <b>Policy → Qdrant Cloud.</b>",
        "Four secrets total: GROQ_API_KEY, OPENROUTER_API_KEY, DATABASE_URL, "
        "QDRANT_URL + QDRANT_API_KEY. Everything else has a working local default.",
    ]))
    story.append(Paragraph(
        "Local run: cp .env.example .env, make setup, make data, make dev. "
        "161 tests (make test), lint (make lint), evaluation (make eval).",
        s["body"]))
    return story


def limits(s):
    story = [Paragraph("9 &nbsp; Limitations and what I would do next", s["h1"])]
    story.append(bullets(s, [
        "The catalogue is synthetic and Indian-market-flavoured rather than real. "
        "Plugging in a licensed feed would change nothing architecturally — it "
        "lands in the same Card schema.",
        "Latency is dominated by free-tier reasoning models (~25s typical). A "
        "smaller extraction model and streaming the explanation would both help; "
        "I measured the split before deciding neither was worth it for this build.",
        "No auth, rate limiting, or persistence on the API. Chat state lives in "
        "the client by design, which keeps it stateless but means nothing is stored.",
        "The verifier checks text against numbers; it does not judge reasoning. "
        "The optional LLM grounding check exists, is off by default, and can only "
        "add a failure.",
        "Next: re-run the ablation on a quiet tier, add Hindi/Hinglish extraction "
        "tests (the regex handles some; the eval does not measure it), and stream "
        "node completions over SSE so the step-by-step UI shows live progress "
        "instead of paced narration.",
    ]))
    story.append(Paragraph(
        "The core claim of this project is small and checkable: no language model "
        "anywhere in it can approve a card, change a rank, or invent a number "
        "that survives to the customer. Everything else is presentation.",
        s["body"]))
    return story


def appendix_mermaid(s, mermaid):
    story = [Paragraph("Appendix — mermaid sources", s["h1"])]
    story.append(Paragraph(
        "The five figures above were rendered from these sources (matplotlib, "
        "checked into scripts/build_report.py). Included so the diagrams stay "
        "editable.", s["body"]))
    for name, code in mermaid.items():
        story.append(Paragraph(name, s["h2"]))
        story.append(Paragraph(f"<font face=\"Courier\" size=\"8\">{code}</font>",
                               s["mono"]))
    return story
