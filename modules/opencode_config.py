"""
Persist OpenCode Go settings for the web GUI.

An empty saved API key is valid: runtime checks OPENCODE_API_KEY /
OPENCODE_ZEN_API_KEY in the environment, and falls back to a silent dummy
credential when none are configured. Base URL resolves from OPENCODE_GO_BASE_URL
when set, otherwise CFG.OPENCODE_GO_BASE_URL.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

from cfg import CFG

DUMMY_OPENCODE_API_KEY = "dummy-not-configured"
DUMMY_OPENCODE_BASE_URL = "http://127.0.0.1:0/opencode-dummy"
_OPENCODE_API_KEY_ENV_VARS = ("OPENCODE_API_KEY", "OPENCODE_ZEN_API_KEY")
_OPENCODE_BASE_URL_ENV_VAR = "OPENCODE_GO_BASE_URL"

MODULE_METADATA = {
    "name": "opencode_config",
    "type": "function",
    "description": "Load and save OpenCode Go API settings used by the web GUI.",
    "functions": [
        {"name": "load_opencode_config", "inputs": {}, "outputs": "dict"},
        {"name": "save_opencode_config", "inputs": {"config": "dict"}, "outputs": "dict"},
        {"name": "public_opencode_config", "inputs": {}, "outputs": "dict"},
        {"name": "get_opencode_api_key", "inputs": {}, "outputs": "str"},
        {"name": "get_opencode_base_url", "inputs": {}, "outputs": "str"},
        {"name": "is_opencode_api_key_configured", "inputs": {}, "outputs": "bool"},
    ],
}

CONFIG_REL_PATH = "agent_memory/opencode_config.json"


def _config_path() -> Path:
    return Path(CFG.PROJECT_ROOT) / CONFIG_REL_PATH


def default_opencode_config() -> dict:
    return {
        "api_key": "",
    }


def normalize_opencode_config(config: dict | None) -> dict:
    raw = config if isinstance(config, dict) else {}
    return {
        "api_key": str(raw.get("api_key") or "").strip(),
    }


def mask_api_key(api_key: str) -> str:
    text = str(api_key or "").strip()
    if not text:
        return ""
    if len(text) <= 4:
        return "*" * len(text)
    return f"{'*' * 4}...{text[-4:]}"


def public_opencode_config(config: dict | None = None) -> dict:
    cfg = normalize_opencode_config(config if config is not None else load_opencode_config())
    return {
        "api_key_masked": mask_api_key(cfg["api_key"]),
        "api_key_configured": is_opencode_api_key_configured(cfg),
    }


def load_opencode_config() -> dict:
    path = _config_path()
    if not path.exists():
        return default_opencode_config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_opencode_config()
    return normalize_opencode_config(raw)


def save_opencode_config(config: dict) -> dict:
    current = load_opencode_config()
    incoming = normalize_opencode_config(config)
    merged = deepcopy(current)
    raw = config if isinstance(config, dict) else {}
    if raw.get("clear_api_key"):
        merged["api_key"] = ""
    elif "api_key" in raw:
        value = incoming["api_key"]
        if value or not current.get("api_key"):
            merged["api_key"] = value
    normalized = normalize_opencode_config(merged)
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(normalized, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return normalized


def _saved_opencode_api_key(config: dict | None = None) -> str:
    cfg = normalize_opencode_config(config if config is not None else load_opencode_config())
    return str(cfg.get("api_key") or "").strip()


def _env_opencode_api_key() -> str:
    for env_name in _OPENCODE_API_KEY_ENV_VARS:
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    return ""


def is_opencode_api_key_configured(config: dict | None = None) -> bool:
    return bool(_saved_opencode_api_key(config) or _env_opencode_api_key())


def get_opencode_api_key() -> str:
    key = _saved_opencode_api_key() or _env_opencode_api_key()
    return key or DUMMY_OPENCODE_API_KEY


def get_opencode_base_url() -> str:
    env = os.environ.get(_OPENCODE_BASE_URL_ENV_VAR, "").strip()
    if env:
        return env.rstrip("/")
    cfg_default = str(getattr(CFG, "OPENCODE_GO_BASE_URL", "") or "").strip()
    return cfg_default.rstrip("/") if cfg_default else DUMMY_OPENCODE_BASE_URL


if __name__ == "__main__":
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp()
    try:
        old = CFG.PROJECT_ROOT
        CFG.PROJECT_ROOT = tmp
        assert save_opencode_config({})["api_key"] == ""
        saved = save_opencode_config({"api_key": "oc_test_key_123"})
        assert saved["api_key"] == "oc_test_key_123"
        pub = public_opencode_config(saved)
        assert pub["api_key_configured"] is True
        assert "oc_test" not in pub["api_key_masked"]
        assert save_opencode_config({"clear_api_key": True})["api_key"] == ""
        assert is_opencode_api_key_configured() is False
        assert get_opencode_api_key() == DUMMY_OPENCODE_API_KEY
        assert save_opencode_config({"api_key": "oc_new"})["api_key"] == "oc_new"
        assert save_opencode_config({"api_key": ""})["api_key"] == "oc_new"
        CFG.PROJECT_ROOT = old
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("OPENCODE_CONFIG SELF TEST PASSED")
