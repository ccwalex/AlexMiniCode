import json

from read_file import read_file
from extract_module_metadata_from_content import extract_module_metadata_from_content
from validate_module_metadata import validate_module_metadata


MODULE_METADATA = {
    "name": "build_temp_modules_registry",
    "type": "function",
    "description": "Build a temporary module registry by overlaying successful executed module write_file steps on top of a persistent registry file.",
    "functions": [
        {
            "name": "build_temp_modules_registry",
            "inputs": {
                "executed_trace": "list of execution trace dicts containing step, success, and output fields",
                "registry_path": "str project-relative path to persistent module registry JSON, default agent_memory/core/modules.json"
            },
            "outputs": "dict with key modules containing persistent registry entries overlaid by valid successful module writes from executed_trace"
        }
    ]
}


def build_temp_modules_registry(
    executed_trace,
    registry_path="agent_memory/core/modules.json",
):
    """
    Build temporary module registry from a persistent registry plus
    successful module write_file actions in executed_trace.

    Latest successful write per module path wins.
    """

    def safe_read(path):
        ok, content = read_file(path)
        return content if ok else ""

    try:
        persistent = json.loads(safe_read(registry_path))
    except Exception:
        persistent = {"modules": []}

    by_path = {}

    for m in persistent.get("modules", []):
        path = m.get("path")
        if path:
            by_path[path] = m

    for item in executed_trace:
        if not item.get("success"):
            continue

        step = item.get("step", {})

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

        by_path[path] = {
            "name": meta.get("name"),
            "type": meta.get("type"),
            "description": meta.get("description"),
            "functions": meta.get("functions", []),
            "path": path,
        }

    return {
        "modules": sorted(
            by_path.values(),
            key=lambda m: m.get("name", ""),
        )
    }