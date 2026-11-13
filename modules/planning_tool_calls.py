"""Parse and execute Gen2 /subagent tool calls inside planning discussion turns."""

from __future__ import annotations

import json
import re

from parse_api_plan import MAX_SUBAGENT_BATCH, extract_json_candidate, parse_text_candidate, validate_call

MODULE_METADATA = {
    "name": "planning_tool_calls",
    "type": "function",
    "description": "Parse planning-mode <tool_call> wrappers and run review /subagent workers.",
    "functions": [
        {
            "name": "parse_planning_tool_calls",
            "inputs": {"text": "str assistant reply"},
            "outputs": "dict with has_tool_call, calls, error",
        }
    ],
}

TOOL_CALL_TAG = "tool_call"
TOOL_CALL_PATTERN = re.compile(
    rf"<{TOOL_CALL_TAG}\s*>([\s\S]*?)</{TOOL_CALL_TAG}\s*>",
    flags=re.IGNORECASE,
)

PLANNING_TOOL_LOOP_CAP = MAX_SUBAGENT_BATCH

TOOL_RESULT_NUDGE = (
    "Review subagent results follow. Synthesize for the user or emit another "
    "<tool_call> if more inspection is required. Do not repeat these tags to the user."
)

TOOL_ERROR_NUDGE = (
    "Could not parse <tool_call>. Emit a valid review /subagent tool_call, "
    "or reply to the user without a <tool_call> wrapper."
)


def extract_tool_call_blocks(text: str) -> list[str]:
    return [match.group(1).strip() for match in TOOL_CALL_PATTERN.finditer(str(text or ""))]


def _normalize_tool_payload(raw) -> list:
    if isinstance(raw, dict):
        if "url" in raw and "payload" in raw:
            return [raw]
        if isinstance(raw.get("calls"), list):
            return raw["calls"]
        if "task" in raw:
            return [{"url": "/subagent", "payload": raw}]
        raise ValueError("tool_call JSON must be a /subagent call, a list, or {calls: [...]}")
    if isinstance(raw, list):
        return raw
    raise ValueError(f"tool_call JSON must be an object or list, got {type(raw).__name__}")


def _validate_planning_call(call, index: int) -> dict:
    normalized = validate_call(call, index)
    url = str(normalized.get("url") or "").strip()
    if url != "/subagent":
        raise ValueError(
            f"planning mode only allows /subagent tool calls, got {url or '(empty)'}"
        )
    payload = dict(normalized.get("payload") or {})
    role = str(payload.get("role") or "review").strip().lower()
    if role != "review":
        raise ValueError("planning mode /subagent payload.role must be review")
    payload["role"] = "review"
    normalized["payload"] = payload
    return normalized


def parse_planning_tool_calls(text: str) -> dict:
    """
    Discriminate conversation vs tool turns.

    Presence of <tool_call> means a tool turn even if the JSON is invalid.
    Absence means a normal user-facing assistant message.
    """
    blocks = extract_tool_call_blocks(text)
    if not blocks:
        return {"has_tool_call": False, "calls": [], "error": None}

    calls = []
    try:
        for block in blocks:
            candidate = extract_json_candidate(block) if block else ""
            parsed = parse_text_candidate(candidate if candidate else block)
            for item in _normalize_tool_payload(parsed):
                calls.append(_validate_planning_call(item, len(calls)))
        if not calls:
            raise ValueError("tool_call wrapper was empty")
        if len(calls) > MAX_SUBAGENT_BATCH:
            raise ValueError(
                f"planning turn may include at most {MAX_SUBAGENT_BATCH} /subagent calls"
            )
    except Exception as exc:
        return {"has_tool_call": True, "calls": [], "error": str(exc)}

    return {"has_tool_call": True, "calls": calls, "error": None}


def format_planning_tool_error(error: str) -> str:
    return f"<tool_error>\n{TOOL_ERROR_NUDGE}\n{error}\n</tool_error>"


def format_planning_subagent_results(results) -> str:
    from build_feedback_context import subagent_feedback_from_execution_result

    execution_result = {
        "results": [
            {
                "url": "/subagent",
                "output": {"subagent_result": item if isinstance(item, dict) else {"summary": str(item)}},
            }
            for item in (results or [])
        ]
    }
    body = subagent_feedback_from_execution_result(execution_result)
    if not body.strip():
        encoded = json.dumps(results or [], ensure_ascii=False)
        body = f"<subagent_result>{encoded}</subagent_result>"
    return f"<tool_results>\n{TOOL_RESULT_NUDGE}\n{body}\n</tool_results>"


def execute_planning_review_subagents(calls) -> list[dict]:
    from subagent_runner import run_review_subagents_parallel

    specs = []
    for call in calls or []:
        payload = dict((call or {}).get("payload") or {})
        specs.append(
            {
                "task": payload.get("task"),
                "files": payload.get("files") or [],
                "timeout_seconds": payload.get("timeout_seconds"),
            }
        )
    return run_review_subagents_parallel(specs)


if __name__ == "__main__":
    chat = parse_planning_tool_calls("Hello\n<plan># x</plan>")
    assert chat["has_tool_call"] is False
    assert chat["calls"] == []

    tool = parse_planning_tool_calls(
        """
<tool_call>
{"url": "/subagent", "payload": {"task": "inspect auth", "role": "review", "files": ["a.py"]}}
</tool_call>
"""
    )
    assert tool["has_tool_call"] is True
    assert tool["error"] is None
    assert tool["calls"][0]["payload"]["role"] == "review"

    listed = parse_planning_tool_calls(
        """
<tool_call>
[
  {"url": "/subagent", "payload": {"task": "one", "role": "review"}},
  {"url": "/subagent", "payload": {"task": "two", "role": "review"}}
]
</tool_call>
"""
    )
    assert len(listed["calls"]) == 2

    implement = parse_planning_tool_calls(
        """
<tool_call>
{"url": "/subagent", "payload": {"task": "edit", "role": "implement"}}
</tool_call>
"""
    )
    assert implement["has_tool_call"] is True
    assert implement["error"]
    assert implement["calls"] == []

    invalid = parse_planning_tool_calls("<tool_call>not json</tool_call>")
    assert invalid["has_tool_call"] is True
    assert invalid["error"]

    print("PLANNING_TOOL_CALLS SELF TEST PASSED")
