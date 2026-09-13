"""
OpenCode Go LLM calls for Gen2 agent roles.
"""

from __future__ import annotations

import json
import os

from cfg import CFG
from model_config import normalize_effort
from opencode_registry import (
    DEFAULT_OPENCODE_MODEL,
    OPENCODE_GO_BASE,
    normalize_model_id,
    resolve_endpoint_path,
    resolve_transport,
)
from opencode_session import get_opencode_session, sanitize_session_id

MODULE_METADATA = {
    "name": "call_llm_opencode",
    "type": "function",
    "description": "Call OpenCode Go via chat/completions, responses, or messages transports.",
    "functions": [
        {
            "name": "call_llm_opencode",
            "inputs": {
                "messages": "list[dict[str, str]]",
                "model": "str OpenCode model id",
                "thinking": "str effort level",
                "max_tokens": "int",
                "timeout": "int | None",
                "session_id": "str | None",
            },
            "outputs": "dict with content/text fields",
        }
    ],
}


def _get_api_key() -> str:
    for env_name in ("OPENCODE_API_KEY", "OPENCODE_ZEN_API_KEY"):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    raise RuntimeError(
        "OPENCODE_API_KEY environment variable is not set "
        "(OPENCODE_ZEN_API_KEY is also accepted)"
    )


def _agent_version() -> str:
    return str(getattr(CFG, "AGENT_VERSION", "2.0"))


def build_opencode_headers(session_id=None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type": "application/json",
        "User-Agent": f"gen2-agent/{_agent_version()}",
        "x-opencode-session": sanitize_session_id(session_id or get_opencode_session()),
    }


def _normalize_messages(messages) -> list[dict[str, str]]:
    out = []
    for item in messages or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "user").strip() or "user"
        content = str(item.get("content") or "")
        out.append({"role": role, "content": content})
    return out


def _apply_reasoning(payload: dict, thinking: str, reasoning_options) -> None:
    effort = normalize_effort(thinking)
    if not isinstance(reasoning_options, list):
        return

    for option in reasoning_options:
        if not isinstance(option, dict):
            continue
        option_type = str(option.get("type") or "").strip().lower()
        if option_type == "effort":
            values = option.get("values") or []
            if effort in values:
                payload["reasoning"] = {"effort": effort}
            elif effort == "medium" and "low" in values:
                payload["reasoning"] = {"effort": "low"}
            return
        if option_type == "toggle" and effort in ("medium", "high"):
            payload["reasoning"] = {"enabled": True}
            return


def _cache_control_block() -> dict:
    return {"type": "ephemeral"}


def _build_messages_payload(model_id: str, messages, max_tokens: int, thinking: str, api_entry) -> dict:
    system_blocks = []
    convo = []

    for item in messages:
        role = item["role"]
        content = item["content"]
        if role == "system":
            system_blocks.append(
                {
                    "type": "text",
                    "text": content,
                    "cache_control": _cache_control_block(),
                }
            )
        else:
            convo.append({"role": role, "content": content})

    if convo:
        last = convo[-1]
        if last["role"] == "user":
            last["content"] = [
                {
                    "type": "text",
                    "text": last["content"],
                    "cache_control": _cache_control_block(),
                }
            ]
        convo[-1] = last

    payload = {
        "model": model_id,
        "max_tokens": int(max_tokens),
        "messages": convo,
    }
    if system_blocks:
        payload["system"] = system_blocks

    _apply_reasoning(payload, thinking, (api_entry or {}).get("reasoning_options"))
    return payload


def _build_chat_payload(model_id: str, messages, max_tokens: int, thinking: str, api_entry) -> dict:
    payload = {
        "model": model_id,
        "messages": messages,
        "max_tokens": int(max_tokens),
    }
    _apply_reasoning(payload, thinking, (api_entry or {}).get("reasoning_options"))
    return payload


def _to_responses_input(messages) -> list[dict]:
    out = []
    for item in messages:
        role = item["role"]
        content_type = "input_text" if role in ("user", "system") else "output_text"
        out.append(
            {
                "role": role,
                "content": [
                    {
                        "type": content_type,
                        "text": item["content"],
                    }
                ],
            }
        )
    return out


def _build_responses_payload(
    model_id: str,
    messages,
    max_tokens: int,
    thinking: str,
    api_entry,
    session_id: str,
) -> dict:
    payload = {
        "model": model_id,
        "input": _to_responses_input(messages),
        "max_output_tokens": int(max_tokens),
        "prompt_cache_key": sanitize_session_id(session_id),
    }
    _apply_reasoning(payload, thinking, (api_entry or {}).get("reasoning_options"))
    return payload


def _extract_chat_text(payload: dict) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
            if isinstance(first.get("text"), str):
                return first["text"]
    raise ValueError("could not extract text from OpenCode chat/completions response")


def _extract_messages_text(payload: dict) -> str:
    content = payload.get("content")
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "".join(parts)
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    raise ValueError("could not extract text from OpenCode messages response")


