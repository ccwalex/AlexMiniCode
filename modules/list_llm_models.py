"""
Fetch available model lists for relay and Cursor sources.
Cursor models are fetched live and are not persisted.
"""

from __future__ import annotations

import json
import os

from cursor_model_selection import model_selection_label, normalize_cursor_params
from model_registry import GUI_MODELS

MODULE_METADATA = {
    "name": "list_llm_models",
    "type": "function",
    "description": "List available models for relay server or Cursor SDK.",
    "functions": [
        {
            "name": "list_relay_models",
            "inputs": {},
            "outputs": "dict with success, models, error",
        },
        {
            "name": "list_cursor_models",
            "inputs": {},
            "outputs": "dict with success, models, error",
        },
        {
            "name": "list_models_for_source",
            "inputs": {"source": "str relay or cursor"},
            "outputs": "dict with success, models, error",
        },
    ],
}


def _model_item(
    model_id: str,
    label: str | None = None,
    *,
    params=None,
    kind: str = "model",
    base_model: str | None = None,
    parameters=None,
    variants=None,
) -> dict:
    model_id = str(model_id or "").strip()
    normalized_params = normalize_cursor_params(params)
    return {
        "id": model_id,
        "label": str(label or model_selection_label(model_id, normalized_params)).strip() or model_id,
        "params": normalized_params,
        "kind": kind,
        "base_model": str(base_model or model_id).strip() or model_id,
        "parameters": parameters or [],
        "variants": variants or [],
    }


def _param_definition(item) -> dict:
    if isinstance(item, dict):
        values = item.get("values") or []
        value_items = []
        for value in values:
            if isinstance(value, dict):
                value_items.append(
                    {
                        "value": str(value.get("value") if value.get("value") is not None else ""),
                        "label": str(value.get("display_name") or value.get("label") or value.get("value") or ""),
                    }
                )
            else:
                value_items.append(
                    {
                        "value": str(getattr(value, "value", value) if value is not None else ""),
                        "label": str(
                            getattr(value, "display_name", None)
                            or getattr(value, "label", None)
                            or getattr(value, "value", value)
                            or ""
                        ),
                    }
                )
        return {
            "id": str(item.get("id") or "").strip(),
            "label": str(item.get("display_name") or item.get("label") or item.get("id") or "").strip(),
            "values": value_items,
        }

    param_id = str(getattr(item, "id", "") or "").strip()
    values = getattr(item, "values", ()) or ()
    value_items = []
    for value in values:
        value_items.append(
            {
                "value": str(getattr(value, "value", value) if value is not None else ""),
                "label": str(
                    getattr(value, "display_name", None)
                    or getattr(value, "label", None)
                    or getattr(value, "value", value)
                    or ""
                ),
            }
        )
    return {
        "id": param_id,
        "label": str(getattr(item, "display_name", None) or getattr(item, "label", None) or param_id).strip(),
        "values": value_items,
    }


def _variant_params(item) -> list[dict[str, str]]:
    if isinstance(item, dict):
        return normalize_cursor_params(item.get("params"))
    return normalize_cursor_params(getattr(item, "params", None))


def _variant_label(model_id: str, variant) -> str:
    if isinstance(variant, dict):
        name = str(variant.get("display_name") or variant.get("label") or variant.get("name") or variant.get("id") or "").strip()
    else:
        name = str(
            getattr(variant, "display_name", None)
            or getattr(variant, "label", None)
            or getattr(variant, "name", None)
            or getattr(variant, "id", None)
            or ""
        ).strip()

    if name:
        return f"{model_id} ({name})"
    params = _variant_params(variant)
    return model_selection_label(model_id, params)


