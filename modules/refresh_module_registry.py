import os
import json

from cfg import CFG
from safe_path import safe_path
from extract_module_metadata_from_content import extract_module_metadata_from_content
from validate_module_metadata import validate_module_metadata
from validate_metadata_matches_code import validate_metadata_matches_code


MODULE_METADATA = {
    "name": "refresh_modules_registry",
    "type": "function",
    "description": "Rebuild the Python module registry by scanning module files and extracting validated MODULE_METADATA entries.",
    "functions": [
        {
            "name": "refresh_modules_registry",
            "inputs": {
                "modules_dir": "str project-relative directory containing Python module files, default code/modules",
                "registry_path": "str project-relative output registry path, default agent_memory/core/modules.json",
                "strict": "bool; if True abort without writing when invalid module files are found"
            },
            "outputs": "tuple (bool, str) where bool indicates refresh success and str summarizes registered or skipped modules"
        }
    ]
}


def refresh_modules_registry(
    modules_dir="code/modules",
    registry_path="agent_memory/core/modules.json",
    strict=False,
):
    """
    Rebuild agent_memory/core/modules.json by scanning all Python files under
    code/modules/ and extracting their top-level MODULE_METADATA dictionaries.

    Args:
        modules_dir: Relative path to module directory.
        registry_path: Relative path to modules registry JSON.
        strict:
            - False: skip invalid module files, rebuild registry from valid ones,
              and report skipped files.
            - True: abort without writing if any module file is invalid.

    Returns:
        (success: bool, message: str)
    """

    root = safe_path(modules_dir)

    if not os.path.exists(root):
        return False, f"modules directory does not exist: {modules_dir}"

    if not os.path.isdir(root):
        return False, f"modules path is not a directory: {modules_dir}"

    entries = []
    errors = []

    for dirpath, dirnames, filenames in os.walk(root):
        # Avoid cache and hidden directories.
        dirnames[:] = [
            d for d in dirnames
            if d != "__pycache__" and not d.startswith(".")
        ]

        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue

            if filename.startswith("."):
                continue

            full_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(full_path, CFG.PROJECT_ROOT)

            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception as e:
                errors.append(
                    {
                        "path": rel_path,
                        "reason": f"failed to read file: {e}",
                    }
                )
                continue

            meta, err = extract_module_metadata_from_content(content)

            if err:
                errors.append(
                    {
                        "path": rel_path,
                        "reason": err,
                    }
                )
                continue

            ok, reason = validate_module_metadata(meta)

            if not ok:
                errors.append(
                    {
                        "path": rel_path,
                        "reason": reason,
                    }
                )
                continue

            ok, reason = validate_metadata_matches_code(meta, content)

            if not ok:
                errors.append(
                    {
                        "path": rel_path,
                        "reason": reason,
                    }
                )
                continue

            entries.append(
                {
                    "name": meta.get("name"),
                    "type": meta.get("type"),
                    "description": meta.get("description"),
                    "functions": meta.get("functions", []),
                    "path": rel_path,
                }
            )

    if strict and errors:
        message = (
            "registry refresh aborted because invalid module files were found:\n"
            + json.dumps(errors, indent=2, ensure_ascii=False)
        )
        return False, message

    # Deduplicate by path. Last entry wins, though paths should already be unique.
    by_path = {}

    for entry in entries:
        by_path[entry["path"]] = entry

    rebuilt = {
        "modules": sorted(
            by_path.values(),
            key=lambda m: (m.get("name", ""), m.get("path", "")),
        )
    }

    full_registry_path = safe_path(registry_path)
    os.makedirs(os.path.dirname(full_registry_path), exist_ok=True)

    with open(full_registry_path, "w", encoding="utf-8") as f:
        json.dump(rebuilt, f, indent=2, ensure_ascii=False)

    message = (
        f"refreshed {registry_path}: "
        f"{len(rebuilt['modules'])} valid module(s) registered"
    )

    if errors:
        message += (
            f", {len(errors)} invalid module file(s) skipped:\n"
            + json.dumps(errors, indent=2, ensure_ascii=False)
        )

    print("✅ " + message)

    return True, message
