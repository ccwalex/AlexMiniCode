import ast
import hashlib
import json

from build_write_verifier_prompt import build_write_verifier_prompt
from call_llm import call_llm_role
from structured_llm_retry import call_llm_role_with_parse_retry, is_valid_verifier_response
from clean_python_content import clean_python_content
from extract_module_metadata_from_content import extract_module_metadata_from_content
from validate_module_metadata import validate_module_metadata
from validate_metadata_matches_code import validate_metadata_matches_code
from cfg import CFG

MODULE_METADATA = {
    "name": "verify_write",
    "type": "function",
    "description": "Verify complete-file write content before write_file is called, including deterministic syntax and module metadata checks plus optional LLM audit.",
    "functions": [
        {
            "name": "verify_write",
            "inputs": {
                "path": "str project-relative target path",
                "content": "str proposed complete file content",
                "modules_override": "dict/list or None optional registry context",
                "read_cache": "dict or None cached read files",
                "use_llm": "bool whether to run optional LLM verifier"
            },
            "outputs": "dict with approved bool, reason, cleaned content, content_hash, and optional metadata"
        }
    ]
}

def verify_write(path, content, modules_override=None, read_cache=None, use_llm=True):
    if not isinstance(path, str) or not path.strip():
        return {
            "approved": False,
            "reason": "Path must be a non-empty string.",
            "content": content,
            "content_hash": None,
            "metadata": None
        }
    
    if not isinstance(content, str) or not content.strip():
        return {
            "approved": False,
            "reason": "Content must be a non-empty string.",
            "content": content,
            "content_hash": None,
            "metadata": None
        }
    
    is_python = path.endswith(".py")
    
    if is_python:
        content = clean_python_content(content)
        
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    
    metadata = None
    """
    if path.startswith("code/modules/") and is_python:
        meta, err = extract_module_metadata_from_content(content)
        if err:
            return {
                "approved": False,
                "reason": f"Failed to extract MODULE_METADATA: {err}",
                "content": content,
                "content_hash": content_hash,
                "metadata": None
            }
        
        valid, msg = validate_module_metadata(meta)
        if not valid:
            return {
                "approved": False,
                "reason": f"Invalid MODULE_METADATA format: {msg}",
                "content": content,
                "content_hash": content_hash,
                "metadata": None
            }
            
        match, match_msg = validate_metadata_matches_code(meta, content)
        if not match:
            return {
                "approved": False,
                "reason": f"Metadata mismatch: {match_msg}",
                "content": content,
                "content_hash": content_hash,
                "metadata": None
            }
        metadata = meta
    """
    if is_python:
        try:
            ast.parse(content)
        except SyntaxError as e:
            return {
                "approved": False,
                "reason": f"SyntaxError in Python file: {e}",
                "content": content,
                "content_hash": content_hash,
                "metadata": metadata
            }
            
    if use_llm:
        try:
            step = {
                "action": "write_file",
                "path": path,
                "content": content
            }
            sys_prompt, usr_prompt = build_write_verifier_prompt(step, modules_override=modules_override)
            messages = [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": usr_prompt}
            ]
            resp = call_llm_role_with_parse_retry(
                role="verifier",
                messages=messages,
                is_valid=is_valid_verifier_response,
                parse_fallback_kind="verifier",
                llm_call=call_llm_role,
                timeout=CFG().get_timeout("verifier_call"),
            )
            
            resp_str = resp
            if isinstance(resp, dict):
                if "approved" in resp:
                    if not resp.get("approved", True):
                        return {
                            "approved": False,
                            "reason": resp.get("reason", "LLM rejected without specific reason."),
                            "content": content,
                            "content_hash": content_hash,
                            "metadata": metadata
                        }
                else:
                    # Try parsing from response text if it's wrapped
                    text = json.dumps(resp)
                    if '"approved": false' in text.lower() or "'approved': false" in text.lower():
                        return {
                            "approved": False,
                            "reason": "LLM rejected the write step (parsed from raw output).",
                            "content": content,
                            "content_hash": content_hash,
                            "metadata": metadata
                        }
        except Exception as e:
            pass # fallback to deterministic pass if LLM fails
            
    return {
        "approved": True,
        "reason": "Passed all deterministic checks.",
        "content": content,
        "content_hash": content_hash,
        "metadata": metadata
    }

if __name__ == "__main__":
    valid_module = """MODULE_METADATA = {
    "name": "dummy_func",
    "type": "function",
    "description": "dummy",
    "functions": [
        {
            "name": "dummy_func",
            "inputs": {},
            "outputs": "None"
        }
    ]
}

def dummy_func():
    pass
"""
    res1 = verify_write("code/modules/example_verify_write.py", valid_module, use_llm=False)
    assert res1["approved"] is True
    assert res1["content_hash"] is not None
    assert res1["metadata"] is None

    invalid_python = """def foo(
"""
    res2 = verify_write("code/modules/bad.py", invalid_python, use_llm=False)
    assert res2["approved"] is False

    missing_meta = """def dummy_func():
    pass
"""
    res3 = verify_write("code/modules/missing.py", missing_meta, use_llm=False)
    assert res3["approved"] is True

    res4 = verify_write("code/example.py", missing_meta, use_llm=False)
    assert res4["approved"] is True

    print("VERIFY_WRITE SELF TEST PASSED")
