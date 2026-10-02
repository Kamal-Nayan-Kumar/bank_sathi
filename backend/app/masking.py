"""PII handling.

Customers type real details into a chat box. Anything that identifies them is
stripped before it reaches the LLM, because the LLM is a third-party API and we
do not send it a customer's PAN to decide which card they get.

Order matters: the stricter patterns run first, since a PAN is partly digits
and would otherwise be partially eaten by the phone-number rule.
"""

from __future__ import annotations

import re

# Each pattern is (compiled regex, replacement, label).
#
# Order and anchoring both matter here. A 16-digit card number and a 12-digit
# Aadhaar number are both "groups of four digits", so:
#   * the longest form is tried first, and
#   * every digit run asserts `(?<!\d)` / `(?!\d)` at its edges.
# Without the adjacency guards a card number is silently matched as an Aadhaar
# number and rewritten with the wrong label, which looks like it worked.
PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # 19-digit account number, then 16-digit card, then 12-digit Aadhaar.
    (re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{3}(?!\d)"), "[ACCOUNT]", "account"),
    (re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)"), "[PAN]", "pan"),
    (re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)"), "[AADHAAR]", "aadhaar"),
    (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), "[PAN]", "pan_alpha"),
    # Indian mobile numbers: +91 optional, and people write them grouped
    # ("98765 43210") as often as solid.
    (
        re.compile(
            r"(?<![\d+])(?:\+91[-\s]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)"
        ),
        "[PHONE]",
        "phone",
    ),
    (re.compile(r"(?<![\d+])\+91[\s-]?\d[\s-]?\d{5}[\s-]?\d{5}(?!\d)"), "[PHONE]", "phone"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "[EMAIL]", "email"),
    # CVV / expiry, which only make sense next to the word naming them.
    (re.compile(r"(?i)\b(?:cvv|cvc)\s*(?:is|:)?\s*\d{3,4}\b"), "CVV [REDACTED]", "cvv"),
    (
        re.compile(r"(?i)\b(?:expiry|expires|valid till)\s*(?:is|:)?\s*\d{2}[/-]\d{2,4}\b"),
        "EXPIRY [REDACTED]",
        "expiry",
    ),
]


def mask(text: str) -> tuple[str, list[str]]:
    """Return (masked_text, labels_of_what_was_found).

    The label list is returned rather than logged so the caller can decide
    whether to surface a warning; it never goes back into a model prompt.
    """
    found: list[str] = []
    out = text
    for pattern, replacement, label in PATTERNS:
        if pattern.search(out):
            found.append(label)
            out = pattern.sub(replacement, out)
    return out, sorted(set(found))


def contains_pii(text: str) -> bool:
    return bool(mask(text)[1])


def is_clean(text: str) -> bool:
    """Strict check for output: the verifier uses this on generated prose."""
    _, found = mask(text)
    return not found
