import json

from read_file import read_file
from extract_module_metadata_from_content import extract_module_metadata_from_content
from validate_module_metadata import validate_module_metadata


MODULE_METADATA = {
    "name": "build_plan_module_registry",
    "type": "function",
    "description": "Build a temporary module registry from persistent modules plus valid module write_file steps in the active plan.",
    "functions": [
        {
            "name": "build_plan_module_registry",
            "inputs": {
                "active_plan": "list of planner action dictionaries, possibly including write_file steps for code/modules/*.py"
            },
            "outputs": "dict with key modules containing persistent registry entries plus valid same-plan module entries"
        }
    ]
}


def build_plan_module_registry(active_plan):
    """
    Build a temporary module registry from the current plan
    before execution, including modules that will be written.

    This allows same-plan dependencies like:
    - define module
    - use module in script

    to validate correctly.
    """

    def safe_read(path):
        ok, content = read_file(path)
        return content if ok else ""

    # Start from persistent registry.
    try:
        registry = json.loads(safe_read("agent_memory/core/modules.json"))
        if "modules" not in registry:
            registry["modules"] = []
    except Exception:
        registry = {"modules": []}

    by_path = {
        m.get("path"): m
        for m in registry.get("modules", [])
        if m.get("path")
    }

    # Walk through planned write_file steps and add valid future modules.
    for step in active_plan:
        if step.get("action") != "write_file":
            continue

        path = step.get("path", "")
        content = step.get("content", "")

        if not (path.startswith("code/modules/") and path.endswith(".py")):
            continue

        meta, err = extract_module_metadata_from_content(content)
        if err:
            continue

        ok, _ = validate_module_metadata(meta)
        if not ok:
            continue

        entry = {
            "name": meta.get("name"),
            "type": meta.get("type"),
            "description": meta.get("description"),
            "functions": meta.get("functions", []),
            "path": path,
        }

        # Overwrite or insert future-state module entry.
        by_path[path] = entry

    return {
        "modules": sorted(
            by_path.values(),
            key=lambda m: m.get("name", ""),
        )
    }