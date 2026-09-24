from load_registry_metadata import resolve_module_metadata


MODULE_METADATA = {
    "name": "build_temp_modules_from_trace",
    "type": "function",
    "description": "Build an ephemeral module registry from successful executed write_file steps in an execution trace using meta_writer metadata.",
    "functions": [
        {
            "name": "build_temp_modules_from_trace",
            "inputs": {
                "executed_trace": "list of execution trace dicts containing step, success, and output fields"
            },
            "outputs": "dict with key modules containing valid module metadata entries from successful write_file steps",
        }
    ],
}


def build_temp_modules_from_trace(executed_trace):
    """
    Build an ephemeral module registry from successful executed write_file steps.

    Latest successful write_file for each code/modules/*.py path wins.
    Metadata is resolved from meta_writer sidecars or generated via meta_caller.
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

        entry = resolve_module_metadata(path, content=content)
        if not entry:
            continue

        by_path[path] = entry

    return {
        "modules": sorted(
            by_path.values(),
            key=lambda m: m.get("name", ""),
        )
    }
