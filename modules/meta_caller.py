MODULE_METADATA = {
    "name": "meta_caller",
    "type": "function",
    "description": "Calls the appropriate metadata prompt builder based on inferred code type and invokes LLM with meta_writer role config.",
    "functions": [
        {
            "name": "meta_caller",
            "inputs": {
                "path": "str",
                "content": "str"
            },
            "outputs": "dict"
        }
    ]
}

from infer_code_type import infer_code_type
from build_py_meta_prompt import build_py_meta_prompt
from build_html_meta_prompt import build_html_meta_prompt
from build_react_tsx_meta_prompt import build_react_tsx_meta_prompt
from build_typescript_meta_prompt import build_typescript_meta_prompt
from call_llm import call_llm_role
from cfg import CFG
from meta_writer import sanitize_module_metadata


def meta_caller(path: str, content: str) -> dict:
    code_type = infer_code_type(path, content)
    if not code_type:
        return {"error": "Could not infer code type"}

    if code_type == "py":
        system_prompt, user_prompt = build_py_meta_prompt(content)
    elif code_type == "html":
        system_prompt, user_prompt = build_html_meta_prompt(content)
    elif code_type == "react":
        system_prompt, user_prompt = build_react_tsx_meta_prompt(content)
    elif code_type == "ts":
        system_prompt, user_prompt = build_typescript_meta_prompt(content)
    else:
        return {"error": f"Unsupported code type: {code_type}"}

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    cfg = CFG()

    raw = call_llm_role(
        role="meta_writer",
        messages=messages,
        timeout=cfg.get_timeout("planner_call"),
    )
    metadata = sanitize_module_metadata(raw, path=path)
    if not metadata.get("name"):
        return {"error": "Could not parse module metadata from LLM response"}
    return metadata
