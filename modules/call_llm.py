from cfg import CFG

MODULE_METADATA = {
    "name": "call_llm",
    "type": "function",
    "description": "Call LLM via relay server or Cursor SDK using global per-role configuration, with model-specific fallback chains on transient Cursor failures.",
    "functions": [
        {
            "name": "call_llm",
            "inputs": {
                "messages": "list[dict[str, str]]",
                "max_tokens": "int",
                "thinking": "str",
                "provider": "str | None",
                "model": "str | None",
                "gemini_config": "dict | None",
                "timeout": "int | None"
            },
            "outputs": "dict",
            "description": "Relay-only LLM call kept for backward compatibility."
        },
        {
            "name": "call_llm_role",
            "inputs": {
                "role": "str configured role name",
                "messages": "list[dict[str, str]]",
                "max_tokens": "int | None",
                "thinking": "str | None",
                "model": "str | None",
                "source": "str | None",
                "provider": "str | None",
                "timeout": "int | None",
                "cursor_params": "list | None Cursor model selection parameters",
            },
            "outputs": "dict",
            "description": "Dispatch LLM call using saved role configuration."
        }
    ]
}


def call_llm(
    messages: list[dict[str, str]],
    max_tokens: int,
    thinking: str,
    provider: str | None,
    model: str | None,
    gemini_config: dict | None,
    timeout: int | None
) -> dict:
    return call_llm_relay(
        messages=messages,
        max_tokens=max_tokens,
        thinking=thinking,
        provider=provider,
        model=model,
        gemini_config=gemini_config,
        timeout=timeout,
    )


def call_llm_relay(
    messages: list[dict[str, str]],
    max_tokens: int,
    thinking: str,
    provider: str | None,
    model: str | None,
    gemini_config: dict | None,
    timeout: int | None,
) -> dict:
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError(
            "Relay LLM calls require the 'requests' package. "
            "Install requests or select the Cursor source."
        ) from exc

    payload = {
        "max_tokens": max_tokens,
        "thinking": thinking,
        "messages": messages,
    }
    if provider is not None:
        payload["provider"] = provider
    if model is not None:
        payload["model"] = model
    if provider == "gemini":
        payload["gemini"] = {
            "enterprise": True,
            "location": "global",
            "api_version": "v1",
            "response_mime_type": "application/json",
        }
        if gemini_config:
            payload["gemini"].update(gemini_config)

    relay_url = CFG.RELAY_URL
    if timeout is None:
        timeout = CFG.get_timeout("planner_call", 240)

    response = requests.post(
        relay_url,
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def _role_timeout(role: str, timeout=None):
    if timeout is not None:
        return timeout

    timeout_key = {
        "main_planner": "planner_call",
        "debug": "debug_call",
        "context_rewriter": "background_context_call",
        "discussion": "discussion_call",
        "verifier": "verifier_call",
        "meta_writer": "planner_call",
        "local_repair": "planner_call",
    }.get(role, "planner_call")
    return CFG.get_timeout(timeout_key, 240)


def _call_llm_attempt(
    *,
    source: str,
    model,
    messages,
    max_tokens,
    thinking,
    provider=None,
    timeout=None,
    gemini_config=None,
    cursor_params=None,
):
    from call_llm_cursor import call_llm_cursor
    from model_registry import normalize_model_provider

    source = str(source or "relay").strip().lower()

    if source == "cursor":
        return call_llm_cursor(
            messages=messages,
            model=model,
            thinking=thinking,
            max_tokens=max_tokens,
            timeout=timeout,
            cursor_params=cursor_params,
        )

    resolved_provider = provider
    resolved_model = model
    if resolved_provider is None:
        resolved_provider, resolved_model = normalize_model_provider(model)

    return call_llm_relay(
        messages=messages,
        max_tokens=max_tokens,
        thinking=thinking,
        provider=resolved_provider,
        model=resolved_model,
        gemini_config=gemini_config,
        timeout=timeout,
    )


def call_llm_role(
    role: str,
    messages: list[dict[str, str]],
    max_tokens=None,
    thinking=None,
    model=None,
    source=None,
    provider=None,
    timeout=None,
    gemini_config=None,
    cursor_params=None,
):
    from model_config import get_role_config, normalize_effort
    from llm_fallback import build_fallback_chain, format_fallback_attempt, is_retryable_llm_error
    from cursor_model_selection import normalize_cursor_params

    cfg = get_role_config(role)

    source = str(source or cfg.get("source") or "relay").strip().lower()
    model = model if model is not None else cfg.get("model")
    thinking = thinking if thinking is not None else normalize_effort(cfg.get("effort"))
    max_tokens = int(max_tokens if max_tokens is not None else cfg.get("max_tokens") or 8192)
    timeout = _role_timeout(role, timeout)
    if cursor_params is None:
        cursor_params = normalize_cursor_params(cfg.get("cursor_params"))
    else:
        cursor_params = normalize_cursor_params(cursor_params)

    chain = build_fallback_chain(source, model, cursor_params=cursor_params)
    errors = []

    for index, attempt in enumerate(chain):
        attempt_source = attempt["source"]
        attempt_model = attempt["model"]
        attempt_params = normalize_cursor_params(attempt.get("cursor_params"))
        label = format_fallback_attempt(attempt)

        try:
            result = _call_llm_attempt(
                source=attempt_source,
                model=attempt_model,
                messages=messages,
                max_tokens=max_tokens,
                thinking=thinking,
                provider=provider,
                timeout=timeout,
                gemini_config=gemini_config,
                cursor_params=attempt_params,
            )
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            has_next = index < len(chain) - 1
            if has_next and is_retryable_llm_error(exc):
                print(
                    f"[LLM Fallback] {label} failed with retryable error; "
                    f"trying {format_fallback_attempt(chain[index + 1])}"
                )
                continue
            if has_next:
                print(
                    f"[LLM Fallback] {label} failed with non-retryable error; "
                    "not trying further fallbacks"
                )
            raise

        if index > 0:
            print(f"[LLM Fallback] succeeded with {label}")

        if isinstance(result, dict):
            result = dict(result)
            result["llm_source"] = attempt_source
            result["llm_model"] = attempt_model
            if attempt_params:
                result["cursor_params"] = attempt_params
            if index > 0:
                result["fallback_used"] = True
                result["fallback_from"] = format_fallback_attempt(chain[0])
            else:
                result["fallback_used"] = False

        return result

    if errors:
        raise RuntimeError("all LLM fallback attempts failed: " + " | ".join(errors))

    raise RuntimeError("no LLM fallback attempts were made")
