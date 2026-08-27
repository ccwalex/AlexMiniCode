import os
import json

from safe_path import safe_path


MODULE_METADATA = {
    "name": "update_modules_registry",
    "type": "function",
    "description": "Update agent_memory/core/modules.json with verified Python module metadata for a written module file.",
    "functions": [
        {
            "name": "update_modules_registry",
            "inputs": {
                "meta": "dict containing validated MODULE_METADATA for a Python module",
                "path": "str project-relative path of the module file"
            },
            "outputs": "None; updates modules.json on disk and prints updated module name"
        }
    ]
}


def update_modules_registry(meta, path):
    registry_path = safe_path("agent_memory/core/modules.json")

    if os.path.exists(registry_path):
        try:
            with open(registry_path, "r") as f:
                data = json.load(f)
        except Exception:
            data = {"modules": []}
    else:
        data = {"modules": []}

    if "modules" not in data:
        data["modules"] = []

    # Remove existing entry for same path.
    data["modules"] = [
        m for m in data["modules"]
        if m.get("path") != path
    ]

    # Store metadata + path.
    entry = {
        "name": meta.get("name"),
        "type": meta.get("type"),
        "description": meta.get("description"),
        "functions": meta.get("functions", []),
        "path": path,
    }

    data["modules"].append(entry)

    # Sort for stable diffs/readability.
    data["modules"] = sorted(
        data["modules"],
        key=lambda m: m.get("name", ""),
    )

    with open(registry_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"✅ modules.json updated: {meta.get('name')}")