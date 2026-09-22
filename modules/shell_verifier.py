import json
import re
from build_shell_verifier_prompt import build_shell_verifier_prompt
from call_llm import call_llm_role
from opencode_session import session_for
from structured_llm_retry import call_llm_role_with_parse_retry, is_valid_verifier_response
from prompt_override import SYSTEM_PROMPT_OVERRIDE_BLOCK

MODULE_METADATA = {
    "name": "shell_verifier",
    "type": "function",
    "description": "Verify shell commands before execution using deterministic hard-deny checks. LLM audit code is retained but disabled by default.",
    "functions": [
        {
            "name": "shell_verifier",
            "inputs": {
                "cmd": "str proposed shell command",
                "instruction_prompt": "str raw permission/safety instructions for shell verification",
                "use_llm": "bool whether to run optional LLM audit; defaults to False"
            },
            "outputs": "dict with approved bool, reason, optional normalized command, inspection flag, and risk level"
        }
    ]
}

_NULL_SINK_RE = re.compile(r"^/dev/null$", re.IGNORECASE)
_REDIRECT_TARGET_RE = re.compile(r"(?:^|[^\d])(\d*)>{1,2}\s*([^\s|;&]+)")
_BASH_NULL_REDIRECT_RE = re.compile(r"&>\s*([^\s|;&]+)")


def _is_null_sink_target(target):
    if not isinstance(target, str):
        return False
    target = target.strip()
    if target in {"&1", "&2"}:
        return True
    return bool(_NULL_SINK_RE.match(target))


def is_null_sink_redirection_only(cmd):
    """True when shell redirection only discards output (e.g. > /dev/null, 2>/dev/null)."""
    if not isinstance(cmd, str) or not cmd.strip():
        return True

    if "| tee" in cmd or "|tee" in cmd:
        return False

    if ">" not in cmd and ">>" not in cmd:
        return True

    saw_redirection = False
    for match in _REDIRECT_TARGET_RE.finditer(cmd):
        saw_redirection = True
        if not _is_null_sink_target(match.group(2)):
            return False

    for match in _BASH_NULL_REDIRECT_RE.finditer(cmd):
        saw_redirection = True
        if not _is_null_sink_target(match.group(1)):
            return False

    return saw_redirection


def has_shell_redirection(cmd):
    if "| tee" in cmd or "|tee" in cmd:
        return True
    if ">" not in cmd and ">>" not in cmd:
        return False
    return not is_null_sink_redirection_only(cmd)

def is_safe_inspection_command(cmd):
    safe_starts = ["ls ", "find ", "grep ", "rg ", "cat ", "head ", "tail ", "wc ", "pwd", "tree"]
    cmd_strip = cmd.strip()
    if cmd_strip in ["ls", "pwd", "tree"]:
        return True
    for start in safe_starts:
        if cmd_strip.startswith(start):
            if not has_shell_redirection(cmd_strip) and "rm " not in cmd_strip:
                return True
    return False

def is_obviously_dangerous_shell(cmd):
    cmd_lower = cmd.lower()
    dangerous_patterns = [
        r"rm\s+-rf\s+/",
        r"\bsudo\b",
        r"\bmkfs\b",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bdd\s+if=",
        r"chmod\s+-r\s+777\s+/"
    ]
    for pattern in dangerous_patterns:
        if re.search(pattern, cmd_lower):
            return True
            
    system_roots = ["/etc", "/bin", "/usr", "/var"]
    for root in system_roots:
        if root in cmd:
            if not is_safe_inspection_command(cmd):
                return True
                
    return False

def is_project_local_validation_command(cmd):
    validations = ["python code/", "pytest", "npm test", "npm run", "node "]
    cmd_strip = cmd.strip()
    for v in validations:
        if cmd_strip.startswith(v):
            return True
    return False

