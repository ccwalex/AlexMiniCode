"""
OpenCode Go model catalog and per-model transport routing.

Transport metadata comes from the Go Endpoints docs table; live APIs only
return model IDs and rich metadata without endpoint paths.
"""

from __future__ import annotations

import re
import time
from typing import Any

from cfg import CFG

MODULE_METADATA = {
    "name": "opencode_registry",
    "type": "function",
    "description": "Resolve OpenCode Go model transports and fetch merged model catalog.",
}

OPENCODE_GO_BASE = CFG.OPENCODE_GO_BASE_URL
DEFAULT_OPENCODE_MODEL = "deepseek-v4-flash"
MODELS_API_URL = "https://models.opencode.ai/api.json"
LIVE_MODELS_URL = f"{OPENCODE_GO_BASE.rstrip('/')}/models"

TRANSPORT_ENDPOINTS = {
    "chat": "/chat/completions",
    "responses": "/responses",
    "messages": "/messages",
}

# Extracted from https://opencode.ai/docs/go/#endpoints
GO_TRANSPORT_REGISTRY: dict[str, str] = {
    "grok-4.6": "responses",
    "gpt-5.6-luna": "responses",
    "muse-spark-1.3-contributor": "responses",
    "muse-spark-1.2-contributor": "responses",
    "minimax-m3": "messages",
    "minimax-m2.7": "messages",
    "minimax-m2.5": "messages",
    "qwen3.8-max": "messages",
    "qwen3.8-flash": "messages",
    "qwen3.7-max": "messages",
    "qwen3.7-plus": "messages",
    "qwen3.6-plus": "messages",
    "glm-5.3-flash": "chat",
    "glm-5.3": "chat",
    "glm-5.2": "chat",
    "glm-5.1": "chat",
    "kimi-k3": "chat",
    "kimi-k2.7-code": "chat",
    "kimi-k2.6": "chat",
    "longcat-2.0": "chat",
    "deepseek-v4.1-flash": "chat",
    "deepseek-v4-pro": "chat",
    "deepseek-v4-flash": "chat",
    "deepseek-v4-flash-vision-exp": "chat",
    "mimo-v2.5": "chat",
    "mimo-v2.5-pro": "chat",
    "hy4-preview": "chat",
    "hy3": "chat",
}

_NPM_TRANSPORT = {
    "@ai-sdk/openai": "responses",
    "@ai-sdk/anthropic": "messages",
    "@ai-sdk/openai-compatible": "chat",
}

_FAMILY_TRANSPORT_PREFIXES = (
    ("qwen3", "messages"),
    ("minimax", "messages"),
    ("grok", "responses"),
    ("gpt-", "responses"),
    ("muse-spark", "responses"),
    ("deepseek", "chat"),
    ("kimi", "chat"),
    ("glm", "chat"),
    ("mimo", "chat"),
    ("hy", "chat"),
    ("longcat", "chat"),
)

_catalog_cache: dict[str, Any] | None = None
_catalog_fetched_at: float = 0.0
_CATALOG_TTL_SECONDS = 300.0


def normalize_model_id(model) -> str:
    name = str(model or "").strip()
    if name.lower().startswith("opencode-go/"):
        name = name.split("/", 1)[1].strip()
    return name


def resolve_endpoint_path(transport: str) -> str:
    key = str(transport or "chat").strip().lower()
    path = TRANSPORT_ENDPOINTS.get(key)
    if not path:
        raise ValueError(f"unknown OpenCode transport: {transport}")
    return path


def _provider_npm(api_entry: dict | None) -> str | None:
    if not isinstance(api_entry, dict):
        return None
    provider = api_entry.get("provider")
    if isinstance(provider, dict):
        npm = provider.get("npm")
        if npm:
            return str(npm).strip()
    return None


def infer_transport(model_id: str, api_entry: dict | None = None) -> tuple[str, str]:
    model_id = normalize_model_id(model_id)
    if not model_id:
        return "chat", "inferred"

    npm = _provider_npm(api_entry)
    if npm and npm in _NPM_TRANSPORT:
        return _NPM_TRANSPORT[npm], "provider_npm"

    family = ""
    if isinstance(api_entry, dict):
        family = str(api_entry.get("family") or "").strip().lower()

    for prefix, transport in _FAMILY_TRANSPORT_PREFIXES:
        if model_id.startswith(prefix) or family.startswith(prefix):
            return transport, "inferred"

    return "chat", "inferred"


def resolve_transport_entry(model_id: str, api_entry: dict | None = None) -> dict[str, str]:
    model_id = normalize_model_id(model_id)
    if model_id in GO_TRANSPORT_REGISTRY:
        transport = GO_TRANSPORT_REGISTRY[model_id]
        return {
            "transport": transport,
            "endpoint_path": resolve_endpoint_path(transport),
            "transport_source": "docs",
        }

    transport, source = infer_transport(model_id, api_entry)
    return {
        "transport": transport,
        "endpoint_path": resolve_endpoint_path(transport),
        "transport_source": source,
    }


