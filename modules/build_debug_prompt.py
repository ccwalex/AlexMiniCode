import json

from read_file import read_file
from build_temp_modules_registry import build_temp_modules_registry
from render_executed_trace import render_executed_trace


MODULE_METADATA = {
    "name": "build_debug_prompt",
    "type": "function",
    "description": "Build system and user prompts for the runtime debug planner after an execution failure.",
    "functions": [
        {
            "name": "build_debug_prompt",
            "inputs": {
                "task": "str original user task",
                "failed_step": "dict failed execution step",
                "error_output": "str or object containing runtime error output",
                "executed_trace": "list of execution trace dicts from already executed steps",
                "context": "str optional additional debug context"
            },
            "outputs": "tuple (system_prompt, user_prompt) for debug planner model"
        }
    ]
}


def build_debug_prompt(
    task,
    failed_step,
    error_output,
    executed_trace,
    context="",
):
    def safe_read(path):
        ok, content = read_file(path)
        return content if ok else ""

    metadata_example = '''MODULE_METADATA = {
  "name": "module_name",
  "type": "function or class",
  "description": "what it does",
  "functions": [
    {
      "name": "function_name",
      "inputs": { "arg": "type" },
      "outputs": "type"
    }
  ]
}'''

    principles = safe_read("agent_memory/core/principles.md")
    memory = safe_read("agent_memory/reasoning/llm_memory.json")
    failure_log = safe_read("agent_memory/reasoning/failures.md")

    temp_modules = build_temp_modules_registry(executed_trace)
    executed_text = render_executed_trace(executed_trace)

    system_prompt = f'''
<system>
You are a runtime debugging planner for an autonomous coding agent.

Your job is to produce a small executable debug plan that repairs the current runtime failure.

Rules:
- Focus only on the current failure.
- Preserve successful work whenever possible.
- Do not restart the whole task unless necessary.
- Prefer minimal full-file rewrites.
- Do not output explanations.
- Do not output markdown.
- Output raw valid JSON only.

<validation_preservation>
If a runtime error occurs during validation, the repair must preserve the validation intent.

Do NOT make the script succeed by deleting, skipping, or weakening the failing check.

Examples:
- If reconstruct(X) failed, the repaired plan must still call reconstruct(X).
- If a training/evaluation step failed, the repaired plan must still train/evaluate the relevant component.
- A final success message is not sufficient unless the previously failing functionality is exercised.
</validation_preservation>
</system>

<available_modules>
{json.dumps(temp_modules, indent=2, ensure_ascii=False)}

Use existing modules whenever possible.
</available_modules>

<available_operations>
Allowed actions:

1. read_file
Use read_file only when necessary file content is not already provided.

If read_file is used, request_feedback must be the final action in that plan.

Do not read the same file twice in one task.

If attached files are present, use them directly.
{{
  "action": "read_file",
  "path": "relative/path"
}}

2. write_file

Use this for ALL file creation and modification.

{{
  "action": "write_file",
  "path": "relative/path",
  "content": "full file content"
}}

3. run_shell

Use this for execution or inspection only.

{{
  "action": "run_shell",
  "cmd": "the bash command to execute"
}}

4. write_llm_memory

Use this only when a reusable lesson or workaround has been discovered.
Do not store raw logs or one-off errors.

{{
  "action": "write_llm_memory",
  "issue": "description of the problem or bug encountered",
  "solution": "how to solve or work around the issue",
  "check": "a short string to check for in future commands to avoid this",
  "confidence": "high, medium, or low"
}}

5. request_feedback

Use this only when intermediate tool outputs are needed before continuing.
If used, it must be the final action in the plan.
Remember to use this for any read_file actions

{{
  "action": "request_feedback"
}}
</available_operations>

<codebase_rules>
- Main scripts must be placed in code/.
- Reusable modules must be placed in code/modules/.
- Each code/modules/*.py file must include top-level MODULE_METADATA.
- Module files must be fully overwritten when changed.
- Do not output diffs or line patches.
- Use write_file for file creation/modification.
- Use run_shell only for execution or inspection.
- Do not use shell redirection for file writes.

Metadata format:

{metadata_example}
</codebase_rules>

<output_format>
Return ONLY valid JSON.

Use:

{{
  "plan": [
    {{
      "action": "read_file",
      "path": "code/path.py"
    }},
    {{
      "action": "write_file",
      "path": "code/path.py",
      "content": "complete file content"
    }},
    {{
      "action": "run_shell",
      "cmd": "python code/test.py"
    }}
  ]
}}

Rules:
- Always return a JSON object with a "plan" list.
- Each item in "plan" must be one executable action.
- If request_feedback is used, it must be the final action.
</output_format>
'''

    user_prompt = f'''
<debug_task>
Repair the runtime failure below.

Original task:
{task}
</debug_task>

<previous_executed_steps>
{executed_text}
</previous_executed_steps>

<failed_step>
{json.dumps(failed_step, indent=2, ensure_ascii=False)}
</failed_step>

<error_trace>
{str(error_output)[-6000:]}
</error_trace>

<debug_context>
{context}
</debug_context>

<llm_memory>
{memory}
</llm_memory>

<failure_log>
{failure_log}
</failure_log>

<debug_instructions>
Produce a minimal debug plan.

Typical flow:
1. read relevant files if needed;
2. request_feedback if inspection is needed;
3. fully overwrite broken file(s);
4. rerun the failed command or equivalent validation command;
5. write LLM memory only if this is a reusable cross-task lesson.
</debug_instructions>
'''

    return system_prompt, user_prompt