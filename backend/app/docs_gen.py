"""Policy document generation.

The RAG corpus is rendered *from* policy.yaml. That direction is the whole
point: if the docs were written independently, the vector store could
contradict the rule engine and the system would have two sources of truth.
A test asserts every threshold in these files still matches the YAML.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.policy import Policy, get_policy


def _neutralise_placeholders(text: str) -> str:
    """Replace every {placeholder} with prose.

    Leaving "{actual}" in an indexed document is worse than useless: the RAG
    layer hands it to the model as evidence, and the model either copies the
    braces to the customer or silently invents a value for them.
    """
    return re.sub(r"\{[a-z_]+\}", "the relevant figure", text)


def _money(n: float) -> str:
    """Indian digit grouping. Rupees read wrong at 1200000 otherwise.

    Exact lakh multiples render as "Rs 1 lakh" / "Rs 2 lakh" so tables stay
    short; anything else keeps plain grouped digits, because "Rs 1 lakh 2,000"
    is worse than "Rs 102,000".
    """
    n = int(round(n))
    if n >= 100000 and n % 100000 == 0:
        lakh = n // 100000
        return f"Rs {lakh} lakh"
    return f"Rs {n:,}"


def render_underwriting(policy: Policy) -> str:
    g = policy.global_gate
    lines = [
        "# Bank Sathi underwriting policy",
        "",
        "These are the company-wide rules applied to every application before any",
        "card is considered. Failing any of them means we recommend nothing at all.",
        "",
        "## Hard requirements",
        "",
        f"- Applicants must be between {g['min_age']} and {g['max_age']} years of age.",
        f"- Minimum credit score to be assessed: {g['min_cibil']}.",
        f"- At or below {g['new_to_credit_cibil']}, or with no credit history, the",
        "  customer is treated as new to credit and offered starter cards only.",
        f"- Missed payments in the last 12 months: at most {g['max_missed_payments_12m']}.",
        f"- Credit enquiries in the last 6 months: at most {g['max_recent_inquiries_6m']}.",
        f"- Card usage must be at or below {g['max_utilization_pct']}% of the limit.",
        "",
        "## Why we ask",
        "",
        "Missed payments and heavy revolving usage are the two factors that most",
        "affect a credit score, so an application showing either is assessed on",
        "those first rather than on reward value.",
        "",
        "## Card tiers",
        "",
        "| Tier | Min monthly income | Min credit score | Age range | APR |",
        "|---|---|---|---|---|",
    ]
    for tier in policy.tier_order():
        t = policy.tier(tier)
        cibil = t["min_cibil"] if t["min_cibil"] is not None else "none"
        lines.append(
            f"| {tier} | {_money(t['min_monthly_income'])} | {cibil} | "
            f"{t['min_age']}-{t['max_age']} | {t['apr_pct']}% |"
        )
    lines += [
        "",
        "## Estimated credit limit",
        "",
        "We estimate an indicative limit as a multiple of monthly income:",
        "",
    ]
    for tier in policy.tier_order():
        t = policy.tier(tier)
        lines.append(f"- {tier}: {t['limit_income_multiplier']}x monthly income")
    lines += [
        "",
        "This is an estimate for comparison between cards, not a sanctioned limit.",
        "",
    ]
    return "\n".join(lines)


def render_rejection_reasons(policy: Policy) -> str:
    lines = [
        "# Why a card was not recommended",
        "",
        "When a card is not shown, it is because at least one requirement was not",
        "met. This page lists every reason code, what it means, and what can be done",
        "about it.",
        "",
    ]
    for code, r in policy.reasons.items():
        lines += [
            f"## {r['label']} (`{code}`)",
            "",
            _neutralise_placeholders(str(r["message"])),
            "",
            f"**What you can do:** {_neutralise_placeholders(str(r['improve']))}",
            "",
        ]
    lines += [
        "## Near misses",
        "",
        "A near miss is a card you failed by a small margin. The gap is calculated",
        "in rupees or credit-score points so the distance is concrete.",
        "",
    ]
    nm = policy.near_miss
    lines += [
        f"- A near miss means failing {nm['max_failed_rules']} rule or fewer.",
        f"- Income counts as a near miss from {int(nm['income_slack_ratio'] * 100)}% of the minimum.",
        f"- Credit score counts as a near miss within {nm['cibil_absolute_slack']} points.",
        f"- Age counts as a near miss within {nm['age_slack_years']} years.",
        "",
    ]
    return "\n".join(lines)


def render_rewards(policy: Policy) -> str:
    rv = policy.reward_valuation
    s = policy.scoring
    lines = [
        "# How we value a card",
        "",
        "Every card is reduced to one number: the net value you get in a year.",
        "",
        "## The formula",
        "",
        "```",
        "net annual value = (spend x reward rate, capped per category) x 12",
        "                 + lounge credit (if you want it)",
        "                 - annual fee you would actually pay",
        "```",
        "",
        "## Reward point value",
        "",
        f"One reward point is worth {rv['point_value_rs']} rupees.",
        "",
        "## Lounge",
        "",
        f"A lounge visit is valued at {_money(rv['lounge_visit_value_rs'])}. We only",
        "credit it if you told us you want travel or lounge benefits, and we cap the",
        f"credited amount at {_money(rv['lounge_annual_credit_cap_rs'])} a year.",
        "",
        "## Annual fee",
        "",
    ]
    if s["fee_waived_if_spend_meets_threshold"]:
        lines.append(
            "If your annual card spend reaches the card's published waiver"
        )
        lines.append("threshold, the fee is waived and we score it as zero.")
    lines += [
        "",
        "## Ranking weights",
        "",
        "| Factor | Weight |",
        "|---|---|",
    ]
    for k, v in policy.scoring_weights.items():
        lines.append(f"| {k.replace('_', ' ')} | {int(v * 100)}% |")
    lines += [
        "",
        f"Cards worth less than {_money(policy.min_net_value_rs)} a year are not",
        "shown at all, however well they match your preferences.",
        "",
        f"At most {policy.max_recommendations} cards are shown.",
        "",
    ]
    return "\n".join(lines)


def render_improving(policy: Policy) -> str:
    g = policy.global_gate
    lines = [
        "# Improving your credit profile",
        "",
        "Generic guidance only. Nothing here is specific to your situation, and",
        "taking on new debt to change a score tends to work against you.",
        "",
        "## Pay on time, every time",
        "",
        "Payment history is the heaviest factor in a credit score. One missed",
        "payment can stay on file for years. Set auto-pay for at least the minimum",
        "due amount on every card.",
        "",
        "## Pay the balance in full",
        "",
        f"Revolving a balance means usage stays high. We will not assess an",
        f"application above {g['max_utilization_pct']}% usage. Paying in full each",
        "month keeps usage low and costs you nothing.",
        "",
        "## Space out applications",
        "",
        f"Every application leaves an enquiry on your file. We look for at most",
        f"{g['max_recent_inquiries_6m']} in six months, because several at once",
        "reads as financial stress to lenders.",
        "",
        "## Start small if you have no history",
        "",
        f"With no credit history, or a score at or below {g['new_to_credit_cibil']},",
        "a secured or starter card is the realistic starting point. Roughly a year",
        "of on-time payments is usually enough to move up a tier.",
        "",
        "## What does not work",
        "",
        "- Taking a personal loan to raise your score. It adds a new obligation.",
        "- Closing an old card you use regularly. It shortens your credit history.",
        "- Rapid applications across many banks in one week.",
        "",
    ]
    return "\n".join(lines)


RENDERERS = {
    "underwriting_policy": render_underwriting,
    "reward_valuation": render_rewards,
    "rejection_reasons": render_rejection_reasons,
    "improving_profile": render_improving,
}


def generate_policy_docs(policy: Policy | None = None) -> list[tuple[str, str]]:
    policy = policy or get_policy()
    return [(name, fn(policy)) for name, fn in RENDERERS.items()]


def write_policy_docs(out_dir: Path, policy: Policy | None = None) -> list[Path]:
    policy = policy or get_policy()
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, body in generate_policy_docs(policy):
        p = out_dir / f"{name}.md"
        p.write_text(body)
        written.append(p)
    return written


# ---------------------------------------------------------------------------
# Consistency check
# ---------------------------------------------------------------------------
# Comma grouping is stripped so "Rs 1,500" and "Rs 1500" read the same.
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def doc_numbers(body: str) -> set[str]:
    """Numeric tokens, with comma grouping normalised away."""
    return {m.replace(",", "") for m in _NUM_RE.findall(body)}


def _forms(value) -> set[str]:
    """Every way a number may legitimately appear in the rendered docs.

    Money is printed in lakh grouping, so Rs 100000 shows up as "100000",
    "1 lakh" and "1,00,000"-style fragments. Accepting only the raw digits
    would fail on correct output.
    """
    out = {str(value)}
    if isinstance(value, float):
        # The YAML has 28.0; the doc prints either "28.0" or "28".
        out.add(str(value_of(value)))
    v = value_of(value)
    if isinstance(v, int) and v >= 100000:
        lakh = v // 100000
        rest = v % 100000
        out.add(str(lakh))
        if rest == 0:
            out.add(f"{lakh} lakh")
            out.add(f"{lakh * 100000:,}")
        else:
            out.add(f"{lakh * 100000 + rest:,}")
    return out


def assert_docs_consistent(docs: dict[str, str], policy: Policy | None = None) -> None:
    """Every threshold that appears in policy.yaml must appear in some doc.

    This is deliberately a subset check rather than a full text diff: the docs
    add prose, so requiring equality would make them impossible to write. What
    we are guarding is the specific failure mode where the YAML changes and a
    doc quietly keeps quoting the old number.
    """
    policy = policy or get_policy()
    corpus = doc_numbers("\n".join(docs.values()))

    must_appear: list[tuple[str, str]] = [
        ("global_gate.min_age", policy.min_age),
        ("global_gate.max_age", policy.max_age),
        ("global_gate.min_cibil", policy.min_cibil),
        ("global_gate.new_to_credit_cibil", policy.new_to_credit_cibil),
        ("global_gate.max_missed_payments_12m", policy.max_missed_payments_12m),
        ("global_gate.max_recent_inquiries_6m", policy.max_recent_inquiries_6m),
        ("global_gate.max_utilization_pct", policy.max_utilization_pct),
        ("near_miss.income_slack_ratio_pct", int(policy.nm_income_slack_ratio * 100)),
        ("near_miss.cibil_absolute_slack", policy.nm_cibil_absolute_slack),
        ("near_miss.age_slack_years", policy.nm_age_slack_years),
        ("near_miss.max_failed_rules", policy.nm_max_failed_rules),
        ("scoring.max_recommendations", policy.max_recommendations),
        ("reward_valuation.lounge_visit_value_rs", int(policy.lounge_visit_value_rs)),
        ("reward_valuation.lounge_annual_credit_cap_rs", int(policy.lounge_annual_credit_cap_rs)),
        ("reward_valuation.point_value_rs", policy.point_value_rs),
    ]
    for tier in policy.tier_order():
        t = policy.tier(tier)
        must_appear.append((f"tiers.{tier}.min_monthly_income", int(t["min_monthly_income"])))
        must_appear.append((f"tiers.{tier}.apr_pct", t["apr_pct"]))
        must_appear.append((f"tiers.{tier}.min_age", int(t["min_age"])))
        must_appear.append((f"tiers.{tier}.max_age", int(t["max_age"])))
    for key, value in policy.scoring_weights.items():
        must_appear.append((f"scoring.weights.{key}", int(value * 100)))

    missing = [f"{k}={v}" for k, v in must_appear if not (_forms(v) & corpus)]
    if missing:
        raise AssertionError(
            "policy.yaml changed but the generated docs do not mention: "
            + ", ".join(sorted(missing))
        )


def value_of(v):
    """Render like the docs do: ints without .0, floats as-is."""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def manifest(paths: list[Path]) -> str:
    return json.dumps([p.name for p in paths], indent=2)
