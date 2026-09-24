from load_registry_metadata import (
    load_metadata_json,
    lookup_metadata_by_source_path,
    modules_list_from_registry,
    resolve_module_metadata,
)


MODULE_METADATA = {
    "name": "build_plan_module_registry",
    "type": "function",
    "description": "Build a temporary module registry from meta_writer sidecars plus valid module write_file steps in the active plan.",
    "functions": [
        {
            "name": "build_plan_module_registry",
            "inputs": {
                "active_plan": "list of planner action dictionaries, possibly including write_file steps for code/modules/*.py"
            },
            "outputs": "dict with key modules containing persistent registry entries plus valid same-plan module entries",
        }
    ],
}


def build_plan_module_registry(active_plan):
    """
    Build a temporary module registry from the current plan
    before execution, including modules that will be written.

    Metadata comes from meta_writer sidecars in metadata.json and,
    for planned writes, from meta_caller generation.
    """

    registry = load_metadata_json()
    by_path = {
        entry.get("path"): entry
        for entry in modules_list_from_registry(registry)
        if entry.get("path")
    }

    for step in active_plan:
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
