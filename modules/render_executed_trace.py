import json


MODULE_METADATA = {
    "name": "render_executed_trace",
    "type": "function",
    "description": "Render executed trace entries into compact XML-like text blocks for debug prompts.",
    "functions": [
        {
            "name": "render_executed_trace",
            "inputs": {
                "executed_trace": "list of execution trace dicts containing step, success, and output fields",
                "max_output_chars": "int maximum number of trailing output characters to include per executed step, default 2000"
            },
            "outputs": "str containing rendered executed_step blocks for debug prompt injection"
        }
    ]
}


def render_executed_trace(executed_trace, max_output_chars=2000):
    """
    Convert executed trace into compact text for debug prompt.
    Only executed steps are included.
    """

    parts = []

    for item in executed_trace:
        step = item.get("step", {})
        success = item.get("success")
        output = str(item.get("output", ""))

        action = step.get("action", "")

        parts.append(
            f"""
<executed_step>
<action>{action}</action>
<success>{success}</success>
<step>
{json.dumps(step, indent=2, ensure_ascii=False)}
</step>
<output>
{output[-max_output_chars:]}
</output>
</executed_step>
""".strip()
        )

    return "\n\n".join(parts)