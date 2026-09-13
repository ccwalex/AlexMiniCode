"""
Validate cursor-only LLM calls never require OpenCode URL or credentials.
"""

from __future__ import annotations

import os
import sys


CODE_DIR = os.path.dirname(os.path.abspath(__file__))
MODULES_DIR = os.path.join(CODE_DIR, "modules")

if CODE_DIR not in sys.path:
    sys.path.insert(0, CODE_DIR)
if MODULES_DIR not in sys.path:
    sys.path.insert(0, MODULES_DIR)


def _clear_opencode_env() -> None:
    for key in list(os.environ):
        if key.startswith("OPENCODE"):
            del os.environ[key]


def _install_opencode_guards():
    import call_llm_opencode
    import opencode_registry

    class OpenCodeTouched(RuntimeError):
        pass

    original_post = call_llm_opencode._post_opencode
    original_fetch_live = opencode_registry._fetch_live_model_ids
    original_fetch_api = opencode_registry._fetch_api_json_models

    def guarded_post(*args, **kwargs):
        raise OpenCodeTouched("_post_opencode must not run for cursor-only calls")

    def guarded_fetch_live(*args, **kwargs):
        raise OpenCodeTouched("_fetch_live_model_ids must not run for cursor-only calls")

    def guarded_fetch_api(*args, **kwargs):
        raise OpenCodeTouched("_fetch_api_json_models must not run for cursor-only calls")

    call_llm_opencode._post_opencode = guarded_post
    opencode_registry._fetch_live_model_ids = guarded_fetch_live
    opencode_registry._fetch_api_json_models = guarded_fetch_api

    return {
        "post": original_post,
        "live": original_fetch_live,
        "api": original_fetch_api,
    }


def _restore_opencode_guards(originals) -> None:
    import call_llm_opencode
    import opencode_registry

    call_llm_opencode._post_opencode = originals["post"]
    opencode_registry._fetch_live_model_ids = originals["live"]
    opencode_registry._fetch_api_json_models = originals["api"]


def _install_cursor_stub():
    import call_llm_cursor

    original = call_llm_cursor.call_llm_cursor

    def stub_call_llm_cursor(*args, **kwargs):
        model = kwargs.get("model") or (args[1] if len(args) > 1 else "cursor-model")
        return {
            "content": '{"calls":[{"url":"/done","payload":{"summary":"ok"}}]}',
            "source": "cursor",
            "model": model,
        }

    call_llm_cursor.call_llm_cursor = stub_call_llm_cursor
    return original


def _restore_cursor_stub(original):
    import call_llm_cursor

    call_llm_cursor.call_llm_cursor = original


def main() -> int:
    _clear_opencode_env()
    os.environ["CURSOR_API_KEY"] = "test-cursor-key"

    from model_config import LLM_ROLES
    from call_llm import call_llm_role
    from structured_llm_retry import call_llm_role_with_parse_retry

    originals = _install_opencode_guards()
    cursor_original = _install_cursor_stub()

    messages = [
        {"role": "system", "content": "You are a test assistant."},
        {"role": "user", "content": "Reply with ok."},
    ]

    failures = []
    try:
        for role in LLM_ROLES:
            try:
                result = call_llm_role(
                    role=role,
                    messages=messages,
                    source="cursor",
                    model="composer-2.5",
                    max_tokens=256,
                    thinking="low",
                )
            except Exception as exc:
                failures.append(f"{role}: {exc}")
                continue

            if not isinstance(result, dict):
                failures.append(f"{role}: expected dict result, got {type(result).__name__}")
                continue
            if result.get("llm_source") != "cursor":
                failures.append(
                    f"{role}: expected llm_source=cursor, got {result.get('llm_source')!r}"
                )

        try:
            parsed = call_llm_role_with_parse_retry(
                role="main_planner",
                messages=messages,
                is_valid=lambda raw: isinstance(raw, dict) and bool(raw.get("content")),
                parse_fallback_kind="execution",
                llm_call=call_llm_role,
                source="cursor",
                model="composer-2.5",
                max_tokens=256,
                thinking="low",
            )
            if not isinstance(parsed, dict) or not parsed.get("content"):
                failures.append("main_planner parse retry: missing content")
        except Exception as exc:
            failures.append(f"main_planner parse retry: {exc}")
    finally:
        _restore_cursor_stub(cursor_original)
        _restore_opencode_guards(originals)

    if failures:
        print("CURSOR WITHOUT OPENCODE VALIDATION FAILED")
        for item in failures:
            print(f"- {item}")
        return 1

    print(f"CURSOR WITHOUT OPENCODE VALIDATION PASSED ({len(LLM_ROLES)} roles)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
