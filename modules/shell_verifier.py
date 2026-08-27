import json
import re
from build_shell_verifier_prompt import build_shell_verifier_prompt
from call_llm import call_llm_role
from structured_llm_retry import call_llm_role_with_parse_retry, is_valid_verifier_response
from prompt_override import SYSTEM_PROMPT_OVERRIDE_BLOCK

MODULE_METADATA = {
    "name": "shell_verifier",
    "type": "function",
    "description": "Verify shell commands before execution using deterministic hard-deny checks and mandatory LLM audit with caller-provided shell permission instructions.",
    "functions": [
        {
            "name": "shell_verifier",
            "inputs": {
                "cmd": "str proposed shell command",
                "instruction_prompt": "str raw permission/safety instructions for shell verification"
            },
            "outputs": "dict with approved bool, reason, optional normalized command, inspection flag, and risk level"
        }
    ]
}

def has_shell_redirection(cmd):
    return ">" in cmd or ">>" in cmd or "| tee" in cmd or "|tee" in cmd

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

def shell_verifier(cmd, instruction_prompt=""):
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
        
    if has_shell_redirection(cmd) and "allow shell file writes" not in instruction_prompt.lower():
         return {
            "approved": False,
            "reason": "Shell redirection used without explicit permission to write files.",
            "command": None,
            "is_inspection": False,
            "risk_level": "high"
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
    
    print("SHELL_VERIFIER SELF TEST PASSED")
