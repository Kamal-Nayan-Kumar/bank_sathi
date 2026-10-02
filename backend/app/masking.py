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
PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"), "[PAN]", "pan"),
    (re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\b"), "[AADHAAR]", "aadhaar"),
    (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), "[PAN]", "pan_alpha"),
    (
        re.compile(r"\b(?:\+91[-\s]?)?[6-9]\d{9}\b"),
        "[PHONE]",
        "phone",
    ),
    (
        re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
        "[EMAIL]",
        "email",
    ),
    (
        re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{2}\b"),
        "[ACCOUNT]",
        "account",
    ),
    # CVV / expiry fragments next to a card word.
    (re.compile(r"(?i)\b(?:cvv|cvc)\s*(?:is|:)?\s*\d{3,4}\b"), "CVV [REDACTED]", "cvv"),
    (
        re.compile(r"(?i)\b(?:expiry|expires|valid till)\s*(?:is|:)?\s*\d{2}[/-]\d{2,4}\b"),
        "EXPIRY [REDACTED]",
        "expiry",
    ),
]

_EMAIL_LIKE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")


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