def _extract_responses_text(payload: dict) -> str:
    if isinstance(payload.get("output_text"), str) and payload["output_text"].strip():
        return payload["output_text"]

    output = payload.get("output")
    if isinstance(output, list):
        parts = []
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message":
                content = item.get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "output_text":
                            text = block.get("text")
                            if isinstance(text, str):
                                parts.append(text)
            elif item.get("type") == "output_text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
        if parts:
            return "".join(parts)

    raise ValueError("could not extract text from OpenCode responses payload")


def _log_cache_usage(payload: dict, model_id: str, transport: str) -> None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return

    cache_bits = []
    for key in (
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "input_tokens_details",
        "prompt_tokens_details",
    ):
        value = usage.get(key)
        if value:
            cache_bits.append(f"{key}={value}")

    if cache_bits:
        print(f"[OpenCode] {model_id} ({transport}) usage: " + ", ".join(cache_bits))


def _post_opencode(path: str, payload: dict, headers: dict, timeout: int | None):
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError(
            "OpenCode LLM calls require the 'requests' package. "
            "Install requests or select the Cursor source."
        ) from exc

    url = f"{OPENCODE_GO_BASE.rstrip('/')}{path}"
    response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if response.status_code in (401, 403):
        raise RuntimeError(
            f"OpenCode authentication failed ({response.status_code}): "
            "check OPENCODE_API_KEY and Go subscription entitlement"
        )
    if response.status_code == 429:
        raise RuntimeError(
            f"OpenCode quota exceeded for request ({response.status_code}): {response.text[:300]}"
        )
    if response.status_code in (404, 405):
        raise RuntimeError(
            f"OpenCode endpoint mismatch ({response.status_code}) for {url}: {response.text[:300]}"
        )
    response.raise_for_status()
    return response.json()


def call_llm_opencode(
    messages,
    model=None,
    thinking="medium",
    max_tokens=8192,
    timeout=None,
    session_id=None,
):
    model_id = normalize_model_id(model) or DEFAULT_OPENCODE_MODEL
    session = sanitize_session_id(session_id or get_opencode_session())
    transport = resolve_transport(model_id)
    endpoint_path = resolve_endpoint_path(transport)
    normalized_messages = _normalize_messages(messages)
    if not normalized_messages:
        raise ValueError("messages must contain at least one item")

    from opencode_registry import get_model_catalog

    api_entry = get_model_catalog().get(model_id, {})
    headers = build_opencode_headers(session)

    if transport == "messages":
        payload = _build_messages_payload(
            model_id,
            normalized_messages,
            max_tokens,
            thinking,
            api_entry,
        )
        raw = _post_opencode(endpoint_path, payload, headers, timeout)
        text = _extract_messages_text(raw)
    elif transport == "responses":
        payload = _build_responses_payload(
            model_id,
            normalized_messages,
            max_tokens,
            thinking,
            api_entry,
            session,
        )
        raw = _post_opencode(endpoint_path, payload, headers, timeout)
        text = _extract_responses_text(raw)
    else:
        payload = _build_chat_payload(
            model_id,
            normalized_messages,
            max_tokens,
            thinking,
            api_entry,
        )
        raw = _post_opencode(endpoint_path, payload, headers, timeout)
        text = _extract_chat_text(raw)

    _log_cache_usage(raw if isinstance(raw, dict) else {}, model_id, transport)

    return {
        "content": text,
        "source": "opencode",
        "model": model_id,
        "transport": transport,
        "endpoint_path": endpoint_path,
        "session_id": session,
        "raw": raw,
    }


if __name__ == "__main__":
    os.environ.setdefault("OPENCODE_API_KEY", "test-key")
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    chat_payload = _build_chat_payload("deepseek-v4-flash", msgs, 100, "medium", {})
    assert chat_payload["model"] == "deepseek-v4-flash"

    msg_payload = _build_messages_payload("minimax-m3", msgs, 100, "medium", {})
    assert msg_payload["system"][0]["cache_control"]["type"] == "ephemeral"
    assert msg_payload["messages"][-1]["content"][0]["cache_control"]["type"] == "ephemeral"

    resp_payload = _build_responses_payload(
        "gpt-5.6-luna",
        msgs,
        100,
        "medium",
        {},
        "job-1:planner",
    )
    assert resp_payload["prompt_cache_key"] == "job-1:planner"

    headers = build_opencode_headers("job-1:planner")
    assert headers["x-opencode-session"] == "job-1:planner"
    assert headers["User-Agent"].startswith("gen2-agent/")

    assert _extract_chat_text({"choices": [{"message": {"content": "ok"}}]}) == "ok"
    assert _extract_messages_text({"content": [{"type": "text", "text": "hi"}]}) == "hi"
    assert _extract_responses_text({"output_text": "done"}) == "done"

    print("CALL_LLM_OPENCODE SELF TEST PASSED")
