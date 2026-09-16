"""
Record planner-reported conflicts/ambiguities and terminate the task loop.

Entries are appended to agent_memory/decision.json with fields:
date, task, conflict
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from cfg import CFG

MODULE_METADATA = {
    "name": "conflict",
    "type": "function",
    "description": "Append conflict/ambiguity decisions to agent_memory/decision.json and terminate the planner loop as failed.",
    "functions": [
        {
            "name": "write_conflict_decision",
            "inputs": {
                "task": "str original task text",
                "conflict": "str description of ambiguity, conflict, or unresolvable error",
            },
            "outputs": "dict with success, path, and item",
        },
        {
            "name": "execute_conflict",
            "inputs": {
                "task": "str original task text",
                "payload": "dict with conflict description",
            },
            "outputs": "dict with success, item, and error",
        },
        {
            "name": "get_conflict_endpoint_doc",
            "inputs": {},
            "outputs": "str endpoint documentation block for planner prompts",
        },
    ],
}


DECISION_REL_PATH = os.path.join("agent_memory", "decision.json")


def decision_path() -> str:
    return os.path.join(CFG.PROJECT_ROOT, DECISION_REL_PATH)


def write_conflict_decision(task: str, conflict: str) -> dict:
    path = decision_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = []

    if not isinstance(data, list):
        data = []

    item = {
        "date": datetime.utcnow().isoformat() + "Z",
        "task": str(task or "").strip(),
        "conflict": str(conflict or "").strip(),
    }

    data.append(item)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return {
        "success": True,
        "path": DECISION_REL_PATH.replace("\\", "/"),
        "item": item,
    }


def is_conflict_failure(call=None, result=None, execution_result=None) -> bool:
    if isinstance(execution_result, dict) and execution_result.get("conflict"):
        return True
    if isinstance(result, dict) and result.get("conflict"):
        return True
    for item in (call, result):
        if isinstance(item, dict) and str(item.get("url") or "").strip() == "/conflict":
            return True
    return False


def conflict_message(
    call=None,
    result=None,
    execution_result=None,
    default="Task terminated via /conflict",
) -> str:
    if isinstance(execution_result, dict):
        text = str(execution_result.get("error") or "").strip()
        if text:
            return text
        conflict_output = execution_result.get("conflict_output")
        if isinstance(conflict_output, dict):
            text = str(conflict_output.get("conflict") or "").strip()
            if text:
                return text
    if isinstance(result, dict):
        text = str(result.get("error") or "").strip()
        if text:
            return text
        output = result.get("output")
        if isinstance(output, dict):
            text = str(output.get("conflict") or output.get("error") or "").strip()
            if text:
                return text
    if isinstance(call, dict):
        payload = call.get("payload")
        if isinstance(payload, dict):
            text = str(payload.get("conflict") or "").strip()
            if text:
                return text
    return default


def execute_conflict(task: str, payload: dict | None) -> dict:
    payload = payload if isinstance(payload, dict) else {}
    conflict = str(payload.get("conflict") or "").strip()

    if not conflict:
        return {
            "success": False,
            "item": None,
            "error": "Missing non-empty conflict in /conflict payload",
        }

    try:
        result = write_conflict_decision(task=task, conflict=conflict)
        return {
            "success": True,
            "item": result.get("item"),
            "path": result.get("path"),
            "error": None,
        }
    except Exception as e:
        return {
            "success": False,
            "item": None,
            "error": str(e),
        }


def get_conflict_endpoint_doc(loop: str = "main") -> str:
    loop = str(loop or "main").strip().lower()
    index = 10 if loop == "debug" else 11
    return f"""
{index}. /conflict

Use /conflict when the task cannot proceed because of ambiguity, conflicting
requirements, missing information that cannot be inferred, or an unresolvable
error that should not be handled by debug repair.

Calling /conflict terminates the current loop immediately and marks the task as
failed. The conflict description is appended to agent_memory/decision.json.

Payload:
{{
  "conflict": "describe ambiguity, conflicting requirements, or unresolvable errors"
}}

Rules:
- Use only when the task cannot be completed without external clarification or resolution.
- /conflict must be the final call in the planner turn.
- Calling /conflict terminates the loop immediately and marks the task as failed.
- The payload is appended to agent_memory/decision.json with date and task metadata.
""".strip()


if __name__ == "__main__":
    import tempfile

    original_root = CFG.PROJECT_ROOT
    with tempfile.TemporaryDirectory() as tmp:
        CFG.PROJECT_ROOT = tmp
        res = execute_conflict(
            task="demo task",
            payload={"conflict": "requirements conflict between A and B"},
        )
        assert res["success"], res

        with open(decision_path(), "r", encoding="utf-8") as f:
            data = json.load(f)

        assert len(data) == 1, data
        assert data[0]["task"] == "demo task"
        assert "requirements conflict" in data[0]["conflict"]
        assert data[0]["date"]

        res2 = execute_conflict(task="demo task 2", payload={"conflict": "second entry"})
        assert res2["success"], res2

        with open(decision_path(), "r", encoding="utf-8") as f:
            data = json.load(f)

        assert len(data) == 2, data

    CFG.PROJECT_ROOT = original_root
    doc = get_conflict_endpoint_doc()
    assert "/conflict" in doc
    assert "11. /conflict" in doc
    assert "10. /conflict" in get_conflict_endpoint_doc("debug")
    assert "PLACEHOLDER" not in doc
    assert "decision.json" in doc
    assert is_conflict_failure(call={"url": "/conflict", "payload": {"conflict": "x"}})
    assert is_conflict_failure(result={"url": "/conflict", "error": "bad"})
    assert is_conflict_failure(execution_result={"conflict": True})
    assert not is_conflict_failure(call={"url": "/write"}, result={"url": "/write"})
    assert conflict_message(
        call={"url": "/conflict", "payload": {"conflict": "ambiguous scope"}},
    ) == "ambiguous scope"
    print("CONFLICT SELF TEST PASSED")
