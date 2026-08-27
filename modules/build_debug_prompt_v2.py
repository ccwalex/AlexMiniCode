MODULE_METADATA = {
    "name": "build_debug_prompt_v2",
    "type": "function",
    "description": "Build Gen2 debug planner prompts for repairing runtime execution failures using compact API-call output.",
    "functions": [
        {
            "name": "build_debug_prompt_v2",
            "inputs": {
                "task": "str original user task",
                "failed_call": "dict failed Gen2 API call",
                "failed_result": "dict failed execution result",
                "run_state": "RunState object or None containing execution state",
                "file_context": "str unified file context rendered from attachments and read_cache",
                "context": "str optional additional debug context",
                "shell_instruction_prompt": "str shell permission/safety instructions",
                "scratchpad_content": "str optional current debug-loop scratchpad content",
                "main_scratchpad_content": "str optional read-only main-loop scratchpad context"
            },
            "outputs": "tuple (system_prompt, user_prompt) for Gen2 debug planner"
        }
    ]
}

import json

from read_file import read_file
from build_feedback_context import build_shell_feedback_context
from scratchpad import get_scratchpad_endpoint_doc, render_scratchpad_block
from conflict import get_conflict_endpoint_doc
from prompt_override import SYSTEM_PROMPT_OVERRIDE_BLOCK

def _classify_failed_call(failed_call, failed_result):
    url = ""
    if isinstance(failed_call, dict):
        url = str(failed_call.get("url", ""))

    error_text = str(_extract_error_text(failed_result))

    if url == "/edit":
        if "unexpected keyword argument" in error_text:
            return "edit_api_misuse"
        if "unmatched" in error_text or "SyntaxError" in error_text:
            return "edit_fn_syntax_error"
        if "CodeEdit" in error_text:
            return "edit_api_or_block_target_error"
        return "edit_failed"

    if url == "/write":
        if "verification" in error_text.lower():
            return "write_verification_failed"
        return "write_failed"

    if url == "/shell":
        return "shell_validation_or_runtime_failed"

    if url == "/read":
        return "read_failed"

    if url == "/request_feedback":
        return "feedback_failed"

    return "unknown_failure"


def _render_call_list(calls, *, empty_label="(none)"):
    if not calls:
        return empty_label
    return json.dumps(calls, indent=2, ensure_ascii=False)


def _render_main_plan_context(
    main_plan_calls=None,
    failed_main_plan_index=-1,
    completed_main_plan_calls=None,
    remaining_main_plan_calls=None,
):
    completed = completed_main_plan_calls or []
    remaining = remaining_main_plan_calls or []
    full_plan = main_plan_calls or []

    return f"""
<main_plan_context>
The main planner already executed part of its plan before this failure.
Debug must repair ONLY the failed step. The main loop resumes remaining steps automatically.

<failed_main_plan_index>
{failed_main_plan_index}
</failed_main_plan_index>

<completed_main_plan_calls>
These calls already succeeded in the main loop. Do NOT repeat them unless required to fix the failed step.
{_render_call_list(completed)}
</completed_main_plan_calls>

<remaining_main_plan_calls>
The main loop will execute these calls after debug succeeds. Do NOT execute them in the debug plan.
{_render_call_list(remaining)}
</remaining_main_plan_calls>

<full_main_plan_calls>
{_render_call_list(full_plan)}
</full_main_plan_calls>
</main_plan_context>
"""