def resolve_transport(model_id: str) -> str:
    catalog = get_model_catalog()
    entry = catalog.get(normalize_model_id(model_id))
    if entry:
        return str(entry["transport"])
    resolved = resolve_transport_entry(model_id)
    return str(resolved["transport"])


def _fetch_live_model_ids() -> list[str]:
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("requests package is required for OpenCode model listing") from exc

    response = requests.get(LIVE_MODELS_URL, timeout=30)
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        return []

    ids = []
    for item in data:
        if isinstance(item, dict) and item.get("id"):
            ids.append(normalize_model_id(item["id"]))
    return sorted(set(ids))


def _fetch_api_json_models() -> dict[str, dict]:
    try:
        import requests
    except ImportError:
        return {}

    try:
        response = requests.get(MODELS_API_URL, timeout=30)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return {}

    provider = payload.get("opencode-go") if isinstance(payload, dict) else None
    if not isinstance(provider, dict):
        return {}

    models = provider.get("models") or {}
    if not isinstance(models, dict):
        return {}

    out: dict[str, dict] = {}
    for key, value in models.items():
        model_id = normalize_model_id(key)
        if model_id and isinstance(value, dict):
            out[model_id] = value
    return out


def _build_catalog_entry(model_id: str, api_entry: dict | None) -> dict[str, Any]:
    routing = resolve_transport_entry(model_id, api_entry)
    label = model_id
    if isinstance(api_entry, dict):
        label = str(api_entry.get("name") or api_entry.get("id") or model_id).strip() or model_id

    entry = {
        "id": model_id,
        "label": label,
        "transport": routing["transport"],
        "endpoint_path": routing["endpoint_path"],
        "transport_source": routing["transport_source"],
    }

    if isinstance(api_entry, dict):
        if api_entry.get("description"):
            entry["description"] = api_entry["description"]
        if api_entry.get("cost"):
            entry["cost"] = api_entry["cost"]
        if api_entry.get("reasoning_options"):
            entry["reasoning_options"] = api_entry["reasoning_options"]
        if api_entry.get("reasoning") is not None:
            entry["reasoning"] = api_entry["reasoning"]

    return entry


def validate_catalog(catalog: dict[str, dict]) -> list[str]:
    warnings = []
    for model_id, entry in catalog.items():
        if entry.get("transport_source") != "docs":
            warnings.append(
                f"OpenCode model '{model_id}' uses inferred transport "
                f"'{entry.get('transport')}' (source={entry.get('transport_source')})"
            )
    return warnings


def fetch_opencode_models(force_refresh: bool = False) -> list[dict[str, Any]]:
    global _catalog_cache, _catalog_fetched_at

    if (
        not force_refresh
        and _catalog_cache is not None
        and (time.time() - _catalog_fetched_at) < _CATALOG_TTL_SECONDS
    ):
        return list(_catalog_cache.values())

    live_ids = _fetch_live_model_ids()
    api_models = _fetch_api_json_models()

    if not live_ids and api_models:
        live_ids = sorted(api_models.keys())

    catalog: dict[str, dict] = {}
    for model_id in live_ids:
        catalog[model_id] = _build_catalog_entry(model_id, api_models.get(model_id))

    for warning in validate_catalog(catalog):
        print(f"[OpenCode] {warning}")

    _catalog_cache = catalog
    _catalog_fetched_at = time.time()
    return list(catalog.values())


def get_model_catalog(force_refresh: bool = False) -> dict[str, dict]:
    models = fetch_opencode_models(force_refresh=force_refresh)
    return {item["id"]: item for item in models}


if __name__ == "__main__":
    for model_id, transport in GO_TRANSPORT_REGISTRY.items():
        resolved = resolve_transport_entry(model_id)
        assert resolved["transport"] == transport
        assert resolved["transport_source"] == "docs"

    assert resolve_transport_entry("grok-4.5", {"provider": {"npm": "@ai-sdk/openai"}})["transport"] == "responses"
    assert resolve_transport_entry("qwen3.5-plus", {"family": "qwen3.5"})["transport"] == "messages"
    assert resolve_transport_entry("glm-5", {"family": "glm"})["transport"] == "chat"
    assert resolve_transport_entry("omen-alpha")["transport"] == "chat"
    assert resolve_endpoint_path("chat") == "/chat/completions"
    assert normalize_model_id("opencode-go/deepseek-v4-flash") == "deepseek-v4-flash"

    sample = _build_catalog_entry("deepseek-v4-flash", {"name": "DeepSeek V4 Flash"})
    assert sample["transport"] == "chat"
    assert sample["endpoint_path"] == "/chat/completions"
    assert sample["transport_source"] == "docs"

    print("OPENCODE_REGISTRY SELF TEST PASSED")
