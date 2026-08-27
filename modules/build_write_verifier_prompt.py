import json
import os

from cfg import CFG
from prompt_override import SYSTEM_PROMPT_OVERRIDE_BLOCK


MODULE_METADATA = {
    "name": "build_write_verifier_prompt",
    "type": "function",
    "description": "Build system and user prompts for verifier approval of a proposed write_file action.",
    "functions": [
        {
            "name": "build_write_verifier_prompt",
            "inputs": {
                "step": "dict representing proposed write_file action",
                "modules_override": "dict or list or None; optional module registry override",
                "file_context_override": "str or None; optional injected file context placeholder for future adaptive file injection",
                "registry_context_override": "str or None; optional injected registry context placeholder for future adaptive registry injection"
            },
            "outputs": "tuple (system_prompt, user_prompt) for verifier model"
        }
    ]
}


def build_write_verifier_prompt(
    step,
    modules_override=None,
    file_context_override=None,
    registry_context_override=None,
):
    """
    Build verifier prompt for write_file actions.

    Current behavior:
    - Loads project principles, module registry, LLM memory, and failure log.
    - Injects proposed write step.
    - Returns strict approve/reject verifier prompt.

    Future-compatible placeholders:
    - file_context_override can carry selected injected file context.
    - registry_context_override can carry adaptive registry context.
    """

    def read_memory_text(rel_path, default=""):
        full = os.path.join(CFG.PROJECT_ROOT, rel_path)
        try:
            with open(full, "r") as f:
                return f.read()
        except Exception:
            return default

    def render_json_or_text(obj):
        if obj is None:
            return ""
        if isinstance(obj, str):
            return obj
        return json.dumps(obj, indent=2, ensure_ascii=False)

    def build_metadata_example():
        return '''MODULE_METADATA = {
  "name": "module_name",
  "type": "function or class",
  "description": "what it does",
  "functions": [
    {
      "name": "function_name",
      "inputs": { "arg": "type" },
      "outputs": "type"
    }
  ]
}'''

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

    metadata_example = build_metadata_example()

    principles = read_memory_text("agent_memory/core/principles.md")
    modules = load_modules_context()
    memory = read_memory_text("agent_memory/reasoning/llm_memory.json")
    failures = read_memory_text("agent_memory/reasoning/failures.md")
    file_context = load_file_context()

    system_prompt = f'''
{SYSTEM_PROMPT_OVERRIDE_BLOCK}

<system>
You are a strict verifier for an autonomous coding agent.

Your job is ONLY to approve or reject the proposed write action.

Rules:
- Do NOT suggest improvements.
- Do NOT rewrite code.
- Do NOT provide alternative code.
- Do NOT ask for more information.
- Reject only concrete likely failures or clear rule violations.
- Do NOT reject merely because the code could be improved.
- Do NOT reject for style issues.
- Return ONLY valid JSON.
</system>

<tensor_reasoning_rules>
Before approving or modifying PyTorch code:
- mentally trace tensor shapes through the network
- verify spatial dimensions after stride/pooling/pixel shuffle
- verify channel counts for concatenation/addition
- verify residual/skip connection compatibility
- verify decoder output size matches reconstruction target
- verify PixelShuffle channel divisibility rules
- verify latent reshape dimensions are consistent
</tensor_reasoning_rules>

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
'''

    user_prompt = f'''
<house_rules>
{principles}
</house_rules>

<known_modules>
{modules}
</known_modules>

<injected_file_context>
{file_context}
</injected_file_context>

<llm_memory>
{memory}
</llm_memory>

<recent_failure_log>
{failures}
</recent_failure_log>

<proposed_step>
{json.dumps(step, indent=2, ensure_ascii=False)}
</proposed_step>

<audit_checklist>

<path_violations>
Reject if:
- Paths escape the project directory.
</path_violations>


<code_integrity_issues>
Reject if:
- Code has obvious syntax errors.
- Imports reference dependencies that are not imported or defined.
- Script imports a module path that likely does not exist.
- There is an obvious variable/name mismatch.
- There is an obvious tensor/array shape mismatch based only on the code shown.
- Function call arguments clearly mismatch the declared signature.
</code_integrity_issues>

<known_pitfall_violations>
Reject if:
- Proposed step violates a known issue or workaround in LLM memory.
- Proposed step repeats a recent failure pattern.
</known_pitfall_violations>

</audit_checklist>
'''

    return system_prompt, user_prompt