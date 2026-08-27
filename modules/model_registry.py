"""
Canonical model selectors for Gen2 GUI and LLM routing.
"""

from __future__ import annotations

from cfg import CFG

MODULE_METADATA = {
    "name": "model_registry",
    "type": "function",
    "description": "Normalize GUI model selectors and map them to relay provider/model pairs.",
    "functions": [
        {
            "name": "normalize_model_name",
            "inputs": {"model": "str | None GUI model selector"},
            "outputs": "str canonical model name",
        },
        {
            "name": "normalize_model_provider",
            "inputs": {"model": "str | None GUI model selector"},
            "outputs": "tuple (provider str | None, resolved_model str | None)",
        },
    ],
}

GUI_MODELS = ("mini", "5.3-codex", "pro", "luna", "terra")

MODEL_ALIASES = {
    "nova": "mini",
    "gemini-3.5-flash": "5.3-codex",
}

RELAY_DEFAULT_MODELS = {"mini"}

RELAY_SLUG_MODELS = {"5.3-codex", "luna", "terra"}


def normalize_model_name(model) -> str:
    if model is None:
        model = getattr(CFG, "DEFAULT_MODEL", "mini")
    name = str(model).strip()
    if not name:
        name = getattr(CFG, "DEFAULT_MODEL", "mini")
    return MODEL_ALIASES.get(name, name)


def normalize_model_provider(model) -> tuple[str | None, str | None]:
    name = normalize_model_name(model)

    if name == "pro":
        return "gemini", "gemini-3.1-pro-preview"

    if name in RELAY_DEFAULT_MODELS:
        return None, None

    if name in RELAY_SLUG_MODELS:
        return None, name

    if name.startswith("gemini"):
        return "gemini", name

    return None, name


if __name__ == "__main__":
    assert normalize_model_name("nova") == "mini"
    assert normalize_model_name("gemini-3.5-flash") == "5.3-codex"
    assert normalize_model_provider("mini") == (None, None)
    assert normalize_model_provider("nova") == (None, None)
    assert normalize_model_provider("5.3-codex") == (None, "5.3-codex")
    assert normalize_model_provider("luna") == (None, "luna")
    assert normalize_model_provider("terra") == (None, "terra")
    assert normalize_model_provider("pro") == ("gemini", "gemini-3.1-pro-preview")
    print("MODEL_REGISTRY SELF TEST PASSED")