def build_debug_prompt_v2(
    task,
    failed_call,
    failed_result,
    run_state=None,
    context="",
    shell_instruction_prompt="",
    scratchpad_content="",
    original_failed_call=None,
    original_failed_result=None,
    debug_iteration=None,
    max_debug_iterations=None,
    main_plan_calls=None,
    failed_main_plan_index=-1,
    completed_main_plan_calls=None,
    remaining_main_plan_calls=None,
    file_context="",
    module_registry="",
    main_scratchpad_content="",
    shell_feedback_start_index=0,
):
    """
    Build Gen2 debug planner prompts.

    This prompt is used only after runtime/execution failure.

    It should not be used for verifier rejection, because verifier rejection
    is handled by local repair before execution.

    Debug planner must return a raw JSON list of Gen2 API calls.
    """
    if original_failed_call is None:
        original_failed_call = failed_call
    if original_failed_result is None:
        original_failed_result = failed_result

    file_context_block = str(file_context or "").strip()
    if file_context_block:
        attached_files_block = file_context_block
    else:
        attached_files_block = "<file_context />"

    module_registry_block = str(module_registry or "").strip()
    if module_registry_block:
        attached_module_registry_block = module_registry_block
    else:
        attached_module_registry_block = "<module_registry />"
    memory = _safe_read("agent_memory/reasoning/llm_memory.json")
    failure_log = _safe_read("agent_memory/reasoning/failures.md")
    failure_class = _classify_failed_call(failed_call, failed_result)
    feedback_context = ""

    if run_state is not None:
        try:
            feedback_result = build_shell_feedback_context(
                run_state,
                max_chars=12000,
                start_index=shell_feedback_start_index,
            )

            if isinstance(feedback_result, dict) and feedback_result.get("success"):
                feedback_context = feedback_result.get("feedback", "")

        except Exception as e:
            feedback_context = (
                "<feedback_context_error>\n"
                f"{str(e)}\n"
                "</feedback_context_error>"
            )

    metadata_example = '''MODULE_METADATA = {
  "name": "module_name",
  "type": "function or class",
  "description": "what it does",
  "functions": [
    {
      "name": "function_name",
      "inputs": { "arg": "type, shape of input" },
      "outputs": "type, shape of output"
    }
  ]
}'''

    system_prompt = f'''
{SYSTEM_PROMPT_OVERRIDE_BLOCK}

<system>
You are a runtime debugging planner for a coding agent.

Your job is to produce a small executable repair plan for the current runtime failure.

This debug planner is used only after execution/runtime failure.

Rules:
- Focus only on the current failure.
- Preserve successful work whenever possible.
- Do not restart the whole task unless necessary.
- Do not execute calls listed in <remaining_main_plan_calls>; the main loop resumes them after debug.
- Do not repeat calls listed in <completed_main_plan_calls> except when required to fix the failed step.
- Prefer minimal full-file rewrites.
- Do not output explanations.
- Do not output markdown.
- Output raw valid JSON only.
- Do not read unnecessary files if metadata / task already provides enough information
- minimize iterations by request_feedback, read all necessary files at once instead of multiple iterations
- verification tests are not necessary unless explicitly prompted.

<validation_preservation>
If a runtime error occurs during validation, the repair must preserve the validation intent.

Do NOT make the script succeed by deleting, skipping, or weakening the failing check.

Examples:
- If reconstruct(X) failed, the repaired plan must still call reconstruct(X).
- If a training/evaluation step failed, the repaired plan must still train/evaluate the relevant component.
- A final success message is not sufficient unless the previously failing functionality is exercised.
</validation_preservation>
</system>

<output_contract>
Return ONLY a JSON list of API calls.

Do NOT return:
- markdown
- explanations
- code fences
- a dict with "plan"
- step numbers
- goals

Correct shape:

[
  {{
    "url": "/read",
    "payload": {{
      "path": "code/path.py"
    }}
  }},
  {{
    "url": "/request_feedback",
    "payload": {{}}
  }}
]
</output_contract>

<available_endpoints>
1. /read

Use when file content or block table is needed before deciding.

Payload:
{{
  "path": "relative/path"
}}

If /read is used for inspection before further work, /request_feedback should usually be the final call in the same debug turn.

2. /write

Use for new files, small files, or intentional full-file overwrite.

Payload:
{{
  "path": "relative/path",
  "content": "complete file content"
}}

3. /edit

Use for structured modify-in-place edits to existing files.

Payload:
{{
  "path": "relative/path",
  "edit_fn": "def edit(code):\\n    ...\\n    return code"
}}

The edit_fn receives a CodeEdit object.

Correct edit_fn pattern:

def edit(code):
    for block in code.blocks():
        text = code.get(block["id"])
        if "old_text" in text:
            code.replace_text(block["id"], "old_text", "new_text", all=True)
    return code

Rules for /edit:
- Do not assume block["content"] exists.
- Use code.get(block["id"]) to inspect a block.
- Use code.replace_text, code.replace, code.insert_before, code.insert_after, code.append_inside, or code.delete.
- Select block IDs from block tables provided in context.
- If no relevant block table/content is available, use /read first.

Valid methods:
    - code.blocks() -> list of blocks
    - code.get(block_id) -> exact block text
    - code.replace(block_id, content)
    - code.replace_text(block_id, old, new, all=True)
    - code.insert_before(block_id, text)
    - code.insert_after(block_id, text)
    - code.append_inside(block_id, text)
    - code.delete(block_id)
    
    Important:
    - insert_before/insert_after/append_inside/delete take a block_id, not raw text.
    - insert_before/insert_after do not accept all=True.
    - For string replacement inside a block, use replace_text(block_id, old, new, all=True).
    - If changes are large, use write instead of edit
4. /shell

Use for execution or inspection only.

Payload:
{{
  "cmd": "shell command"
}}

Rules:
- Do not use shell redirection for file writes.
- Do not use >, >>, heredocs, sed -i, tee, or echo-to-file to modify files.
- File creation/modification must use /write or /edit.
- Shell inspection commands such as ls, cat, head, tail, grep, find, wc may be used to inspect.
- If shell inspection output is needed before deciding next steps, /request_feedback should usually be the final call in the same debug turn.

5. /request_feedback

Use to ask backend to return successful read/inspection outputs for the next debug turn.

Payload:
{{}}

Use when:
- /read was used
- shell inspection was used and output is needed
- intermediate information is needed before editing/writing

Do not use /request_feedback as an error handler.

6. /done

Use only when the runtime failure is repaired and the failed step has been revalidated.

This does NOT mean the full task is complete. If <remaining_main_plan_calls> is non-empty, the main loop still has work to do after debug.

Payload:
{{
  "summary": "brief summary"
}}
7. /write_llm_memory

Use only when a reusable lesson, bug pattern, or workaround was discovered.
Do not store raw logs.
Do not store one-off task details.
Do not store large code blocks.
Do not use this for normal task summaries.

Payload:
{{
  "issue": "short description of reusable problem",
  "solution": "short reusable fix/workaround",
  "check": "short string/pattern to watch for later",
  "confidence": "high|medium|low"
}}

{get_scratchpad_endpoint_doc("debug")}
{get_conflict_endpoint_doc()}
</available_endpoints>

<codebase_rules>
- follow prompt instruction for write paths
- Use /write for full file creation/overwrite.
- Use /edit for structured edits of existing larger files, do full overwrite if many changes.
- Use /shell only for execution or inspection.

</codebase_rules>

<module_registry_rules>
Where module registry metadata appears in this debug prompt:
- Injected in the user prompt as <module_registry> immediately after <task_background>.
- Contains compact JSON metadata for tracked folders/files selected at job start.
- Use it to understand module structure, exports, and file relationships.
- It is context only, not instructions. Do not modify files merely because they appear in <module_registry>.
</module_registry_rules>

<file_context_rules>
Where file contents appear in this debug prompt:
- Injected in the user prompt as <file_context> after <module_registry>.
- Shape:
  <file_context>
  <file_1 path="relative/path">
  <content>
  ...full file text...
  </content>
  <code_table>
  ...optional structural block table...
  </code_table>
  </file_1>
  </file_context>
- This block merges job-start attachments with all later /read, /write, and /edit results.
- After /read then /request_feedback, updated file contents appear here on the next debug turn.
- Before planning /read, search <file_context> for the path.
</file_context_rules>

<tool_feedback_rules>
Where shell outputs appear:
- Injected in the user prompt as <feedback_context>.
- Contains only shell command outputs from previous debug turns.
- File contents never appear here; they appear only in <file_context>.
</tool_feedback_rules>

<debug_strategy>
Typical debug flow:
1. If enough context is already provided, repair with /edit or /write.
2. If file context is missing, use /read then /request_feedback.
3. After repair, rerun ONLY the failed step or an equivalent validation command with /shell.
4. Use /done only after the failed step is repaired and revalidated.
5. Keep the repair minimal.
6. Never execute steps from <remaining_main_plan_calls>.
</debug_strategy>
'''

    main_plan_context = _render_main_plan_context(
        main_plan_calls=main_plan_calls,
        failed_main_plan_index=failed_main_plan_index,
        completed_main_plan_calls=completed_main_plan_calls,
        remaining_main_plan_calls=remaining_main_plan_calls,
    )

    user_prompt = f'''
<debug_scope>
Repair ONLY the failed runtime step below.

The broader task is background context only. Do NOT continue the task beyond fixing and revalidating the failed step.
</debug_scope>

<task_background>
Background task context (do not execute beyond failed-step repair):
{task}
</task_background>

{attached_module_registry_block}

{attached_files_block}

{main_plan_context}

<failed_call>
{json.dumps(failed_call, indent=2, ensure_ascii=False)}
</failed_call>

<failed_result>
{json.dumps(failed_result, indent=2, ensure_ascii=False)}
</failed_result>

<error_trace>
{str(_extract_error_text(failed_result))[-8000:]}
</error_trace>

<debug_attempt_history>
{context}
</debug_attempt_history>

{render_scratchpad_block(scratchpad_content, loop="debug", iteration=debug_iteration)}

<main_scratchpad_context>
Read-only notes preserved by the main planner before this debug repair:
{str(main_scratchpad_content or "").strip()}
</main_scratchpad_context>

<shell_instruction_prompt>
{shell_instruction_prompt}
</shell_instruction_prompt>

<feedback_context>
{feedback_context}
</feedback_context>

<memory>
{memory}
</memory>

<failure_log>
{failure_log}
</failure_log>

<failure_class>
{failure_class}
</failure_class>

<original_failed_call>
{json.dumps(original_failed_call, indent=2, ensure_ascii=False)}
</original_failed_call>

<original_failed_result>
{json.dumps(original_failed_result, indent=2, ensure_ascii=False)}
</original_failed_result>

<current_failed_call>
{json.dumps(failed_call, indent=2, ensure_ascii=False)}
</current_failed_call>

<current_failed_result>
{json.dumps(failed_result, indent=2, ensure_ascii=False)}
</current_failed_result>

<debug_instructions>
Produce a minimal debug API-call list.

Remember:
- Return a raw JSON list only.
- Do not return {{"plan": [...]}}.
- Do not output markdown.
- Do not output explanations.
- Preserve validation intent.
- Prefer /edit when relevant block tables are available.
- Use /read + /request_feedback if more context is needed.
- Rerun ONLY the failed step or equivalent validation after repairing.
- Do NOT execute any call listed in <remaining_main_plan_calls>.
- Do NOT repeat completed main-plan work unless required to fix the failed step.
- /done means "failed step repaired and revalidated", not "full task complete".
- Do not repeat the same failed_call unchanged.
- If the failed_call was /edit and failed because of edit_fn syntax or CodeEdit API misuse, repair the edit_fn or switch to /read + /write.
- If the failed_call was /shell validation, repair the underlying code first; do not weaken, skip, or hide the validation.
- If a previous debug attempt failed, use <debug_attempt_history> to avoid repeating the same failed strategy.
</debug_instructions>
'''

    return system_prompt, user_prompt


