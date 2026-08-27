import json

from read_file import read_file
from call_planner import call_planner
from clean_python_content import clean_python_content


MODULE_METADATA = {
    "name": "repair_write_step",
    "type": "function",
    "description": "Repair rejected write_file content by asking the planner model to return only complete repaired file content.",
    "functions": [
        {
            "name": "repair_write_step",
            "inputs": {
                "step": "dict representing a rejected write_file action",
                "rejection_reason": "str reason returned by verifier for rejecting the write_file action",
                "modules_override": "dict/list or None; optional temporary module registry for same-plan module dependencies"
            },
            "outputs": "dict with success bool and either repaired content str or failure reason str"
        }
    ]
}


def repair_write_step(path, content, rejection_reason, modules_override=None):
    """
    Repair a rejected write_file step.

    The repair model must return:
    {
      "content": "complete repaired file content"
    }

    Only step["content"] should be replaced by the caller.

    modules_override:
        Optional temporary module registry dict.
        Useful when repairing a file that imports modules planned in the
        same active_plan but not yet written to persistent modules.json.
    """

    def safe_read(path):
        ok, content = read_file(path)
        return content if ok else ""
    """
    if step.get("action") != "write_file":
        return {
            "success": False,
            "reason": "repair_write_step only supports write_file actions",
        }

    if "content" not in step:
        return {
            "success": False,
            "reason": "write_file step missing content",
        }
    """
    

    principles = safe_read("agent_memory/core/principles.md")
    memory = safe_read("agent_memory/reasoning/llm_memory.json")


    system_repair = f'''
<system>
You are a coding assistant that repairs rejected file content.

Your task:
- Repair ONLY the provided file content.
- Return ONLY valid JSON.
- Do NOT return markdown.
- Do NOT return explanations.
- Do NOT return a plan.
- Do NOT output tool calls.
- Do NOT change the file path.
- Do NOT introduce unrelated changes.

Output contract:
- Return exactly one JSON object.
- The JSON object must contain exactly one key: "content".
- "content" must be the complete repaired file content as a string.

Follow the principles, known modules, and learned lessons strictly.

Important module registry rule:
- The known_modules block may include temporary modules planned in the same task.
- Treat those modules as available for repairing imports and module usage.
- If repairing a module file itself, the provided repaired content is the new source of truth.
- Do not reject or preserve an old module interface only because it appears in known_modules.
</system>

<principles>
{principles}
</principles>

<learned_lessons>
{memory}
</learned_lessons>
'''

    user_repair = f'''
<repair_request>
The following write_file content was rejected by the verifier.

<path>
{path}
</path>

<rejection_reason>
{rejection_reason}
</rejection_reason>

<original_content>
{content}
</original_content>

<repair_constraints>
- Preserve the original file purpose.
- Preserve compatible public APIs unless the rejection requires changing them.
- If repairing imports, ensure imported names match known modules when possible.
- Return the full repaired file content, not a diff.
</repair_constraints>

<output_format>
Return ONLY:

{{
  "content": "complete repaired file content"
}}
</output_format>
</repair_request>
'''

    repaired = call_planner(
        system_repair,
        user_repair,
        max_tokens=8192,
    )

    if not isinstance(repaired, dict):
        return {
            "success": False,
            "reason": f"repair model returned non-dict response: {str(repaired)[:300]}",
        }

    if "content" not in repaired:
        return {
            "success": False,
            "reason": f"repair model response missing content: {str(repaired)[:300]}",
        }

    repaired_content = repaired["content"]

    if not isinstance(repaired_content, str):
        return {
            "success": False,
            "reason": "repair model content is not a string",
        }
    
    #repaired_content = clean_python_content(repaired_content).strip()

    if not repaired_content:
        return {
            "success": False,
            "reason": "repair model returned empty content",
        }

    return {
        "success": True,
        "content": repaired_content,
    }