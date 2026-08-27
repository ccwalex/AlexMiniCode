MODULE_METADATA = {
    "name": "refresh_registry",
    "type": "function",
    "description": "Refresh metadata registry by generating missing metadata files and rebuilding global metadata JSON.",
    "functions": [
        {
            "name": "refresh_registry",
            "inputs": {},
            "outputs": "tuple (bool, str)"
        }
    ]
}

import os
import json
import traceback

from track_folders import TrackFolders
from meta_caller import meta_caller
from meta_writer import meta_writer
from read_file import read_file
from cfg import CFG
from safe_path import safe_path


EXCLUDED_DIRS = {
    ".git",
    "__pycache__",
    ".ipynb_checkpoints",
    "node_modules",
    ".venv",
    "venv",
    "env",
    ".pytest_cache",
    ".mypy_cache",
}

EXCLUDED_SUFFIXES = {
    ".pyc", ".pyo",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".pdf", ".zip", ".tar", ".gz", ".7z",
    ".mp4", ".mov", ".avi",
    ".sqlite", ".db",
    ".parquet", ".npy", ".npz", ".pt", ".pth",
}

CODE_TYPE_EXTENSIONS = {
    "py": {".py"},
    "python": {".py"},
    "html": {".html", ".htm"},
    "react": {".tsx", ".jsx", ".ts", ".js"},
    "ts": {".ts", ".tsx", ".js", ".jsx"},
    "js": {".js", ".jsx", ".ts", ".tsx"},
    "md": {".md"},
}


def _should_skip_dir(dirname):
    return dirname in EXCLUDED_DIRS


def _should_scan_file(path, code_type):
    suffix = os.path.splitext(path)[1].lower()

    if suffix in EXCLUDED_SUFFIXES:
        return False

    allowed = CODE_TYPE_EXTENSIONS.get(str(code_type).lower().strip())

    if allowed is None:
        # Unknown code type: allow common text/code files.
        return suffix in {
            ".py", ".js", ".jsx", ".ts", ".tsx",
            ".html", ".htm", ".css",
            ".md", ".txt", ".json", ".yaml", ".yml",
            ".sh",
        }

    return suffix in allowed


def _safe_metadata_path(metadata_path, rel_path_in_folder):
    """
    Expected metadata location for one file.

    Example:
        folder_path = code
        metadata_path = agent_memory/meta/code
        source file = code/a/b.py
        rel_path_in_folder = a/b.py

    Result:
        agent_memory/meta/code/a/b.py.txt
    """
    expected_rel = os.path.join(metadata_path, rel_path_in_folder + ".txt")
    return safe_path(expected_rel)


def _metadata_exists(metadata_path, rel_path_in_folder):
    expected = _safe_metadata_path(metadata_path, rel_path_in_folder)
    return os.path.exists(expected), expected


