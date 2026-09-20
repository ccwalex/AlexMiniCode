import hashlib
import json

from build_write_verifier_prompt import build_write_verifier_prompt
from call_llm import call_llm_role
from opencode_session import universal_session
from structured_llm_retry import call_llm_role_with_parse_retry, is_valid_verifier_response
from deterministic_code_checker import deterministic_code_check
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
                "outputs": "dict with approved bool, reason, content, content_hash, and optional metadata"
        }
    ]
}

def _llm_verify_write(path, content, modules_override=None):
    step = {
        "action": "write_file",
        "path": path,
        "content": content,
    }
    sys_prompt, usr_prompt = build_write_verifier_prompt(
        step,
        modules_override=modules_override,
    )
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": usr_prompt},
    ]
    return call_llm_role_with_parse_retry(
        role="verifier",
        messages=messages,
        is_valid=is_valid_verifier_response,
        parse_fallback_kind="verifier",
        llm_call=call_llm_role,
        timeout=CFG().get_timeout("verifier_call"),
        session_id=universal_session("verifier", "write"),
    )


def _verify_mode(llm_used):
    return "llm" if llm_used else "deterministic"


def _parse_llm_write_decision(resp, content, content_hash, metadata):
    if isinstance(resp, dict) and "approved" in resp:
        if not resp.get("approved", True):
            return {
                "approved": False,
                "reason": resp.get("reason", "LLM rejected without specific reason."),
                "content": content,
                "content_hash": content_hash,
                "metadata": metadata,
                "verify_mode": _verify_mode(True),
            }
        return None

    if isinstance(resp, dict):
        text = json.dumps(resp)
        if '"approved": false' in text.lower() or "'approved': false" in text.lower():
            return {
                "approved": False,
                "reason": "LLM rejected the write step (parsed from raw output).",
                "content": content,
                "content_hash": content_hash,
                "metadata": metadata,
                "verify_mode": _verify_mode(True),
            }
    return None


def verify_write(path, content, modules_override=None, read_cache=None, use_llm=False):
    if not isinstance(path, str) or not path.strip():
        return {
            "approved": False,
            "reason": "Path must be a non-empty string.",
            "content": content,
            "content_hash": None,
            "metadata": None,
            "verify_mode": _verify_mode(False),
        }
    
    if not isinstance(content, str) or not content.strip():
        return {
            "approved": False,
            "reason": "Content must be a non-empty string.",
            "content": content,
            "content_hash": None,
            "metadata": None,
            "verify_mode": _verify_mode(False),
        }
    
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    metadata = None

    det = deterministic_code_check(path, content)
    if not det.get("approved") and not det.get("escalate"):
        return {
            "approved": False,
            "reason": det.get("reason", "Deterministic code check failed."),
            "content": content,
            "content_hash": content_hash,
            "metadata": metadata,
            "checker_status": det.get("status"),
            "verify_mode": _verify_mode(False),
        }

    force_llm = bool(det.get("escalate")) or det.get("status") == "uncovered"
    llm_used = False
    if use_llm or force_llm:
        llm_used = True
        try:
            resp = _llm_verify_write(path, content, modules_override=modules_override)
            rejected = _parse_llm_write_decision(resp, content, content_hash, metadata)
            if rejected:
                return rejected
        except Exception:
            if force_llm:
                return {
                    "approved": False,
                    "reason": det.get(
                        "reason",
                        "Deterministic checker escalated but LLM verifier failed.",
                    ),
                    "content": content,
                    "content_hash": content_hash,
                    "metadata": metadata,
                    "checker_status": det.get("status"),
                    "verify_mode": _verify_mode(True),
                }
            
    return {
        "approved": True,
        "reason": "Passed all deterministic checks." if not llm_used else "Passed LLM verification.",
        "content": content,
        "content_hash": content_hash,
        "metadata": metadata,
        "verify_mode": _verify_mode(llm_used),
    }

if __name__ == "__main__":
    import sys
    from unittest.mock import patch

    _checker = sys.modules["deterministic_code_checker"]

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

    class _FakeStatus:
        def __init__(self, value):
            self.value = value

    class _FakeResult:
        def __init__(self, status, findings=None):
            self.status = _FakeStatus(status)
            self.findings = findings or []

    def _fake_ok(source, path="<string>", **kwargs):
        return _FakeResult("ok")

    def _fake_error(source, path="<string>", **kwargs):
        return _FakeResult(
            "error",
            [{"line": 1, "code": "SYNTAX_ERROR", "message": "invalid syntax"}],
        )

    def _fake_uncovered(source, path="<string>", **kwargs):
        return _FakeResult(
            "uncovered",
            [{"line": 1, "code": "UNCOVERED_CALL", "message": "unknown API"}],
        )

    with patch.object(_checker, "_get_check_source", return_value=_fake_ok):
        res1 = verify_write("code/modules/example_verify_write.py", valid_module, use_llm=False)
        assert res1["approved"] is True
        assert res1["content_hash"] is not None
        assert res1["metadata"] is None

        missing_meta = """def dummy_func():
    pass
"""
        res3 = verify_write("code/modules/missing.py", missing_meta, use_llm=False)
        assert res3["approved"] is False

        res4 = verify_write("code/example.py", missing_meta, use_llm=False)
        assert res4["approved"] is True

    with patch.object(_checker, "_get_check_source", return_value=_fake_error):
        invalid_python = """def foo(
"""
        res2 = verify_write("code/modules/bad.py", invalid_python, use_llm=False)
        assert res2["approved"] is False
        assert res2.get("checker_status") == "error"

    _verify_write = sys.modules[__name__]
    with patch.object(_checker, "_get_check_source", return_value=_fake_uncovered):
        with patch.object(
            _verify_write,
            "_llm_verify_write",
            return_value={"approved": True, "reason": "ok"},
        ):
            escalated = verify_write("code/example.py", "x = mystery()\n", use_llm=False)
            assert escalated["approved"] is True

        with patch.object(
            _verify_write,
            "_llm_verify_write",
            return_value={"approved": False, "reason": "bad"},
        ):
            rejected = verify_write("code/example.py", "x = mystery()\n", use_llm=False)
            assert rejected["approved"] is False

    print("VERIFY_WRITE SELF TEST PASSED")
