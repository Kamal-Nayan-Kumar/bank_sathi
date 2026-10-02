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
from dataclasses import dataclass, field

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

# Two attempts with backoff. A free-tier 429 that survives this degrades to the
# template path, which is correct behaviour, not a failure.
LLM_MAX_ATTEMPTS = 2
LLM_RETRY_BASE_S = 2.0

_USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}


def usage() -> dict[str, int]:
    return dict(_USAGE)


def reset_usage() -> None:
    _USAGE.update(prompt_tokens=0, completion_tokens=0, calls=0)


@dataclass
class LLMResult:
    text: str = ""
    json_value: dict | None = None
    used_llm: bool = True
    error: str | None = None
    model: str = ""


def available() -> bool:
    return get_settings().has_llm


_REASONING_MODELS = {"openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"}


def chat_multi(messages: list[dict], model: str | None = None) -> LLMResult:
    """Multi-turn call. Used by the chat tool loop, which needs to feed a tool
    result back in as a further turn."""
    s = get_settings()
    if not s.has_llm:
        return LLMResult(text="", used_llm=False, error="no_api_key")
    use_model = model or s.groq_model_explain
    last_error = ""
    for attempt in range(LLM_MAX_ATTEMPTS):
        try:
            text = _post(messages, use_model, json_mode=False)
            return LLMResult(text=text, model=use_model)
        except httpx.HTTPStatusError as exc:
            last_error = f"HTTP {exc.response.status_code}"
            if exc.response.status_code != 429 or attempt == LLM_MAX_ATTEMPTS - 1:
                break
            time.sleep(LLM_RETRY_BASE_S * (2**attempt))
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            break
    log.warning("LLM multi-turn failed (%s)", last_error)
    return LLMResult(text="", used_llm=False, error=last_error, model=use_model)


def _post(messages: list[dict], model: str, json_mode: bool = False, temperature: float = 0.1):
    s = get_settings()
    payload: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": 1400,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
        # Some Groq models emit a `reasoning` field and reject an explicit
        # temperature; sending neither keeps every available model working.
    elif model not in _REASONING_MODELS:
        payload["temperature"] = temperature
    with httpx.Client(timeout=s.llm_timeout_s) as client:
        r = client.post(
            f"{s.groq_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {s.groq_api_key}"},
            json=payload,
        )
        r.raise_for_status()
        data = r.json()
    usage_meta = data.get("usage", {})
    _USAGE["prompt_tokens"] += int(usage_meta.get("prompt_tokens", 0))
    _USAGE["completion_tokens"] += int(usage_meta.get("completion_tokens", 0))
    _USAGE["calls"] += 1
    # Reasoning models put their thinking in a separate field; the answer is
    # still in `content`, but a truncated answer shows up as an empty content
    # string, so record why in case it matters upstream.
    message = data["choices"][0]["message"]
    text = (message.get("content") or "").strip()
    if not text and message.get("reasoning"):
        log.info("model returned reasoning but no content (likely hit max_tokens)")
    return text


def chat(
    system: str, user: str, *, model: str | None = None, json_mode: bool = False
) -> LLMResult:
    """One call, with bounded retry on 429 only.

    Retrying is limited to rate limiting and is capped at two attempts with a
    backoff. A 429 here means the shared free tier is saturated; retrying a
    400 or a 500 just wastes the customer's wait. After the retries are
    exhausted the caller gets `used_llm=False` and degrades, which is the
    behaviour every call site already handles.
    """
    s = get_settings()
    if not s.has_llm:
        return LLMResult(text="", used_llm=False, error="no_api_key")
    use_model = model or s.groq_model_explain

    last_error = ""
    for attempt in range(LLM_MAX_ATTEMPTS):
        try:
            text = _post(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                use_model,
                json_mode=json_mode,
            )
            parsed = _parse_json(text) if json_mode else None
            return LLMResult(text=text, json_value=parsed, model=use_model)
        except httpx.HTTPStatusError as exc:
            last_error = f"HTTP {exc.response.status_code}"
            if exc.response.status_code != 429:
                break
            if attempt < LLM_MAX_ATTEMPTS - 1:
                time.sleep(LLM_RETRY_BASE_S * (2**attempt))
                continue
        except Exception as exc:  # noqa: BLE001 - any failure must degrade, not 500
            last_error = str(exc)
            break

    log.warning("LLM call failed (%s) for model %s", last_error, use_model)
    return LLMResult(text="", used_llm=False, error=last_error, model=use_model)


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
