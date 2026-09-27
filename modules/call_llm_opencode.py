"""
OpenCode Go LLM calls for Gen2 agent roles.
"""

from __future__ import annotations

import json
import os
import re

from cfg import CFG
from model_config import normalize_effort
from opencode_registry import (
    DEFAULT_OPENCODE_MODEL,
    get_opencode_go_base,
    get_model_catalog,
    normalize_model_id,
    resolve_endpoint_path,
    resolve_transport_routing,
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
    from opencode_config import get_opencode_api_key

    return get_opencode_api_key()


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
        # Drop lone UTF-16 surrogates that can break strict JSON encoders.
        content = content.encode("utf-8", "surrogatepass").decode("utf-8", "replace")
        out.append({"role": role, "content": content})
    return out


def _clamp_output_tokens(max_tokens: int, api_entry: dict | None) -> int:
    limit = 0
    if isinstance(api_entry, dict):
        raw_limit = api_entry.get("limit")
        if isinstance(raw_limit, dict):
            try:
                limit = int(raw_limit.get("output") or 0)
            except Exception:
                limit = 0
    value = int(max_tokens)
    if limit > 0:
        value = min(value, limit)
    return max(1, value)


def _responses_system_role(model_id: str) -> str:
    model_id = str(model_id or "").strip().lower()
    if model_id.startswith("o"):
        return "developer"
    match = re.match(r"^gpt-(\d+)", model_id)
    if match and int(match.group(1)) >= 5:
        return "developer"
    return "system"


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


def _split_responses_messages(messages, model_id: str) -> tuple[str, list[dict]]:
    instructions_parts: list[str] = []
    input_items: list[dict] = []
    system_role = _responses_system_role(model_id)

    for item in messages:
        role = item["role"]
        content = item["content"]
        if role == "system":
            instructions_parts.append(content)
            continue
        if role == "user":
            input_items.append(
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": content}],
                }
            )
            continue
        if role == "assistant":
            input_items.append({"role": "assistant", "content": content})
            continue
        input_items.append({"role": role, "content": content})

    instructions = "\n\n".join(part for part in instructions_parts if part).strip()
    return instructions, input_items


def _build_responses_payload(
    model_id: str,
    messages,
    max_tokens: int,
    thinking: str,
    api_entry,
    session_id: str,
    *,
    system_mode: str = "input",
    include_reasoning: bool = True,
    include_prompt_cache_key: bool = True,
) -> dict:
    instructions, input_items = _split_responses_messages(messages, model_id)
    payload = {
        "model": model_id,
        "input": input_items,
        "max_output_tokens": _clamp_output_tokens(max_tokens, api_entry),
    }
    if include_prompt_cache_key:
        payload["prompt_cache_key"] = sanitize_session_id(session_id)

    if instructions:
        if system_mode == "instructions":
            payload["instructions"] = instructions
        else:
            payload["input"] = [
                {"role": _responses_system_role(model_id), "content": instructions},
                *input_items,
            ]

    if include_reasoning:
        _apply_reasoning(payload, thinking, (api_entry or {}).get("reasoning_options"))
    return payload


def _responses_payload_variants(
    model_id: str,
    messages,
    max_tokens: int,
    thinking: str,
    api_entry,
    session_id: str,
) -> list[tuple[str, dict]]:
    variants: list[tuple[str, dict]] = []
    seen: set[str] = set()

    def _add(label: str, **kwargs):
        payload = _build_responses_payload(
            model_id,
            messages,
            max_tokens,
            thinking,
            api_entry,
            session_id,
            **kwargs,
        )
        key = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        if key in seen:
            return
        seen.add(key)
        variants.append((label, payload))

    _add("input-system")
    _add("instructions", system_mode="instructions")
    _add("input-system-no-reasoning", include_reasoning=False)
    _add("instructions-no-reasoning", system_mode="instructions", include_reasoning=False)
    _add(
        "input-system-no-cache",
        include_reasoning=False,
        include_prompt_cache_key=False,
    )
    return variants


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


class OpenCodeEndpointMismatch(RuntimeError):
    def __init__(self, status_code: int, url: str, body: str):
        self.status_code = status_code
        self.url = url
        self.body = body
        super().__init__(
            f"OpenCode endpoint mismatch ({status_code}) for {url}: {body[:300]}"
        )


