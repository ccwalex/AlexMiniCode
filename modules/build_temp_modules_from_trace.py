from extract_module_metadata_from_content import extract_module_metadata_from_content
from validate_module_metadata import validate_module_metadata


MODULE_METADATA = {
    "name": "build_temp_modules_from_trace",
    "type": "function",
    "description": "Build an ephemeral module registry from successful executed write_file steps in an execution trace.",
    "functions": [
        {
            "name": "build_temp_modules_from_trace",
            "inputs": {
                "executed_trace": "list of execution trace dicts containing step, success, and output fields"
            },
            "outputs": "dict with key modules containing valid module metadata entries from successful write_file steps"
        }
    ]
}


def build_temp_modules_from_trace(executed_trace):
    """
    Build an ephemeral module registry from successful executed write_file steps.

    Latest successful write_file for each code/modules/*.py path wins.
    This does NOT update persistent agent_memory/core/modules.json.
    """

    by_path = {}

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