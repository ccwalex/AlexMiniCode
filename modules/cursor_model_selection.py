"""
Helpers for Cursor SDK model ids and per-model parameter selections.
"""

from __future__ import annotations

import json

MODULE_METADATA = {
    "name": "cursor_model_selection",
    "type": "function",
    "description": "Parse and normalize Cursor model ids with optional per-model params.",
    "functions": [
        {
            "name": "normalize_cursor_params",
            "inputs": {"params": "list | None"},
            "outputs": "list[dict[str, str]]",
        },
        {
            "name": "parse_model_selection",
            "inputs": {"model": "str | dict | None", "cursor_params": "list | None"},
            "outputs": "dict with id and params",
        },
    ],
}


CURSOR_FAST_PARAM_ID = "fast"
CURSOR_FAST_DISABLED_VALUE = "false"


def normalize_cursor_params(params) -> list[dict[str, str]]:
    out = []
    if not isinstance(params, list):
        return out

    for item in params:
        if not isinstance(item, dict):
            continue
        param_id = str(item.get("id") or "").strip()
        value = str(item.get("value") if item.get("value") is not None else "").strip()
        if param_id:
            out.append({"id": param_id, "value": value})
    return out


def _param_id(item: dict) -> str:
    return str((item or {}).get("id") or "").strip().lower()


def apply_cursor_runtime_params(model_id: str, cursor_params=None) -> list[dict[str, str]]:
    """
    Normalize Cursor params for Gen2 runtime calls.

    Strip any fast=true selection and force fast=false for Composer, Grok,
    and all other Cursor models.
    """
    _ = str(model_id or "").strip()
    params = [
        item
        for item in normalize_cursor_params(cursor_params)
        if _param_id(item) != CURSOR_FAST_PARAM_ID
    ]
    params.append({"id": CURSOR_FAST_PARAM_ID, "value": CURSOR_FAST_DISABLED_VALUE})
    return params


def build_cursor_model_value(model_id: str, cursor_params=None):
    """
    Build a Cursor SDK model value with runtime params applied.

    Always returns a ModelSelection when runtime params are present.
    """
    model_id = str(model_id or "").strip() or "auto"
    params = apply_cursor_runtime_params(model_id, cursor_params)

    if not params:
        return model_id

    try:
        from cursor_sdk import ModelParameterValue, ModelSelection
    except ImportError as exc:
        raise RuntimeError(
            "cursor-sdk is not installed. Install with: pip install cursor-sdk"
        ) from exc

    return ModelSelection(
        id=model_id,
        params=[ModelParameterValue(id=item["id"], value=item["value"]) for item in params],
    )


def parse_model_selection(model=None, cursor_params=None) -> dict:
    """
    Return a normalized Cursor model selection.

    Accepts:
    - plain model id string
    - JSON object string: {"id": "...", "params": [...]}
    - dict with id/params
    """
    params = normalize_cursor_params(cursor_params)
    model_id = ""

    if isinstance(model, dict):
        model_id = str(model.get("id") or model.get("model") or "").strip()
        if not params:
            params = normalize_cursor_params(model.get("params"))
    else:
        raw = str(model or "").strip()
        if raw.startswith("{"):
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                model_id = str(parsed.get("id") or parsed.get("model") or "").strip()
                if not params:
                    params = normalize_cursor_params(parsed.get("params"))
            else:
                model_id = raw
        else:
            model_id = raw

    if not model_id:
        model_id = "auto"

    return {
        "id": model_id,
        "params": params,
    }


def model_selection_label(model_id: str, params: list[dict[str, str]] | None = None) -> str:
    model_id = str(model_id or "").strip() or "auto"
    params = normalize_cursor_params(params)
    if not params:
        return model_id

    bits = []
    for item in params:
        param_id = item.get("id") or ""
        value = item.get("value") or ""
        if param_id and value:
            bits.append(f"{param_id}={value}")
        elif param_id:
            bits.append(param_id)

    suffix = ", ".join(bits)
    return f"{model_id} ({suffix})" if suffix else model_id


def selections_equal(left: dict, right: dict) -> bool:
    left_id = str((left or {}).get("id") or "").strip()
    right_id = str((right or {}).get("id") or "").strip()
    return left_id == right_id and normalize_cursor_params((left or {}).get("params")) == normalize_cursor_params(
        (right or {}).get("params")
    )


if __name__ == "__main__":
    assert parse_model_selection("composer-2.5") == {
        "id": "composer-2.5",
        "params": [],
    }
    assert apply_cursor_runtime_params(
        "composer-2.5",
        [{"id": "fast", "value": "true"}],
    ) == [{"id": "fast", "value": "false"}]
    assert apply_cursor_runtime_params("grok-4.5") == [{"id": "fast", "value": "false"}]
    assert parse_model_selection(
        '{"id":"composer-2.5","params":[{"id":"fast","value":"true"}]}'
    ) == {
        "id": "composer-2.5",
        "params": [{"id": "fast", "value": "true"}],
    }
    assert model_selection_label("composer-2.5", [{"id": "fast", "value": "true"}]) == "composer-2.5 (fast=true)"
    print("CURSOR_MODEL_SELECTION SELF TEST PASSED")
