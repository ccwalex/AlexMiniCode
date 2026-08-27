"""
Model-specific LLM fallback chains for transient Cursor availability failures.

When a configured Cursor model is unavailable due to demand, quota, or similar
transient errors, call_llm_role can walk a predefined fallback chain before
giving up.

Cursor model speed/effort presets are passed via cursor_params, not baked into
the model id. Update the chain below to match Cursor.models.list() on your
account.
"""

from __future__ import annotations

import json
import re

from cursor_model_selection import normalize_cursor_params

MODULE_METADATA = {
    "name": "llm_fallback",
    "type": "function",
    "description": "Resolve model fallback chains and detect retryable LLM failures.",
    "functions": [
        {
            "name": "build_fallback_chain",
            "inputs": {
                "source": "str",
                "model": "str | None",
                "cursor_params": "list | None",
            },
            "outputs": "list[dict] ordered attempts with source, model, and cursor_params",
        },
        {
            "name": "is_retryable_llm_error",
            "inputs": {"error": "Exception | str | None"},
            "outputs": "bool",
        },
    ],
}

MODEL_ALIASES = {
    "composer 2.5": "composer-2.5",
    "5.3-codex": "5.3-codex",
    "gemini-3.5-flash": "5.3-codex",
}

# Ordered fallback chains keyed by canonical primary model id.
# Each entry may include cursor_params for preset variants such as fast=true.
FALLBACK_CHAINS: dict[str, list[dict[str, object]]] = {
    "composer-2.5": [
        {"source": "cursor", "model": "composer-2.5", "cursor_params": []},
        # Replace the grok ids/params below with values from Cursor.models.list().
        {"source": "cursor", "model": "grok-4.5", "cursor_params": []},
        {"source": "cursor", "model": "grok-4.6", "cursor_params": []},
        {"source": "relay", "model": "5.3-codex", "cursor_params": []},
    ],
}

_RETRYABLE_PATTERNS = (
    r"quota",
    r"rate[\s_-]?limit",
    r"too many requests",
    r"capacity",
    r"demand",
    r"overloaded",
    r"unavailable",
    r"not available",
    r"temporarily",
    r"try again",
    r"resource[\s_-]?exhausted",
    r"server busy",
    r"503",
    r"429",
    r"502",
    r"504",
)

_NON_RETRYABLE_PATTERNS = (
    r"invalid api key",
    r"authentication",
    r"unauthorized",
    r"forbidden",
    r"permission denied",
    r"messages must contain",
    r"invalid prompt",
    r"bad request",
    r"malformed",
    r"json parse",
)


def canonical_model_id(model) -> str:
    name = str(model or "").strip()
    if not name:
        return ""
    lowered = name.lower()
    if lowered in MODEL_ALIASES:
        return MODEL_ALIASES[lowered]
    return name


def _selection_key(model, cursor_params=None) -> str:
    return json.dumps(
        {
            "model": canonical_model_id(model),
            "cursor_params": normalize_cursor_params(cursor_params),
        },
        sort_keys=True,
    )


def build_fallback_chain(source: str, model, cursor_params=None) -> list[dict[str, object]]:
    """
    Return ordered LLM attempts for the requested source/model pair.

    Non-cursor sources, unknown models, and models without a configured chain
    return a single direct attempt.
    """
    source = str(source or "relay").strip().lower()
    model_id = canonical_model_id(model)
    params = normalize_cursor_params(cursor_params)

    if source != "cursor" or not model_id:
        return [{"source": source or "relay", "model": model_id or str(model or "").strip(), "cursor_params": params}]

    chain = FALLBACK_CHAINS.get(model_id)
    if not chain:
        return [{"source": "cursor", "model": model_id, "cursor_params": params}]

    primary_key = _selection_key(model_id, params)
    resolved = []
    seen = set()

    for item in chain:
        attempt = {
            "source": str(item.get("source") or "cursor").strip().lower(),
            "model": canonical_model_id(item.get("model")),
            "cursor_params": normalize_cursor_params(item.get("cursor_params")),
        }
        key = _selection_key(attempt["model"], attempt["cursor_params"])
        if key in seen:
            continue
        seen.add(key)
        resolved.append(attempt)

    if primary_key not in seen:
        resolved.insert(
            0,
            {"source": "cursor", "model": model_id, "cursor_params": params},
        )

    return resolved


def is_retryable_llm_error(error) -> bool:
    """
    True for transient availability / quota style failures that should trigger
    the next fallback model. False for auth, validation, and other hard errors.
    """
    if error is None:
        return False

    text = str(error).strip().lower()
    if not text:
        return False

    for pattern in _NON_RETRYABLE_PATTERNS:
        if re.search(pattern, text):
            return False

    for pattern in _RETRYABLE_PATTERNS:
        if re.search(pattern, text):
            return True

    return False


def format_fallback_attempt(attempt: dict) -> str:
    source = str(attempt.get("source") or "").strip()
    model = str(attempt.get("model") or "").strip()
    params = normalize_cursor_params(attempt.get("cursor_params"))
    if not params:
        return f"{source}:{model}"

    bits = []
    for item in params:
        param_id = item.get("id") or ""
        value = item.get("value") or ""
        if param_id and value:
            bits.append(f"{param_id}={value}")
        elif param_id:
            bits.append(param_id)
    return f"{source}:{model} ({', '.join(bits)})"


if __name__ == "__main__":
    chain = build_fallback_chain("cursor", "composer-2.5")
    assert len(chain) >= 4
    assert chain[0]["model"] == "composer-2.5"
    assert chain[-1] == {"source": "relay", "model": "5.3-codex", "cursor_params": []}

    custom = build_fallback_chain(
        "cursor",
        "composer-2.5",
        cursor_params=[{"id": "fast", "value": "true"}],
    )
    assert custom[0]["cursor_params"] == [{"id": "fast", "value": "true"}]

    relay_only = build_fallback_chain("relay", "mini")
    assert relay_only == [{"source": "relay", "model": "mini", "cursor_params": []}]

    assert is_retryable_llm_error("model unavailable due to high demand") is True
    assert is_retryable_llm_error("invalid api key") is False

    print("LLM_FALLBACK SELF TEST PASSED")
