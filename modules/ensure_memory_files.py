import os
import json

from safe_path import safe_path


MODULE_METADATA = {
    "name": "ensure_memory_files",
    "type": "function",
    "description": "Create required agent memory files and directories if they are missing.",
    "functions": [
        {
            "name": "ensure_memory_files",
            "inputs": {},
            "outputs": "None; creates missing agent_memory files/directories and prints created file paths"
        }
    ]
}


def ensure_memory_files():
    """
    Create required agent memory files/directories if missing.
    """

    defaults = {
        "agent_memory/core/project.md": "",
        "agent_memory/core/principles.md": "",
        "agent_memory/core/modules.json": {
            "modules": []
        },

        "agent_memory/planning/current_plan.md": "",

        "agent_memory/summaries/compressed_context.md": "",

        "agent_memory/reasoning/llm_memory.json": [],
        "agent_memory/reasoning/failures.md": "",

        "agent_memory/execution/recent_runs.json": []
    }

    for rel_path, default_content in defaults.items():
        full = safe_path(rel_path)

        os.makedirs(os.path.dirname(full), exist_ok=True)

        if not os.path.exists(full):
            with open(full, "w") as f:
                if isinstance(default_content, (dict, list)):
                    json.dump(default_content, f, indent=2)
                else:
                    f.write(default_content)

            print(f"✅ Created missing memory file: {rel_path}")
