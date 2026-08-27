from call_llm import call_llm_role
from cfg import CFG


MODULE_METADATA = {
    "name": "call_planner",
    "type": "function",
    "description": "Call the planner LLM with system and user prompts using the local_repair role config.",
    "functions": [
        {
            "name": "call_planner",
            "inputs": {
                "system_prompt": "str system prompt",
                "user_prompt": "str user prompt",
                "max_tokens": "int maximum output tokens, defaults to role config",
                "thinking": "str reasoning effort or thinking level, defaults to role config",
                "provider": "str or None optional provider override",
                "model": "str or None optional model override",
                "gemini_config": "dict or None optional Gemini-specific config override"
            },
            "outputs": "dict JSON response returned by configured LLM backend"
        }
    ]
}


def call_planner(
    system_prompt,
    user_prompt,
    max_tokens=None,
    thinking=None,
    provider=None,
    model=None,
    gemini_config=None,
):
    messages = [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "user",
            "content": user_prompt,
        },
    ]

    return call_llm_role(
        role="local_repair",
        messages=messages,
        max_tokens=max_tokens,
        thinking=thinking,
        model=model,
        provider=provider,
        gemini_config=gemini_config,
        timeout=CFG.get_timeout("planner_call"),
    )