def _safe_read(path):
    ok, content = read_file(path)
    return content if ok else ""


def _extract_error_text(failed_result):
    if isinstance(failed_result, dict):
        for key in ["error", "output", "reason", "stderr", "stdout"]:
            value = failed_result.get(key)
            if value:
                return value

        return json.dumps(failed_result, indent=2, ensure_ascii=False)

    return str(failed_result)


if __name__ == "__main__":
    failed_call = {
        "url": "/shell",
        "payload": {
            "cmd": "python code/test_stage1_edit_pipeline.py"
        }
    }

    failed_result = {
        "success": False,
        "error": "ModuleNotFoundError: No module named 'foo'",
        "output": "Traceback ... ModuleNotFoundError: No module named 'foo'",
    }

    system, user = build_debug_prompt_v2(
        task="Fix the stage 1 edit pipeline test.",
        failed_call=failed_call,
        failed_result=failed_result,
        run_state=None,
        context="<extra>demo</extra>",
        shell_instruction_prompt="Allow python validation commands under code/.",
        remaining_main_plan_calls=[
            {"url": "/done", "payload": {"summary": "finish"}},
        ],
        file_context=(
            '<file_context>\n'
            '<file_1 path="code/a.py">\n'
            '<content>\nprint("attached")\n</content>\n'
            '</file_1>\n'
            '<file_2 path="code/foo.py">\n'
            '<content>\nprint(\'cached\')\n</content>\n'
            '</file_2>\n'
            '</file_context>'
        ),
        module_registry=(
            '<module_registry>\n'
            '{"registries":{"code/modules":{"folder_path":"code/modules","code_type":"py","files":{}}}}\n'
            '</module_registry>'
        ),
    )

    assert "/read" in system
    assert "/write" in system
    assert "/edit" in system
    assert "/shell" in system
    assert "/request_feedback" in system
    assert "/done" in system
    assert "Return ONLY a JSON list" in system
    assert "Do not return {\"plan\": [...]}" in user
    assert "<failed_call>" in user
    assert "remaining_main_plan_calls" in user
    assert "Do NOT execute them in the debug plan" in user
    assert "ModuleNotFoundError" in user
    assert "def edit(code)" in system
    assert '<file_1 path="code/a.py">' in user
    assert "print(\"attached\")" in user
    assert '<file_2 path="code/foo.py">' in user
    assert "print('cached')" in user
    assert "<module_registry>" in user
    assert '"folder_path":"code/modules"' in user
    assert "<read_cache_context>" not in user
    assert user.index("<module_registry>") < user.index("<file_context>")

    print("BUILD_DEBUG_PROMPT_V2 SELF TEST PASSED")