class OpenCodeBadRequest(RuntimeError):
    def __init__(self, status_code: int, url: str, body: str):
        self.status_code = status_code
        self.url = url
        self.body = body
        super().__init__(
            f"OpenCode bad request ({status_code}) for {url}: {body[:500]}"
        )


def _post_opencode(path: str, payload: dict, headers: dict, timeout: int | None):
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError(
            "OpenCode LLM calls require the 'requests' package. "
            "Install requests or select the Cursor source."
        ) from exc

    url = f"{get_opencode_go_base().rstrip('/')}{path}"
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
        raise OpenCodeEndpointMismatch(response.status_code, url, response.text)
    if response.status_code == 400:
        raise OpenCodeBadRequest(response.status_code, url, response.text)
    response.raise_for_status()
    return response.json()


_TRANSPORT_FALLBACK_ORDER = ("chat", "responses", "messages")


def _candidate_transports(primary: str) -> list[str]:
    primary = str(primary or "chat").strip().lower()
    return [primary] + [t for t in _TRANSPORT_FALLBACK_ORDER if t != primary]


def _dispatch_transport(
    transport: str,
    model_id: str,
    normalized_messages,
    max_tokens: int,
    thinking: str,
    api_entry,
    session: str,
    headers: dict,
    timeout: int | None,
):
    endpoint_path = resolve_endpoint_path(transport)
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
        last_bad_request = None
        for label, payload in _responses_payload_variants(
            model_id,
            normalized_messages,
            max_tokens,
            thinking,
            api_entry,
            session,
        ):
            try:
                raw = _post_opencode(endpoint_path, payload, headers, timeout)
                if label != "input-system":
                    print(
                        f"[OpenCode] {model_id}: responses payload '{label}' succeeded "
                        f"after earlier variant was rejected"
                    )
                text = _extract_responses_text(raw)
                break
            except OpenCodeBadRequest as exc:
                last_bad_request = exc
                continue
        else:
            raise RuntimeError(str(last_bad_request) if last_bad_request else "OpenCode responses request failed")
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
    return text, raw, transport, endpoint_path


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
    normalized_messages = _normalize_messages(messages)
    if not normalized_messages:
        raise ValueError("messages must contain at least one item")

    api_entry = get_model_catalog().get(model_id, {})
    routing = resolve_transport_routing(model_id, api_entry)
    primary_transport = routing["transport"]
    headers = build_opencode_headers(session)

    last_mismatch = None
    text = ""
    raw = {}
    transport = primary_transport
    endpoint_path = resolve_endpoint_path(transport)
    for candidate in _candidate_transports(primary_transport):
        try:
            text, raw, transport, endpoint_path = _dispatch_transport(
                candidate,
                model_id,
                normalized_messages,
                max_tokens,
                thinking,
                api_entry,
                session,
                headers,
                timeout,
            )
            if candidate != primary_transport:
                print(
                    f"[OpenCode] {model_id}: retried with transport "
                    f"'{candidate}' after '{primary_transport}' endpoint mismatch"
                )
            break
        except OpenCodeEndpointMismatch as exc:
            last_mismatch = exc
            continue
    else:
        raise RuntimeError(str(last_mismatch) if last_mismatch else "OpenCode request failed")

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
    assert resp_payload["input"][0]["role"] == "developer"
    assert resp_payload["input"][0]["content"] == "s"
    assert resp_payload["input"][1] == {
        "role": "user",
        "content": [{"type": "input_text", "text": "u"}],
    }
    muse_payload = _build_responses_payload(
        "muse-spark-1.3-contributor",
        msgs,
        100,
        "medium",
        {"reasoning_options": [{"type": "effort", "values": ["low", "medium"]}]},
        "job-1:debug",
    )
    assert muse_payload["input"][0]["role"] == "system"
    assert len(_responses_payload_variants("muse-spark-1.3-contributor", msgs, 100, "medium", {}, "job-1:debug")) >= 3

    headers = build_opencode_headers("job-1:planner")
    assert headers["x-opencode-session"] == "job-1:planner"
    assert headers["User-Agent"].startswith("gen2-agent/")

    assert _extract_chat_text({"choices": [{"message": {"content": "ok"}}]}) == "ok"
    assert _extract_messages_text({"content": [{"type": "text", "text": "hi"}]}) == "hi"
    assert _extract_responses_text({"output_text": "done"}) == "done"

    print("CALL_LLM_OPENCODE SELF TEST PASSED")
