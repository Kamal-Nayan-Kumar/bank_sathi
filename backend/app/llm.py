"""LLM access.

Two hard rules are enforced here rather than left to prompt wording:

1. **The model never receives a tool that can change a decision.** The only
   tools exposed are read-only lookups.
2. **Every call degrades.** With no API key the intake falls back to a regex
   extractor and the explainer falls back to a template, so the pipeline always
   produces a response. The response records which path ran.

Responses are logged with token counts because "how much does the LLM cost per
recommendation" is one of the questions this project has to answer with a
number.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

import httpx
from app.config import get_settings

log = logging.getLogger(__name__)

# One retry, and only for transient server faults. Rate limits fail over
# immediately (see `_should_fail_over`): measured end to end, retrying a
# saturated free tier added ~40s to a 56s request without a single success.
LLM_MAX_ATTEMPTS = 2
LLM_RETRY_BASE_S = 1.0

# Generous because reasoning models (gpt-oss-120b, the Nemotron fallbacks) emit
# a `reasoning` field that consumes the same budget as the answer. Measured: a
# Nemotron explanation returned 446 completion tokens of which roughly 400 were
# thinking, and at 500 tokens the visible answer was cut mid-sentence.
MAX_TOKENS = 2048

_USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "fallbacks": 0}


def usage() -> dict[str, int]:
    return dict(_USAGE)


def reset_usage() -> None:
    _USAGE.update(prompt_tokens=0, completion_tokens=0, calls=0, fallbacks=0)


@dataclass
class LLMResult:
    text: str = ""
    json_value: dict | None = None
    used_llm: bool = True
    error: str | None = None
    model: str = ""


def available() -> bool:
    return get_settings().has_llm


def providers() -> list[tuple[str, str, str]]:
    """Configured providers as (name, base_url, api_key), primary first.

    Order matters: the primary carries the model choice and the retry budget,
    and a fallback only gets what the primary left behind. Groq's free tier
    rate-limits aggressively, so "Groq is 429, try the other one" is a normal
    production path here rather than an edge case.
    """
    s = get_settings()
    out: list[tuple[str, str, str]] = []
    if s.groq_api_key:
        out.append(("groq", s.groq_base_url, s.groq_api_key))
    if s.openrouter_api_key:
        out.append(("openrouter", s.openrouter_base_url, s.openrouter_api_key))
    return out


def _model_for(provider: str, role: str, settings) -> str:
    """Resolve the model name, remapping if the fallback lacks the primary's."""
    if provider == "groq":
        return settings.groq_model_extract if role == "extract" else settings.groq_model_explain
    return (
        settings.openrouter_model_extract
        if role == "extract"
        else settings.openrouter_model_explain
    )


_REASONING_MODELS = {"openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"}


def chat_multi(messages: list[dict], model: str | None = None) -> LLMResult:
    """Multi-turn call. Used by the chat tool loop, which needs to feed a tool
    result back in as a further turn."""
    return _dispatch(messages, role="explain", json_mode=False, forced_model=model)


def chat(
    system: str, user: str, *, model: str | None = None, json_mode: bool = False
) -> LLMResult:
    """One call, with bounded retry, then failover.

    Retry is limited to rate limiting and capped; after that the next provider
    is tried. Every call site already handles `used_llm=False` by degrading, so
    a total outage produces a correct but plainer answer rather than an error.
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    forced = model if model and model in (
        get_settings().groq_model_extract,
        get_settings().groq_model_explain,
    ) else None
    return _dispatch(messages, role="explain", json_mode=json_mode, forced_model=forced)


def _dispatch(
    messages: list[dict],
    *,
    role: str,
    json_mode: bool,
    forced_model: str | None = None,
) -> LLMResult:
    settings = get_settings()
    if not settings.has_llm:
        return LLMResult(used_llm=False, error="no_api_key")

    last_error = ""
    for name, base_url, api_key in providers():
        use_model = forced_model or _model_for(name, role, settings)
        for attempt in range(LLM_MAX_ATTEMPTS):
            try:
                text = _post(
                    messages,
                    use_model,
                    base_url=base_url,
                    api_key=api_key,
                    provider=name,
                    json_mode=json_mode,
                )
                parsed = _parse_json(text) if json_mode else None
                _USAGE["calls"] += 1
                if name != "groq":
                    _USAGE["fallbacks"] += 1
                return LLMResult(text=text, json_value=parsed, model=use_model)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                last_error = f"{name}: HTTP {status}"
                if _should_fail_over(status) or attempt == LLM_MAX_ATTEMPTS - 1:
                    break
                time.sleep(LLM_RETRY_BASE_S * (2**attempt))
            except Exception as exc:  # noqa: BLE001
                last_error = f"{name}: {exc}"
                break
        log.info("provider %s exhausted (%s)", name, last_error)

    log.warning("all LLM providers failed: %s", last_error)
    return LLMResult(used_llm=False, error=last_error)


def _should_fail_over(status: int) -> bool:
    """Whether to abandon this provider immediately rather than retry it.

    429 is the interesting case. Both providers here are free tiers over shared
    capacity, so retrying the same one after a rate limit competes with the same
    saturated pool and adds the backoff straight to the customer's wait. A 5xx
    is different: the provider is reachable and the fault may be transient, so
    one retry is worth it.
    """
    if status == 429:
        return True
    if 400 <= status < 500:
        return True  # bad request, bad key, no access: retrying cannot help
    return status < 500


def _post(
    messages: list[dict],
    model: str,
    *,
    base_url: str,
    api_key: str,
    provider: str,
    json_mode: bool = False,
    temperature: float = 0.1,
) -> str:
    """One HTTP call to an OpenAI-compatible endpoint.

    Groq and OpenRouter differ only in headers, so there is one implementation.
    """
    s = get_settings()
    payload: dict = {"model": model, "messages": messages, "max_tokens": MAX_TOKENS}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    elif model not in _REASONING_MODELS:
        payload["temperature"] = temperature

    headers = {"Authorization": f"Bearer {api_key}"}
    if provider == "openrouter":
        # OpenRouter routes on these headers for attribution and for its
        # own free-tier fairness accounting. Missing them is not an error, but
        # being a good citizen costs nothing.
        headers["HTTP-Referer"] = "https://cardsahi.vercel.app"
        headers["X-Title"] = "Card Sathi"

    with httpx.Client(timeout=s.llm_timeout_s) as client:
        r = client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json=payload,
        )
        r.raise_for_status()
        data = r.json()

    usage_meta = data.get("usage", {})
    _USAGE["prompt_tokens"] += int(usage_meta.get("prompt_tokens", 0))
    _USAGE["completion_tokens"] += int(usage_meta.get("completion_tokens", 0))

    # Reasoning models put their thinking in a separate field; the answer is
    # still in `content`, but a truncated answer shows up as an empty string.
    message = data["choices"][0]["message"]
    text = (message.get("content") or "").strip()
    if not text and message.get("reasoning"):
        log.info("%s/%s returned reasoning but no content (likely hit max_tokens)", provider, model)
    return text


def _parse_json(text: str) -> dict | None:
    """Tolerate the fenced-code wrapping small models add around JSON."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None
