"""
Canonical model selectors for Gen2 GUI and LLM routing.
"""

from __future__ import annotations

from cfg import CFG
from opencode_registry import DEFAULT_OPENCODE_MODEL, normalize_model_id

MODULE_METADATA = {
    "name": "model_registry",
    "type": "function",
    "description": "Normalize GUI model selectors for OpenCode and Cursor sources.",
    "functions": [
        {
            "name": "normalize_model_name",
            "inputs": {"model": "str | None GUI model selector"},
            "outputs": "str canonical model name",
        },
    ],
}


def normalize_model_name(model) -> str:
    if model is None:
        model = getattr(CFG, "DEFAULT_MODEL", DEFAULT_OPENCODE_MODEL)
    name = normalize_model_id(model)
    if not name:
        name = getattr(CFG, "DEFAULT_MODEL", DEFAULT_OPENCODE_MODEL)
    return name


if __name__ == "__main__":
    assert normalize_model_name(None) == DEFAULT_OPENCODE_MODEL
    assert normalize_model_name("opencode-go/deepseek-v4-flash") == "deepseek-v4-flash"
    print("MODEL_REGISTRY SELF TEST PASSED")
