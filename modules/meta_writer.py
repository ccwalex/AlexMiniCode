MODULE_METADATA = {
    "name": "meta_writer",
    "type": "function",
    "description": "Appends relative file path to metadata and writes metadata to a txt file named after the module.",
    "functions": [
        {
            "name": "meta_writer",
            "inputs": {
                "metadata": "dict, metadata from meta_caller",
                "file_path": "str, path to the module file",
                "output_path": "str or None, path to write"
            },
            "outputs": "None"
        }
    ]
}

import os
import json
from cfg import CFG
from track_folders import TrackFolders


def _normalize_rel(path):
    return os.path.normpath(path).replace("\\", "/")


def _find_tracked_metadata_output(file_path):
    project_root = CFG.PROJECT_ROOT
    abs_file_path = os.path.abspath(os.path.join(project_root, file_path))

    tracker = TrackFolders()
    rows = tracker.get_all()

    best = None

    for row in rows:
        folder_path = row.get("folder_path")
        metadata_path = row.get("metadata_path")

        if not folder_path or not metadata_path:
            continue

        abs_folder = os.path.abspath(os.path.join(project_root, folder_path))

        try:
            common = os.path.commonpath([abs_file_path, abs_folder])
        except Exception:
            continue

        if common != abs_folder:
            continue

        rel_inside = os.path.relpath(abs_file_path, abs_folder)
        candidate = {
            "folder_path": folder_path,
            "metadata_path": metadata_path,
            "rel_inside": _normalize_rel(rel_inside),
            "folder_len": len(abs_folder),
        }

        if best is None or candidate["folder_len"] > best["folder_len"]:
            best = candidate

    if best is None:
        return None

    output_rel = os.path.join(
        best["metadata_path"],
        best["rel_inside"] + ".txt",
    )

    return os.path.abspath(os.path.join(project_root, output_rel))


def meta_writer(metadata: dict, file_path: str, output_path: str | None = None) -> None:
    project_root = CFG.PROJECT_ROOT

    abs_file_path = os.path.abspath(os.path.join(project_root, file_path))
    relative_path = os.path.relpath(abs_file_path, start=project_root)
    relative_path = _normalize_rel(relative_path)

    metadata["path"] = relative_path

    if output_path is None:
        output_path = _find_tracked_metadata_output(relative_path)

    if output_path is None:
        module_name = metadata.get("name")
        if not module_name:
            raise ValueError("Metadata missing 'name' key")

        meta_dir = os.path.join(project_root, "agent_memory", "meta")
        output_path = os.path.join(meta_dir, f"{module_name}.txt")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)