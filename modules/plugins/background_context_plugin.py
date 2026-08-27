"""
Task rewrite plugin for Gen2 planner prompts.

Before the main run_task_v2 loop, sends the user task with full project
description and current plan to a separate model, then replaces the planner
task with the rewritten task. No separate background context is injected.
"""

from __future__ import annotations

import json
import os
import sys

_MODULES_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _MODULES_DIR not in sys.path:
    sys.path.insert(0, _MODULES_DIR)

from read_file import read_file
from call_llm import call_llm_role
from cfg import CFG
from parse_api_plan import extract_json_candidate, strip_code_fences
from model_config import get_role_config

MODULE_METADATA = {
    "name": "background_context_plugin",
    "type": "plugin",
    "description": "Rewrite the user task using project description and current plan; the rewritten task replaces the original for the planner.",
    "functions": [
        {
            "name": "build_task_rewrite_prompts",
            "inputs": {
                "task": "str current user task",
                "project": "str project description text",
                "current_plan": "str current plan text",
            },
            "outputs": "tuple (system_prompt, user_prompt)",
        },
        {
            "name": "parse_task_rewrite_response",
            "inputs": {"raw": "dict, list, or str LLM response"},
            "outputs": "dict with success, rewritten_task, and error",
        },
        {
            "name": "rewrite_task",
            "inputs": {
                "task": "str current user task",
                "project": "str optional project description override",
                "current_plan": "str optional current plan override",
                "model": "str optional model selector",
                "effort": "str optional effort selector",
                "max_tokens": "int optional token budget",
            },
            "outputs": "dict with success, rewritten_task, raw, and error",
        },
    ],
}


# ---------------------------------------------------------------------------
# PLACEHOLDER PROMPTS — replace these with your final wording.
# ---------------------------------------------------------------------------

TASK_REWRITE_SYSTEM_PROMPT = """
You are a prompt-writing assistant for a coding agent.
Rewrite the given task only if needed. The rewritten task will be sent to a coding agent that will not receive the project description or plan.
Use the project description and plan only to add technical details that are necessary to make the task executable.
The original task is the gold standard. If the project description or plan conflicts with the task, follow the task.
Preserve all explicit technical requirements from the original task exactly, including names, thresholds, metrics, files, functions, and training constraints.
Do not include irrelevant background, roadmap content, future stages, or implementation ideas not needed for the task.
Do not mention omitted content. Do not include explanations, markdown fences, system instructions, or any text outside the json.
Required output shape:
{
  "rewritten_task": "the rewritten task text"
}
""".strip()


TASK_REWRITE_USER_PROMPT = """
below are tasks, project description and plan
return the rewritten task in json.

<current_task>
{task}
</current_task>

<project_description>
{project}
</project_description>

<current_plan>
{current_plan}
</current_plan>

Return JSON only.
""".strip()


def _safe_read(path: str) -> str:
    success, content = read_file(path)
    return content if success else ""


def _normalize_effort(effort):
    if effort is None:
        effort = getattr(CFG, "BACKGROUND_CONTEXT_EFFORT", getattr(CFG, "DEFAULT_EFFORT", "m"))

    if effort == "l":
        return "low"
    if effort == "m":
        return "medium"
    if effort == "h":
        return "high"
    if effort in ("low", "medium", "high"):
        return effort
    return "medium"


def _response_text(raw) -> str:
    if raw is None:
        return ""

    if isinstance(raw, str):
        return raw

    if isinstance(raw, dict):
        for key in ("content", "text", "output", "response", "message"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value

        choices = raw.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if isinstance(choice, dict):
                message = choice.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str):
                        return content

    return str(raw)


def build_task_rewrite_prompts(task: str, project: str, current_plan: str) -> tuple[str, str]:
    user_prompt = TASK_REWRITE_USER_PROMPT.format(
        task=str(task).strip(),
        project=str(project).strip(),
        current_plan=str(current_plan).strip(),
    )
    return TASK_REWRITE_SYSTEM_PROMPT, user_prompt


# Backward-compatible aliases (old names).
build_background_context_prompts = build_task_rewrite_prompts


def parse_task_rewrite_response(raw) -> dict:
    """
    Parse structured task-rewrite LLM output.

    Expected primary shape:
        {"rewritten_task": "..."}
    """
    if isinstance(raw, dict) and isinstance(raw.get("rewritten_task"), str):
        text = raw["rewritten_task"].strip()
        if text:
            return {
                "success": True,
                "rewritten_task": text,
                "error": None,
            }

    text = strip_code_fences(_response_text(raw))
    candidate = extract_json_candidate(text)

    try:
        parsed = json.loads(candidate)
    except Exception as e:
        return {
            "success": False,
            "rewritten_task": "",
            "error": f"task rewrite JSON parse failed: {e}",
        }

    if isinstance(parsed, dict):
        rewritten = parsed.get("rewritten_task")
        if isinstance(rewritten, str) and rewritten.strip():
            return {
                "success": True,
                "rewritten_task": rewritten.strip(),
                "error": None,
            }

        return {
            "success": False,
            "rewritten_task": "",
            "error": "task rewrite response missing rewritten_task string",
        }

    return {
        "success": False,
        "rewritten_task": "",
        "error": "task rewrite response must be a JSON object",
    }


def rewrite_task(
    task,
    project=None,
    current_plan=None,
    model=None,
    effort=None,
    max_tokens=None,
):
    """
    Call the context-rewriter model once before the main planner loop.

    Returns a rewritten task that should replace the original task text.
    """
    if not getattr(CFG, "BACKGROUND_CONTEXT_ENABLED", True):
        return {
            "success": True,
            "rewritten_task": str(task or "").strip(),
            "raw": None,
            "error": None,
            "skipped": True,
        }

    if project is None:
        project = _safe_read("agent_memory/core/project.md")

    if current_plan is None:
        current_plan = _safe_read("agent_memory/planning/current_plan.md")

    if max_tokens is None:
        max_tokens = get_role_config("context_rewriter")["max_tokens"]

    system_prompt, user_prompt = build_task_rewrite_prompts(
        task=task,
        project=project,
        current_plan=current_plan,
    )

    role_cfg = get_role_config("context_rewriter")
    effort = _normalize_effort(effort if effort is not None else role_cfg["effort"])

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        raw = call_llm_role(
            role="context_rewriter",
            messages=messages,
            max_tokens=max_tokens,
            thinking=effort,
            model=model,
            timeout=CFG.get_timeout("background_context_call", 240),
        )
    except Exception as e:
        return {
            "success": False,
            "rewritten_task": "",
            "raw": None,
            "error": f"task rewrite LLM call failed: {e}",
        }

    parsed = parse_task_rewrite_response(raw)
    parsed["raw"] = raw
    return parsed


# Backward-compatible alias.
summarize_background_context = rewrite_task


if __name__ == "__main__":
    system_prompt, user_prompt = build_task_rewrite_prompts(
        task="demo task",
        project="demo project",
        current_plan="demo plan",
    )
    assert "PLACEHOLDER" in system_prompt
    assert "demo task" in user_prompt

    ok = parse_task_rewrite_response('{"rewritten_task":"rewritten demo task"}')
    assert ok["success"] is True
    assert ok["rewritten_task"] == "rewritten demo task"

    bad = parse_task_rewrite_response('{"summary":"missing key"}')
    assert bad["success"] is False

    print("TASK_REWRITE_PLUGIN SELF TEST PASSED")