def classify_shell_command(cmd):
    if is_obviously_dangerous_shell(cmd):
        return "dangerous"
    if is_safe_inspection_command(cmd):
        return "inspection"
    if is_project_local_validation_command(cmd):
        return "validation"
    if has_shell_redirection(cmd):
        return "mutation"
    
    mutations = ["rm ", "mv ", "cp ", "touch ", "mkdir ", "chmod ", "chown "]
    for m in mutations:
        if cmd.strip().startswith(m):
            return "mutation"
            
    return "unknown"

def parse_llm_shell_decision(raw):
    if isinstance(raw, dict):
        return raw
    
    if isinstance(raw, str):
        try:
            start = raw.find('{')
            end = raw.rfind('}')
            if start != -1 and end != -1:
                return json.loads(raw[start:end+1])
        except Exception:
            pass
            
    return None

def shell_verifier(cmd, instruction_prompt="", use_llm=False):
    if not isinstance(cmd, str) or not cmd.strip():
        return {
            "approved": False,
            "reason": "Command is empty or invalid.",
            "command": None,
            "is_inspection": False,
            "risk_level": "low"
        }
        
    cmd = cmd.strip()
    classification = classify_shell_command(cmd)
    
    is_inspection = classification == "inspection"
    risk_level = "high" if classification in ["dangerous", "mutation"] else ("medium" if classification == "unknown" else "low")
    
    if classification == "dangerous":
        return {
            "approved": False,
            "reason": "Command matches deterministic hard-deny patterns.",
            "command": None,
            "is_inspection": False,
            "risk_level": "high"
        }
        
    prompt_lower = instruction_prompt.lower() if isinstance(instruction_prompt, str) else ""
    allows_shell_writes = (
        "allow shell file writes" in prompt_lower
        and "do not allow shell file writes" not in prompt_lower
    )
    if has_shell_redirection(cmd) and not allows_shell_writes:
         return {
            "approved": False,
            "reason": "Shell redirection used without explicit permission to write files.",
            "command": None,
            "is_inspection": False,
            "risk_level": "high"
        }

    if not use_llm:
        return {
            "approved": True,
            "reason": "Passed deterministic shell checks; LLM audit disabled.",
            "command": cmd,
            "is_inspection": is_inspection,
            "risk_level": risk_level
        }
        
    step = {"action": "run_shell", "cmd": cmd}
    try:
        sys_prompt, user_prompt = build_shell_verifier_prompt(
            step=step,
            original_prompt="",
            permission_context=instruction_prompt
        )
    except Exception:
        sys_prompt = f"""{SYSTEM_PROMPT_OVERRIDE_BLOCK}

You are a shell command verifier.
"""
        user_prompt = f"Verify this command:\n{cmd}\n"
        
    extra_instructions = f"""
<shell_permission_instructions>
{instruction_prompt}
</shell_permission_instructions>

<shell_verifier_policy>
- Follow shell_permission_instructions when deciding task-specific permissions.
- Still reject clearly destructive commands.
- If a safer replacement command is appropriate, return it.
- Return JSON only with keys: approved (bool), reason (str), command (str or null).
</shell_verifier_policy>
"""
    user_prompt += "\n" + extra_instructions
    
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt}
    ]
    
    try:
        response = call_llm_role_with_parse_retry(
            role="verifier",
            messages=messages,
            is_valid=is_valid_verifier_response,
            parse_fallback_kind="verifier",
            llm_call=call_llm_role,
            max_tokens=500,
            thinking="low",
            timeout=60,
            session_id=session_for("verifier", "shell"),
        )
        
        parsed = parse_llm_shell_decision(response)
        if parsed and "approved" in parsed:
            return {
                "approved": bool(parsed.get("approved")),
                "reason": parsed.get("reason", "LLM decision"),
                "command": parsed.get("command", cmd),
                "is_inspection": is_inspection,
                "risk_level": risk_level
            }
            
    except Exception as e:
        if classification in ["inspection", "validation"]:
            return {
                "approved": True,
                "reason": f"LLM verification failed, but command is known low-risk. Error: {str(e)}",
                "command": cmd,
                "is_inspection": is_inspection,
                "risk_level": risk_level
            }
        else:
            return {
                "approved": False,
                "reason": f"LLM verification failed and command is not guaranteed safe. Error: {str(e)}",
                "command": None,
                "is_inspection": is_inspection,
                "risk_level": risk_level
            }
            
    if classification in ["inspection", "validation"]:
        return {
            "approved": True,
            "reason": "LLM verification parsing failed, but command is known low-risk.",
            "command": cmd,
            "is_inspection": is_inspection,
            "risk_level": risk_level
        }
        
    return {
        "approved": False,
        "reason": "LLM verification parsing failed and command is not guaranteed safe.",
        "command": None,
        "is_inspection": is_inspection,
        "risk_level": risk_level
    }

