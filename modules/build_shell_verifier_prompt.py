from prompt_override import SYSTEM_PROMPT_OVERRIDE_BLOCK

MODULE_METADATA = {
  "name": "build_shell_verifier_prompt",
  "type": "function",
  "description": "Build system and user prompts for verifier approval of a proposed run_shell action.",
  "functions": [
    {
      "name": "build_shell_verifier_prompt",
      "inputs": {
        "step": "dict representing proposed run_shell action",
        "original_prompt": "str or None; optional original user/task prompt for context",
        "permission_context": "str; optional raw prompt block describing task-specific shell permissions"
      },
      "outputs": "tuple (system_prompt, user_prompt) for shell verifier model"
    }
  ]
}

def build_shell_verifier_prompt(step, original_prompt=None, permission_context=None):
    """Build system and user prompts for verifier approval of a proposed run_shell action."""
    
    # Base system instructions with enhanced permission checks
    system_prompt = f"""{SYSTEM_PROMPT_OVERRIDE_BLOCK}

You are a security-aware shell command verifier for a coding agent.

Your responsibilities:
1. Validate that proposed shell commands are safe to execute in the project environment
2. Check for file operations outside allowed project paths or house rules
3. Verify commands don't expose sensitive data or perform destructive operations
4. Approve only if the command aligns with the original task intent

Permission Rules:
- File operations must stay within project boundaries (CFG.PROJECT_ROOT)
- No access to system configuration files outside project scope
- Reject any command attempting to modify files outside allowed paths
- Verify write operations match declared intent from original prompt

When rejecting:
- Provide clear, specific reasons for denial
- Suggest safe alternatives if possible
- Preserve task integrity while maintaining security
"""

    # User prompt with full context
    cmd = step.get("cmd") if isinstance(step, dict) else step
    user_prompt = f"""Proposed Shell Command Verification

Original Task Prompt:
{original_prompt if original_prompt else 'N/A'}

Permission Context:
{permission_context if permission_context else 'None'}

Proposed Command Details:
{cmd}

Verification Requirements:
1. Does this command stay within project boundaries?
2. Are file operations limited to allowed paths?
3. Does it match the intent of the original prompt?
4. Are there any security concerns?

Respond with JSON:

{{
  "approved": true/false,
  "reason": "explanation",
  "command": "{cmd}" if approved else null,
  "suggested_alternative": "optional improvement"
}}
"""

    return system_prompt, user_prompt