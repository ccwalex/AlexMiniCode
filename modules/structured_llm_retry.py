"""
Retry structured LLM outputs: same-model repeat, then a user-selected fallback model.

Used when a planner/debug call returns no API-call list, or a verifier call
returns no JSON decision. This is separate from transport/quota fallbacks in
llm_fallback.py.
"""

from __future__ import annotations

import json

MODULE_METADATA = {
    "name": "structured_llm_retry",
    "type": "function",
    "description": "Repeat an LLM call once if structured output is missing, then route to a configured fallback model.",
    "functions": [
        {
            "name": "call_llm_role_with_parse_retry",
            "inputs": {
                "role": "str LLM role",
                "messages": "list of chat messages",
                "is_valid": "callable(raw) -> bool",
                "parse_fallback_kind": "str execution or verifier",
            },
            "outputs": "raw LLM response after at most primary, repeat, and fallback attempts",
        }
    ],
}


def is_valid_api_plan(raw) -> bool:
    from parse_api_plan import parse_api_plan

    parsed = parse_api_plan(raw)
    return bool(isinstance(parsed, dict) and parsed.get("success") and parsed.get("calls"))


def _unwrap_text(raw):
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, dict):
        return None
    for key in ("content", "text", "output", "response"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value
    message = raw.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message.get("content")
    return None


def _extract_json_obj(raw):
    if isinstance(raw, dict) and "approved" in raw:
        return raw

    text = _unwrap_text(raw)
    if not isinstance(text, str) or not text.strip():
        if isinstance(raw, dict):
            text = json.dumps(raw)
        else:
            return None

    text = text.strip()
    if text.startswith("```"):
        text = text.replace("```json", "").replace("```", "").strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return None


def is_valid_verifier_response(raw) -> bool:
    parsed = _extract_json_obj(raw)
    return isinstance(parsed, dict) and "approved" in parsed


def _fallback_overrides(kind: str) -> dict:
    from model_config import get_parse_fallback, normalize_effort

    entry = get_parse_fallback(kind)
    if not entry:
        return {}

    overrides = {
        "source": entry.get("source"),
        "model": entry.get("model"),
        "thinking": normalize_effort(entry.get("effort")),
        "max_tokens": entry.get("max_tokens"),
    }
    if entry.get("cursor_params"):
        overrides["cursor_params"] = entry.get("cursor_params")
    return {key: value for key, value in overrides.items() if value is not None}


def call_llm_role_with_parse_retry(
    role: str,
    messages: list[dict[str, str]],
    is_valid,
    parse_fallback_kind: str,
    llm_call=None,
    **call_kwargs,
):
    """
    Call the LLM up to three times for missing structured output:

    1. primary model
    2. same model, once
    3. user-selected parse fallback model from model_config
    """
    if llm_call is None:
        from call_llm import call_llm_role as llm_call

    def _attempt(extra=None):
        kwargs = dict(call_kwargs)
        if extra:
            kwargs.update(extra)
        return llm_call(role=role, messages=messages, **kwargs)

    # Quiet on success: only log when structured output is missing.
    raw = _attempt()
    if is_valid(raw):
        return raw

    print(
        f"[Structured Retry] {role}: missing structured output "
        f"(expected API list or verifier JSON); repeating same model once"
    )
    raw = _attempt()
    if is_valid(raw):
        print(f"[Structured Retry] {role}: repeat succeeded")
        return raw

    extra = _fallback_overrides(parse_fallback_kind)
    if not extra.get("model"):
        print(f"[Structured Retry] {role}: no parse fallback model configured; giving up")
        return raw

    label = f"{extra.get('source')}:{extra.get('model')}"
    print(f"[Structured Retry] {role}: repeat still bad; routing to parse fallback {label}")
    raw = _attempt(extra)
    if is_valid(raw):
        print(f"[Structured Retry] {role}: parse fallback {label} succeeded")
    else:
        print(f"[Structured Retry] {role}: parse fallback {label} also missing structured output")
    return raw


if __name__ == "__main__":
    calls = {"n": 0}

    def fake_llm(**kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            return "not a list"
        return [{"url": "/done", "payload": {"summary": "ok"}}]

    def fake_valid(raw):
        return isinstance(raw, list)

    result = call_llm_role_with_parse_retry(
        role="main_planner",
        messages=[{"role": "user", "content": "x"}],
        is_valid=fake_valid,
        parse_fallback_kind="execution",
        llm_call=lambda **kwargs: fake_llm(**kwargs),
    )
    assert result[0]["url"] == "/done"
    assert calls["n"] == 2

    fallback_calls = {"n": 0, "models": []}

    def always_invalid(_raw):
        return False

    def tracking_llm(**kwargs):
        fallback_calls["n"] += 1
        fallback_calls["models"].append(kwargs.get("model"))
        return "still invalid"

    call_llm_role_with_parse_retry(
        role="verifier",
        messages=[{"role": "user", "content": "x"}],
        is_valid=always_invalid,
        parse_fallback_kind="verifier",
        llm_call=lambda **kwargs: tracking_llm(**kwargs),
        model="primary-model",
    )
    assert fallback_calls["n"] == 3
    assert fallback_calls["models"][0] == "primary-model"
    assert fallback_calls["models"][1] == "primary-model"
    assert fallback_calls["models"][2] not in (None, "")

    print("STRUCTURED_LLM_RETRY SELF TEST PASSED")
