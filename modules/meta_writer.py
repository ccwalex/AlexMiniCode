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


METADATA_KEYS = ("name", "type", "description", "functions", "path")


def _normalize_rel(path):
    return os.path.normpath(path).replace("\\", "/")


def _strip_code_fences(text):
    text = str(text).strip()
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _response_text(raw):
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, dict):
        return ""

    for key in ("content", "text", "output", "response"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value

    message = raw.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    if isinstance(message, str) and message.strip():
        return message

    choices = raw.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            msg = first.get("message")
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                return msg["content"]
            if isinstance(first.get("text"), str):
                return first["text"]

    candidates = raw.get("candidates")
    if isinstance(candidates, list) and candidates:
        first = candidates[0]
        if isinstance(first, dict):
            content = first.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, dict):
                parts = content.get("parts")
                if isinstance(parts, list):
                    joined = "".join(
                        part.get("text", "")
                        for part in parts
                        if isinstance(part, dict)
                    )
                    if joined.strip():
                        return joined

    return ""


def _parse_json_object(text):
    text = _strip_code_fences(text)
    if not text:
        return None

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None

    try:
        parsed = json.loads(text[start : end + 1])
    except Exception:
        return None

    return parsed if isinstance(parsed, dict) else None


def _looks_like_module_metadata(value):
    return isinstance(value, dict) and "name" in value and (
        "functions" in value or "type" in value or "description" in value
    )


def _coerce_metadata_dict(raw):
    if isinstance(raw, str):
        parsed = _parse_json_object(raw)
        return parsed if isinstance(parsed, dict) else {}

    if not isinstance(raw, dict):
        return {}

    if _looks_like_module_metadata(raw):
        return raw

    nested = raw.get("metadata")
    if _looks_like_module_metadata(nested):
        return nested

    text = _response_text(raw)
    if text:
        parsed = _parse_json_object(text)
        if _looks_like_module_metadata(parsed):
            return parsed
        if isinstance(parsed, dict) and _looks_like_module_metadata(parsed.get("metadata")):
            return parsed["metadata"]

    return {}


def sanitize_module_metadata(raw, path=None):
    """Keep only MODULE_METADATA fields plus path. Drop LLM envelope fields."""
    parsed = _coerce_metadata_dict(raw)
    entry = {}

    for key in METADATA_KEYS:
        value = parsed.get(key)
        if value is not None:
            entry[key] = value

    if path:
        entry["path"] = _normalize_rel(path)

    return entry


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

    metadata = sanitize_module_metadata(metadata, path=relative_path)

    if not metadata.get("name"):
        raise ValueError("Metadata missing 'name' key")

    if output_path is None:
        output_path = _find_tracked_metadata_output(relative_path)

    if output_path is None:
        meta_dir = os.path.join(project_root, "agent_memory", "meta")
        output_path = os.path.join(meta_dir, f"{metadata['name']}.txt")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    raw = {
        "name": "example",
        "type": "function",
        "description": "demo",
        "functions": [{"name": "example", "inputs": {}, "outputs": "None"}],
        "llm_source": "relay",
        "llm_model": "mini",
        "fallback_used": False,
        "cursor_params": [],
    }
    cleaned = sanitize_module_metadata(raw, path="modules/example.py")
    assert cleaned == {
        "name": "example",
        "type": "function",
        "description": "demo",
        "functions": [{"name": "example", "inputs": {}, "outputs": "None"}],
        "path": "modules/example.py",
    }
    assert "llm_source" not in cleaned
    assert "llm_model" not in cleaned

    wrapped = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "name": "wrapped",
                            "type": "function",
                            "description": "from llm",
                            "functions": [{"name": "wrapped", "inputs": {}, "outputs": "str"}],
                        }
                    )
                }
            }
        ],
        "llm_source": "cursor",
        "llm_model": "composer",
    }
    from_wrapper = sanitize_module_metadata(wrapped, path="code/wrapped.py")
    assert from_wrapper["name"] == "wrapped"
    assert from_wrapper["path"] == "code/wrapped.py"
    assert "llm_source" not in from_wrapper
    print("META_WRITER SANITIZE SELF TEST PASSED")