def _write_metadata_fallback(metadata, expected_meta_full, rel_file_path):
    """
    meta_writer is the canonical writer. This fallback only writes to the exact
    expected path if meta_writer did not create it.
    """
    os.makedirs(os.path.dirname(expected_meta_full), exist_ok=True)

    if isinstance(metadata, dict):
        data = dict(metadata)
    else:
        data = {
            "metadata": metadata
        }

    data.setdefault("path", rel_file_path)

    with open(expected_meta_full, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def refresh_registry():
    """
    Refresh metadata registry by generating missing metadata files and rebuilding
    global metadata JSON.

    Important:
    - `TrackFolders` is the source of tracked folder configuration.
    - Missing metadata is checked under each tracked folder's `metadata_path`.
    - Files are scanned because they are inside a tracked folder; do not call
      `is_tracked()` on absolute file directories, since that silently skips files.
    """

    track_manager = TrackFolders()
    tracked_folders = track_manager.get_all()

    generated = 0
    existing = 0
    failed = 0
    scanned = 0
    skipped = 0
    errors = []

    # First pass: generate missing metadata files.
    for folder in tracked_folders:
        folder_path = folder.get("folder_path") or folder.get("folder") or folder.get("path")
        code_type = folder.get("code_type") or folder.get("type") or ""
        metadata_path = folder.get("metadata_path") or folder.get("meta_path")

        if not folder_path or not metadata_path:
            skipped += 1
            continue

        try:
            abs_folder_path = safe_path(folder_path)
        except Exception as e:
            failed += 1
            errors.append(f"Invalid folder path {folder_path}: {e}")
            continue

        if not os.path.exists(abs_folder_path):
            failed += 1
            errors.append(f"Tracked folder does not exist: {folder_path}")
            continue

        try:
            safe_path(metadata_path)
        except Exception as e:
            failed += 1
            errors.append(f"Invalid metadata path {metadata_path}: {e}")
            continue

        for root, dirs, files in os.walk(abs_folder_path):
            dirs[:] = [d for d in dirs if not _should_skip_dir(d)]

            for filename in files:
                abs_file_path = os.path.join(root, filename)

                if not _should_scan_file(abs_file_path, code_type):
                    skipped += 1
                    continue

                scanned += 1

                try:
                    rel_file_path = os.path.relpath(abs_file_path, CFG.PROJECT_ROOT)
                    rel_file_path = rel_file_path.replace("\\", "/")

                    rel_path_in_folder = os.path.relpath(abs_file_path, abs_folder_path)
                    rel_path_in_folder = rel_path_in_folder.replace("\\", "/")

                    exists, expected_meta_full = _metadata_exists(
                        metadata_path,
                        rel_path_in_folder
                    )

                    if exists:
                        existing += 1
                        continue

                    success, content = read_file(rel_file_path)

                    if not success:
                        failed += 1
                        errors.append(f"Could not read {rel_file_path}: {content}")
                        continue

                    metadata = meta_caller(path=rel_file_path, content=content)

                    if metadata is None:
                        failed += 1
                        errors.append(f"meta_caller returned None for {rel_file_path}")
                        continue

                    # Canonical writer.
                    try:
                        meta_writer(metadata, rel_file_path)
                    except Exception as e:
                        errors.append(f"meta_writer failed for {rel_file_path}: {e}")

                    # Ensure the expected file exists even if meta_writer uses
                    # a slightly different naming convention.
                    if not os.path.exists(expected_meta_full):
                        _write_metadata_fallback(
                            metadata=metadata,
                            expected_meta_full=expected_meta_full,
                            rel_file_path=rel_file_path,
                        )

                    generated += 1

                except Exception as e:
                    failed += 1
                    errors.append(
                        f"Metadata refresh failed for {abs_file_path}: {e}\n"
                        f"{traceback.format_exc()}"
                    )

    # Second pass: rebuild global metadata JSON.
    global_metadata = {}

    for folder in tracked_folders:
        meta_dir = folder.get("metadata_path") or folder.get("meta_path")

        if not meta_dir:
            continue

        try:
            abs_meta_dir = safe_path(meta_dir)
        except Exception:
            continue

        if not os.path.exists(abs_meta_dir):
            continue

        for root, _, files in os.walk(abs_meta_dir):
            for filename in files:
                if not filename.endswith(".txt"):
                    continue

                meta_file_path = os.path.join(root, filename)

                try:
                    with open(meta_file_path, "r", encoding="utf-8") as f:
                        meta_data = json.load(f)

                    rel_meta_path = os.path.relpath(meta_file_path, CFG.PROJECT_ROOT)
                    rel_meta_path = rel_meta_path.replace("\\", "/")

                    key = rel_meta_path
                    global_metadata[key] = meta_data

                except Exception:
                    continue

    output_rel = os.path.join("agent_memory", "core", "metadata.json")

    try:
        output_full = safe_path(output_rel)
        os.makedirs(os.path.dirname(output_full), exist_ok=True)

        with open(output_full, "w", encoding="utf-8") as f:
            json.dump(global_metadata, f, indent=2, ensure_ascii=False)

    except Exception as e:
        failed += 1
        errors.append(f"Error writing global metadata: {str(e)}")

    ok = failed == 0

    summary = (
        "Metadata registry refreshed: "
        f"tracked_folders={len(tracked_folders)}, "
        f"scanned={scanned}, "
        f"existing={existing}, "
        f"generated={generated}, "
        f"skipped={skipped}, "
        f"failed={failed}"
    )

    if errors:
        summary += "\n\nErrors:\n" + "\n".join(errors[:20])

    return ok, summary


if __name__ == "__main__":
    ok, msg = refresh_registry()
    print(ok)
    print(msg)