if __name__ == "__main__":
    import sys
    sys.modules['build_shell_verifier_prompt'] = type('Mock', (object,), {
        'build_shell_verifier_prompt': lambda step, original_prompt, permission_context: ("sys", "user")
    })
    
    def mock_call_llm_role(*args, **kwargs):
        messages = kwargs.get("messages") or []
        content = messages[1]["content"] if len(messages) > 1 else ""
        if "rm -rf /" in content:
            return '{"approved": false, "reason": "destructive"}'
        if "cat code/a.py > code/b.py" in content and "Allow shell file writes for this task." in content:
            return '{"approved": true, "reason": "allowed write"}'
        if "cat code/a.py > code/b.py" in content:
            return '{"approved": false, "reason": "no writes"}'
        return '{"approved": true, "reason": "looks good"}'

    sys.modules['call_llm'] = type('Mock', (object,), {'call_llm': mock_call_llm_role, 'call_llm_role': mock_call_llm_role})
    globals()['call_llm'] = mock_call_llm_role
    globals()['call_llm_role'] = mock_call_llm_role
    
    t1 = shell_verifier("ls code/modules", instruction_prompt="Allow reading files.")
    assert t1["approved"] == True, t1
    assert t1["is_inspection"] == True
    
    t2 = shell_verifier("grep -R CodeEdit code/modules", instruction_prompt="Allow grep.")
    assert t2["approved"] == True
    assert t2["is_inspection"] == True
    
    t3 = shell_verifier("python code/test_stage1_edit_pipeline.py", instruction_prompt="Allow python validation scripts under code/.")
    assert t3["approved"] == True
    assert t3["is_inspection"] == False
    
    t4 = shell_verifier("rm -rf /", instruction_prompt="Allow everything.")
    assert t4["approved"] == False
    
    t5 = shell_verifier("cat code/a.py > code/b.py", instruction_prompt="Do not allow shell file writes.")
    assert t5["approved"] == False
    
    t6 = shell_verifier("cat code/a.py > code/b.py", instruction_prompt="Allow shell file writes for this task.")
    assert t6["approved"] == True

    t7 = shell_verifier("pytest tests/ 2>/dev/null", instruction_prompt="Allow pytest.")
    assert t7["approved"] == True, t7

    t8 = shell_verifier("grep -R foo code/ > /dev/null", instruction_prompt="Allow grep.")
    assert t8["approved"] == True, t8

    t9 = shell_verifier("python code/run.py > /dev/null 2>&1", instruction_prompt="Allow python.")
    assert t9["approved"] == True, t9

    t10 = shell_verifier("cat code/a.py > code/b.py 2>/dev/null", instruction_prompt="Do not allow shell file writes.")
    assert t10["approved"] == False, t10

    assert is_null_sink_redirection_only("pytest 2>/dev/null") is True
    assert is_null_sink_redirection_only("cat a.py > b.py") is False
    assert has_shell_redirection("pytest 2>/dev/null") is False
    assert has_shell_redirection("cat a.py > b.py") is True
    
    print("SHELL_VERIFIER SELF TEST PASSED")
