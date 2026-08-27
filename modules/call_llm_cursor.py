"""
Cursor SDK chat-only LLM calls for Gen2 agent roles.
"""

from __future__ import annotations

import os

from cfg import CFG
from cursor_model_selection import (
    apply_cursor_runtime_params,
    build_cursor_model_value,
    normalize_cursor_params,
    parse_model_selection,
)
from model_config import normalize_effort
from safe_path import safe_path

MODULE_METADATA = {
    "name": "call_llm_cursor",
    "type": "function",
    "description": "Call Cursor SDK in isolated text-only mode (no built-in tools) and return relay-compatible response dict.",
    "functions": [
        {
            "name": "call_llm_cursor",
            "inputs": {
                "messages": "list[dict[str, str]]",
                "model": "str Cursor model id",
                "thinking": "str effort level",
                "max_tokens": "int",
                "timeout": "int | None",
            },
            "outputs": "dict with content/text fields",
        }
    ],
}


def _messages_to_prompt(messages) -> str:
    parts = []
    for item in messages or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user").strip() or "user"
        content = str(item.get("content") or "")
        parts.append(f"<{role}>\n{content}\n</{role}>")
    return "\n\n".join(parts).strip()


def _extract_cursor_text(result) -> str:
    if result is None:
        return ""

    if isinstance(result, str):
        return result

    for attr in ("result", "text", "output", "content"):
        value = getattr(result, attr, None)
        if isinstance(value, str) and value.strip():
            return value

    if isinstance(result, dict):
        for key in ("result", "text", "output", "content"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value

    return str(result)


def _is_cursor_param_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    if not text:
        return False

    needles = (
        "parameter",
        "model param",
        "invalid param",
        "unknown param",
        "unsupported param",
        "fast",
    )
    return any(needle in text for needle in needles)


def _cursor_model_attempts(model_id: str, cursor_params=None):
    runtime_params = apply_cursor_runtime_params(model_id, cursor_params)
    stripped_params = [
        item
        for item in normalize_cursor_params(cursor_params)
        if str(item.get("id") or "").strip().lower() != "fast"
    ]

    attempts = []
    seen = set()

    for params in (runtime_params, stripped_params, []):
        key = tuple((item.get("id"), item.get("value")) for item in params)
        if key in seen:
            continue
        seen.add(key)
        attempts.append(params)

    return attempts


def _build_cursor_model(model, cursor_params=None, *, force_params=None):
    selection = parse_model_selection(model, cursor_params)
    model_id = selection["id"]

    if force_params is not None:
        params = normalize_cursor_params(force_params)
        if not params:
            return model_id, []

        try:
            from cursor_sdk import ModelParameterValue, ModelSelection
        except ImportError as exc:
            raise RuntimeError(
                "cursor-sdk is not installed. Install with: pip install cursor-sdk"
            ) from exc

        return (
            ModelSelection(
                id=model_id,
                params=[
                    ModelParameterValue(id=item["id"], value=item["value"])
                    for item in params
                ],
            ),
            params,
        )

    params = apply_cursor_runtime_params(model_id, selection.get("params"))
    return build_cursor_model_value(model_id, selection.get("params")), params


def _run_cursor_prompt(prompt, model_value, api_key, cwd):
    from cursor_sdk import Agent, AgentOptions, LocalAgentOptions

    local_opts = LocalAgentOptions(
        cwd=cwd,
        setting_sources=[],
    )

    options = AgentOptions(
        api_key=api_key,
        model=model_value,
        local=local_opts,
        # Omitting tools exposes Cursor's full agent toolset (read/edit/shell/...).
        # Gen2 expects plain text/JSON from our own prompts, not native tool calls.
        tools=[],
    )

    return Agent.prompt(prompt, options)


def call_llm_cursor(
    messages,
    model,
    thinking="medium",
    max_tokens=8192,
    timeout=None,
    cursor_params=None,
):
    api_key = os.environ.get("CURSOR_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("CURSOR_API_KEY environment variable is not set")

    prompt = _messages_to_prompt(messages)
    if not prompt:
        raise ValueError("messages must contain at least one non-empty prompt")

    cwd = safe_path(".")
    selection = parse_model_selection(model, cursor_params)
    model_id = selection["id"]
    effort = normalize_effort(thinking)

    result = None
    used_params = apply_cursor_runtime_params(model_id, selection.get("params"))
    errors = []

    for params in _cursor_model_attempts(model_id, selection.get("params")):
        try:
            model_value, used_params = _build_cursor_model(
                model_id,
                selection.get("params"),
                force_params=params,
            )
            result = _run_cursor_prompt(prompt, model_value, api_key, cwd)
            break
        except Exception as exc:
            errors.append(f"params={params or 'none'}: {exc}")
            if params and _is_cursor_param_error(exc):
                continue
            raise

    if result is None:
        raise RuntimeError(
            "Cursor SDK run failed for all model parameter attempts: "
            + " | ".join(errors)
        )

    if getattr(result, "status", None) == "error":
        raise RuntimeError(f"Cursor SDK run failed: {_extract_cursor_text(result)}")

    text = _extract_cursor_text(result)
    return {
        "content": text,
        "source": "cursor",
        "model": model_id,
        "cursor_params": used_params,
    }


if __name__ == "__main__":
    assert _messages_to_prompt(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    ).startswith("<system>")
    assert apply_cursor_runtime_params(
        "composer-2.5",
        [{"id": "fast", "value": "true"}],
    ) == [{"id": "fast", "value": "false"}]
    attempts = _cursor_model_attempts(
        "grok-4.5",
        [{"id": "fast", "value": "true"}],
    )
    assert attempts[0] == [{"id": "fast", "value": "false"}]
    assert attempts[-1] == []
    print("CALL_LLM_CURSOR SELF TEST PASSED")