def _serialize_cursor_model(item) -> dict:
    if isinstance(item, str):
        return _model_item(item)

    if isinstance(item, dict):
        model_id = (
            item.get("id")
            or item.get("model")
            or item.get("slug")
            or item.get("name")
            or ""
        )
        label = item.get("label") or item.get("name") or model_id
        parameters = [_param_definition(p) for p in (item.get("parameters") or [])]
        variants = item.get("variants") or []
    else:
        model_id = getattr(item, "id", None) or getattr(item, "model", None) or str(item)
        label = getattr(item, "label", None) or getattr(item, "name", None) or model_id
        parameters = [_param_definition(p) for p in (getattr(item, "parameters", ()) or ())]
        variants = getattr(item, "variants", ()) or ()

    model_id = str(model_id or "").strip()
    if not model_id:
        return {}

    base = _model_item(
        model_id,
        str(label or model_id),
        kind="model",
        base_model=model_id,
        parameters=parameters,
        variants=[
            {
                "label": _variant_label(model_id, variant),
                "params": _variant_params(variant),
            }
            for variant in variants
        ],
    )

    selections = [base]
    for variant in variants:
        params = _variant_params(variant)
        selections.append(
            _model_item(
                model_id,
                _variant_label(model_id, variant),
                params=params,
                kind="variant",
                base_model=model_id,
            )
        )

    return {
        "model": base,
        "selections": selections,
    }


def list_relay_models() -> dict:
    models = [_model_item(name) for name in GUI_MODELS]
    return {
        "success": True,
        "source": "relay",
        "models": models,
        "error": None,
    }


def list_cursor_models() -> dict:
    if not os.environ.get("CURSOR_API_KEY", "").strip():
        return {
            "success": False,
            "source": "cursor",
            "models": [],
            "catalog": [],
            "error": "CURSOR_API_KEY is not set",
        }

    try:
        from cursor_sdk import Cursor
    except ImportError:
        return {
            "success": False,
            "source": "cursor",
            "models": [],
            "catalog": [],
            "error": "cursor-sdk is not installed",
        }

    try:
        raw = Cursor.models.list()
    except Exception as exc:
        return {
            "success": False,
            "source": "cursor",
            "models": [],
            "catalog": [],
            "error": str(exc),
        }

    items = raw
    if isinstance(raw, dict):
        items = raw.get("models") or raw.get("data") or []

    if not isinstance(items, list):
        items = []

    catalog = []
    models = []
    seen = set()

    for item in items:
        serialized = _serialize_cursor_model(item)
        if not serialized:
            continue

        catalog.append(serialized["model"])
        for selection in serialized["selections"]:
            key = json.dumps(
                {"id": selection["id"], "params": selection.get("params") or []},
                sort_keys=True,
            )
            if key in seen:
                continue
            seen.add(key)
            models.append(selection)

    models.sort(key=lambda x: x["label"].lower())
    catalog.sort(key=lambda x: x["label"].lower())

    return {
        "success": True,
        "source": "cursor",
        "models": models,
        "catalog": catalog,
        "error": None,
    }


def list_models_for_source(source: str) -> dict:
    source = str(source or "relay").strip().lower()
    if source == "cursor":
        return list_cursor_models()
    if source == "relay":
        return list_relay_models()
    return {
        "success": False,
        "source": source,
        "models": [],
        "catalog": [],
        "error": f"unknown source: {source}",
    }


if __name__ == "__main__":
    relay = list_relay_models()
    assert relay["success"] is True
    assert any(m["id"] == "mini" for m in relay["models"])

    sample = _serialize_cursor_model(
        {
            "id": "composer-2.5",
            "parameters": [
                {
                    "id": "fast",
                    "display_name": "Fast",
                    "values": [{"value": "false"}, {"value": "true", "display_name": "Fast"}],
                }
            ],
            "variants": [
                {
                    "display_name": "Fast",
                    "params": [{"id": "fast", "value": "true"}],
                }
            ],
        }
    )
    assert len(sample["selections"]) == 2
    assert sample["selections"][1]["params"] == [{"id": "fast", "value": "true"}]

    cursor = list_cursor_models()
    assert "success" in cursor
    print("LIST_LLM_MODELS SELF TEST PASSED")
