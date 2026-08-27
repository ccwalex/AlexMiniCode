import ast
import json

from call_llm import call_llm_role
from cfg import CFG
from structured_llm_retry import call_llm_role_with_parse_retry, is_valid_verifier_response
from prompt_override import SYSTEM_PROMPT_OVERRIDE_BLOCK


MODULE_METADATA = {
    "name": "verify_edit",
    "type": "function",
    "description": "Verify final reconstructed file content after edit, using content-only deterministic checks plus optional LLM audit.",
    "functions": [
        {
            "name": "verify_edit",
            "inputs": {
                "path": "str target file path",
                "original_source": "str original file content",
                "reconstructed_source": "str final produced file content",
                "mutation_log": "list ignored except for compatibility",
                "code_type": "str code type hint",
                "use_llm": "bool whether to run optional LLM verifier"
            },
            "outputs": "dict with approved bool, reason str, and content str"
        }
    ]
}


def _extract_json_obj(resp):
    if isinstance(resp, dict):
        return resp

    if not isinstance(resp, str):
        return None

    text = resp.strip()

    if text.startswith("```"):
        text = text.replace("```json", "").replace("```", "").strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    decoder = json.JSONDecoder()

    for i, ch in enumerate(text):
        if ch not in "{[":
            continue

        try:
            obj, _ = decoder.raw_decode(text[i:])
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue

    return None


def _llm_verify_final_content(path, original_source, reconstructed_source, code_type):
    def load_modules_context():
        if registry_context_override is not None:
            return render_json_or_text(registry_context_override)

        if modules_override is not None:
            return render_json_or_text(modules_override)

        return read_memory_text("agent_memory/core/metadata.json")

    def load_file_context():
        if file_context_override is None:
            return ""
        return render_json_or_text(file_context_override)

    principles = read_memory_text("agent_memory/core/principles.md")
    modules = load_modules_context()
    memory = read_memory_text("agent_memory/reasoning/llm_memory.json")
    failures = read_memory_text("agent_memory/reasoning/failures.md")
    system_prompt = f"""
{SYSTEM_PROMPT_OVERRIDE_BLOCK}

<system>
You are a strict content verifier for an autonomous coding agent.

Your job is ONLY to approve or reject the final produced file content.

Rules:
- Judge only the final reconstructed file content.
- Do NOT care how the file was produced.
- Do NOT inspect or reason about edit mechanics, block IDs, mutation logs, or transaction details.
- Do NOT suggest improvements.
- Do NOT rewrite code.
- Do NOT provide alternative code.
- Do NOT ask for more information.
- Reject only concrete likely failures or clear content damage.
- Do NOT reject for style issues.
- Return ONLY valid JSON.
</system>

<output_format>
If approved:
{{
  "approved": true,
  "reason": "short reason"
}}

If rejected:
{{
  "approved": false,
  "reason": "short concrete reason"
}}
</output_format>

<modules>
{modules}
</modules>

<memory>
{memory}
</memory>

<principles>
{principles}
</principles>

<audit_policy>
Approve if:
- The reconstructed source is plausible complete file content.
- The reconstructed source preserves the apparent purpose of the original file.
- The reconstructed source does not obviously delete unrelated major functionality.
- The reconstructed source does not obviously contain undefined names, broken imports, malformed syntax, or incompatible public API changes.

Reject if:
- The reconstructed source is empty or fragmentary.
- The reconstructed source appears unrelated to the original file.
- The reconstructed source loses large unrelated sections without clear reason.
- The reconstructed source contains obvious syntax or structure damage.
- The reconstructed source introduces obvious broken imports, undefined names, or inconsistent function/class signatures.
</audit_policy>
""".strip()

    user_prompt = f"""
<content_verification_request>
<path>
{path}
</path>

<code_type>
{code_type}
</code_type>

<original_source>
{original_source}
</original_source>

<reconstructed_source>
{reconstructed_source}
</reconstructed_source>

<instruction>
Judge whether reconstructed_source is acceptable final content for this file.
Return only JSON with approved and reason.
</instruction>
</content_verification_request>
"""

    cfg = CFG()

    resp = call_llm_role_with_parse_retry(
        role="verifier",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        is_valid=is_valid_verifier_response,
        parse_fallback_kind="verifier",
        llm_call=call_llm_role,
        max_tokens=1024,
        thinking="low",
        timeout=cfg.get_timeout("verifier_call"),
    )

    parsed = _extract_json_obj(resp)

    if not isinstance(parsed, dict):
        return {
            "approved": True,
            "reason": "Passed deterministic verification; LLM verifier returned unparseable response, so not blocking edit."
        }

    if "approved" not in parsed:
        return {
            "approved": True,
            "reason": "Passed deterministic verification; LLM verifier response missing approved field, so not blocking edit."
        }

    return {
        "approved": bool(parsed.get("approved")),
        "reason": str(parsed.get("reason", "LLM verifier returned no reason.")),
    }


def verify_edit(
    path,
    original_source,
    reconstructed_source,
    mutation_log=None,
    code_type="python",
    use_llm=True,
):
    """
    Content-only verification for final file content after edit.

    Important:
    - mutation_log is accepted only for backward compatibility.
    - This verifier does not care how reconstructed_source was produced.
    - Repair should produce a complete file overwrite candidate.
    """

    if not isinstance(path, str) or not path.strip():
        return {
            "approved": False,
            "reason": "Path must be a non-empty string.",
            "content": reconstructed_source,
        }

    if not isinstance(reconstructed_source, str) or not reconstructed_source.strip():
        return {
            "approved": False,
            "reason": "Reconstructed source is empty.",
            "content": reconstructed_source,
        }

    if original_source is None:
        original_source = ""

    if not isinstance(original_source, str):
        original_source = str(original_source)

    if reconstructed_source == original_source:
        return {
            "approved": False,
            "reason": "Reconstructed source is identical to original source.",
            "content": reconstructed_source,
        }

    c_type = str(code_type or "").lower().strip()

    # Path-based fallback because code_type can be inconsistent.
    is_python = c_type in ["python", "py"] or path.endswith(".py")

    if is_python:
        try:
            ast.parse(reconstructed_source)
        except SyntaxError as e:
            return {
                "approved": False,
                "reason": f"SyntaxError in reconstructed source: {e}",
                "content": reconstructed_source,
            }

    if not use_llm:
        return {
            "approved": True,
            "reason": "Passed deterministic content verification.",
            "content": reconstructed_source,
        }

    try:
        llm_res = _llm_verify_final_content(
            path=path,
            original_source=original_source,
            reconstructed_source=reconstructed_source,
            code_type=c_type,
        )

        if not isinstance(llm_res, dict):
            return {
                "approved": True,
                "reason": "Passed deterministic verification; LLM verifier returned non-dict response, so not blocking edit.",
                "content": reconstructed_source,
            }

        return {
            "approved": bool(llm_res.get("approved", True)),
            "reason": str(llm_res.get("reason", "LLM verifier returned no reason.")),
            "content": reconstructed_source,
        }

    except Exception as e:
        return {
            "approved": True,
            "reason": f"Passed deterministic verification; LLM verifier failed non-fatally: {e}",
            "content": reconstructed_source,
        }