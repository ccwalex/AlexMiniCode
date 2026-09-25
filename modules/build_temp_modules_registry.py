from load_registry_metadata import (
    load_metadata_json,
    lookup_metadata_by_source_path,
    modules_list_from_registry,
    resolve_module_metadata,
)


MODULE_METADATA = {
    "name": "build_temp_modules_registry",
    "type": "function",
    "description": "Build a temporary module registry by overlaying successful executed module write_file steps on top of meta_writer sidecar metadata.",
    "functions": [
        {
            "name": "build_temp_modules_registry",
            "inputs": {
                "executed_trace": "list of execution trace dicts containing step, success, and output fields",
                "registry_path": "str project-relative path to metadata.json, default agent_memory/core/metadata.json",
            },
            "outputs": "dict with key modules containing persistent registry entries overlaid by valid successful module writes from executed_trace",
        }
    ],
}


def build_temp_modules_registry(
    executed_trace,
    registry_path="agent_memory/core/metadata.json",
):
    """
    Build temporary module registry from meta_writer sidecars plus
    successful module write_file actions in executed_trace.

    Latest successful write per module path wins.
    """

    registry = load_metadata_json(registry_path)
    by_path = {
        entry.get("path"): entry
        for entry in modules_list_from_registry(registry)
        if entry.get("path")
    }

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

        entry = lookup_metadata_by_source_path(registry, path)
        if not entry:
            entry = resolve_module_metadata(path, content=content, registry=registry)

        if not entry:
            continue

        by_path[path] = entry

    return {
        "modules": sorted(
            by_path.values(),
            key=lambda m: m.get("name", ""),
        )
    }
