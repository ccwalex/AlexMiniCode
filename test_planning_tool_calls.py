"""Parser and prompt tests for discussion-mode XML /subagent tool calls."""

from __future__ import annotations

import os
import sys
from unittest import mock


CODE_DIR = os.path.dirname(os.path.abspath(__file__))
MODULES_DIR = os.path.join(CODE_DIR, "modules")

if CODE_DIR not in sys.path:
    sys.path.insert(0, CODE_DIR)
if MODULES_DIR not in sys.path:
    sys.path.insert(0, MODULES_DIR)

from planning_tool_calls import (  # noqa: E402
    format_planning_subagent_results,
    format_planning_tool_error,
    parse_planning_tool_calls,
)
from discussion_mode import DISCUSSION_SYSTEM_PROMPT, build_discussion_system_prompt  # noqa: E402


def _wrap(body: str) -> str:
    return f"<tool_call>\n{body}\n</tool_call>"


def test_chat_without_wrapper_is_not_a_tool_turn():
    parsed = parse_planning_tool_calls("Hello\n<plan># next</plan>")
    assert parsed["has_tool_call"] is False
    assert parsed["calls"] == []
    assert parsed["error"] is None


def test_single_subagent_call():
    parsed = parse_planning_tool_calls(
        _wrap(
            '{"url": "/subagent", "payload": {"task": "inspect auth", "role": "review", "files": ["a.py"]}}'
        )
    )
    assert parsed["has_tool_call"] is True
    assert parsed["error"] is None
    assert len(parsed["calls"]) == 1
    payload = parsed["calls"][0]["payload"]
    assert payload["task"] == "inspect auth"
    assert payload["role"] == "review"
    assert payload["files"] == ["a.py"]


def test_json_list_of_calls():
    parsed = parse_planning_tool_calls(
        _wrap(
            """
[
  {"url": "/subagent", "payload": {"task": "one", "role": "review"}},
  {"url": "/subagent", "payload": {"task": "two", "role": "review"}}
]
"""
        )
    )
    assert parsed["error"] is None
    assert [call["payload"]["task"] for call in parsed["calls"]] == ["one", "two"]


def test_calls_wrapper_object():
    parsed = parse_planning_tool_calls(
        _wrap(
            '{"calls": [{"url": "/subagent", "payload": {"task": "mapped", "role": "review"}}]}'
        )
    )
    assert parsed["error"] is None
    assert parsed["calls"][0]["payload"]["task"] == "mapped"


def test_payload_only_object():
    parsed = parse_planning_tool_calls(
        _wrap(
            '{"task": "inspect auth", "role": "review", "files": ["a.py"]}'
        )
    )
    assert parsed["error"] is None
    assert parsed["calls"][0]["url"] == "/subagent"
    assert parsed["calls"][0]["payload"]["task"] == "inspect auth"
    assert parsed["calls"][0]["payload"]["files"] == ["a.py"]


def test_reject_implement_role():
    parsed = parse_planning_tool_calls(
        _wrap('{"url": "/subagent", "payload": {"task": "edit", "role": "implement"}}')
    )
    assert parsed["has_tool_call"] is True
    assert parsed["calls"] == []
    assert "review" in parsed["error"]


def test_reject_non_subagent_urls():
    for url in ("/read", "/write"):
        parsed = parse_planning_tool_calls(
            _wrap(f'{{"url": "{url}", "payload": {{"path": "a.py"}}}}')
        )
        assert parsed["has_tool_call"] is True
        assert parsed["calls"] == []
        assert parsed["error"]
        assert url in parsed["error"] or "only allows /subagent" in parsed["error"]


def test_invalid_json_is_tool_turn_with_error():
    parsed = parse_planning_tool_calls("<tool_call>not json</tool_call>")
    assert parsed["has_tool_call"] is True
    assert parsed["calls"] == []
    assert parsed["error"]


def test_format_helpers():
    err = format_planning_tool_error("boom")
    assert err.startswith("<tool_error>")
    assert "native Cursor tools" in err
    assert "boom" in err

    body = format_planning_subagent_results(
        [
            {
                "success": True,
                "status": "done",
                "role": "review",
                "summary": "auth lives in a.py",
                "artifacts": [],
            }
        ]
    )
    assert body.startswith("<tool_results>")
    assert "<subagent_result>" in body
    assert "auth lives in a.py" in body


def test_prompt_documents_xml_contract_and_disabled_native_tools():
    prompt = build_discussion_system_prompt(
        decisions=[{"index": 0, "conflict": "scope"}],
        project="# project",
        current_plan="# plan",
    )
    assert DISCUSSION_SYSTEM_PROMPT in prompt
    assert '<tool_call>' in prompt
    assert '{"url": "/subagent", "payload": {"task": "self-contained review brief", "role": "review"' in prompt
    assert 'role": "review"' in prompt
    assert "native function-calling tools" in prompt
    assert "Task" in prompt
    assert "not available" in prompt
    assert "Do not retry" in prompt or "do not retry" in prompt


def test_execute_planning_review_subagents_passes_specs():
    from planning_tool_calls import execute_planning_review_subagents

    calls = [
        {
            "url": "/subagent",
            "payload": {
                "task": "inspect",
                "files": ["a.py"],
                "timeout_seconds": 30,
            },
        }
    ]
    with mock.patch(
        "subagent_runner.run_review_subagents_parallel",
        return_value=[{"success": True, "summary": "ok"}],
    ) as runner:
        results = execute_planning_review_subagents(calls)
    assert results[0]["summary"] == "ok"
    runner.assert_called_once_with(
        [
            {
                "task": "inspect",
                "files": ["a.py"],
                "timeout_seconds": 30,
            }
        ]
    )


def main() -> int:
    test_chat_without_wrapper_is_not_a_tool_turn()
    test_single_subagent_call()
    test_json_list_of_calls()
    test_calls_wrapper_object()
    test_payload_only_object()
    test_reject_implement_role()
    test_reject_non_subagent_urls()
    test_invalid_json_is_tool_turn_with_error()
    test_format_helpers()
    test_prompt_documents_xml_contract_and_disabled_native_tools()
    test_execute_planning_review_subagents_passes_specs()
    print("PLANNING TOOL CALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
