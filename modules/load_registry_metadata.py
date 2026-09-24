import json
import os

from read_file import read_file
from validate_module_metadata import validate_module_metadata
from meta_writer import sanitize_module_metadata

MODULE_METADATA = {
    "name": "load_registry_metadata",
    "type": "function",
    "description": "Load module metadata from meta_writer sidecars and metadata.json, optionally generating metadata via meta_caller.",
    "functions": [
        {
            "name": "load_metadata_json",
            "inputs": {
                "registry_path": "str project-relative metadata.json path",
            },
            "outputs": "dict keyed by metadata sidecar path with sanitized metadata entries",
        },
        {
            "name": "lookup_metadata_by_source_path",
            "inputs": {
                "registry": "dict metadata.json contents",
                "source_path": "str project-relative source file path",
            },
            "outputs": "dict metadata entry or None",
        },
        {
            "name": "resolve_module_metadata",
            "inputs": {
                "path": "str project-relative source file path",
                "content": "str or None optional source content for meta_caller generation",
            },
            "outputs": "dict metadata entry or None",
        },
        {
            "name": "modules_list_from_registry",
            "inputs": {
                "registry": "dict metadata.json contents",
            },
            "outputs": "list of module metadata dicts with path",
        },
    ],
}


def _normalize_rel(path):
    return os.path.normpath(str(path or "")).replace("\\", "/")


def load_metadata_json(registry_path="agent_memory/core/metadata.json"):
    ok, content = read_file(registry_path)
    if not ok or not str(content).strip():
        return {}

    try:
        parsed = json.loads(content)
    except Exception:
        return {}

    if not isinstance(parsed, dict):
        return {}

    registry = {}
    for key, value in parsed.items():
        if not isinstance(value, dict):
            continue
        source_path = value.get("path") or key
        registry[key] = sanitize_module_metadata(value, path=source_path)
    return registry


def lookup_metadata_by_source_path(registry, source_path):
    target = _normalize_rel(source_path)
    if not target or not isinstance(registry, dict):
        return None

    for entry in registry.values():
        if not isinstance(entry, dict):
            continue
        if _normalize_rel(entry.get("path")) == target:
            return entry
    return None


def _metadata_entry_from_meta(meta, path):
    if not isinstance(meta, dict) or meta.get("error"):
        return None

    ok, _ = validate_module_metadata(meta)
    if not ok:
        return None

    entry = sanitize_module_metadata(meta, path=path)
    if not entry.get("name"):
        return None

    return entry


def resolve_module_metadata(path, content=None, registry=None):
    path = _normalize_rel(path)
    if not path:
        return None

    if registry is None:
        registry = load_metadata_json()

    existing = lookup_metadata_by_source_path(registry, path)
    if existing and existing.get("name"):
        return existing

    if content is None:
        return None

    try:
        from meta_caller import meta_caller
    except Exception:
        return None

    generated = meta_caller(path, content)
    return _metadata_entry_from_meta(generated, path)


def modules_list_from_registry(registry):
    by_path = {}
    for entry in (registry or {}).values():
        if not isinstance(entry, dict) or not entry.get("path"):
            continue
        by_path[_normalize_rel(entry["path"])] = entry

    return sorted(by_path.values(), key=lambda item: item.get("name", ""))
