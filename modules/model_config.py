"""
Global per-role LLM configuration for Gen2 agent components.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import contextvars

from cfg import CFG
from safe_path import safe_path

MODULE_METADATA = {
    "name": "model_config",
    "type": "function",
    "description": "Load, save, and resolve global per-role LLM settings (source, model, effort, max_tokens).",
    "functions": [
        {
            "name": "get_role_config",
            "inputs": {"role": "str role name"},
            "outputs": "dict with source, model, effort, max_tokens",
        },
        {
            "name": "load_model_config",
            "inputs": {},
            "outputs": "dict with roles mapping",
        },
        {
            "name": "save_model_config",
            "inputs": {"config": "dict"},
            "outputs": "dict saved config",
        },
    ],
}

LLM_SOURCES = ("relay", "cursor")

LLM_ROLES = (
    "main_planner",
    "subagent_explore",
    "subagent_review",
    "subagent_implement",
    "meta_writer",
    "verifier",
    "local_repair",
    "debug",
    "context_rewriter",
    "discussion",
)

ROLE_LABELS = {
    "main_planner": "Main planner",
    "subagent_explore": "Subagent — explore",
    "subagent_review": "Subagent — review",
    "subagent_implement": "Subagent — implement",
    "meta_writer": "Meta writer",
    "verifier": "Verifier",
    "local_repair": "Local repair",
    "debug": "Debug",
    "context_rewriter": "Task rewriter",
    "discussion": "Discussion mode",
}

PARSE_FALLBACK_KINDS = (
    "execution",
    "verifier",
)

PARSE_FALLBACK_LABELS = {
    "execution": "Execution (planner + debug)",
    "verifier": "Verifier",
}

CONFIG_REL_PATH = "agent_memory/model_config.json"
SUBAGENT_ROLES = ("subagent_explore", "subagent_review", "subagent_implement")
_role_overrides: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "llm_role_overrides",
    default=None,
)


def _config_path() -> Path:
    return Path(safe_path(CONFIG_REL_PATH))


def normalize_effort(effort) -> str:
    if effort is None:
        effort = getattr(CFG, "DEFAULT_EFFORT", "m")

    value = str(effort).strip().lower()
    if value == "l":
        return "low"
    if value == "m":
        return "medium"
    if value == "h":
        return "high"
    if value in ("low", "medium", "high"):
        return value
    return "medium"


def effort_selector_value(effort) -> str:
    normalized = normalize_effort(effort)
    return {"low": "l", "medium": "m", "high": "h"}[normalized]


def _default_role_config(role: str) -> dict:
    defaults = {
        "source": "relay",
        "model": getattr(CFG, "DEFAULT_MODEL", "mini"),
        "effort": effort_selector_value(getattr(CFG, "DEFAULT_EFFORT", "m")),
        "max_tokens": int(getattr(CFG, "DEFAULT_MAX_TOKENS", 16384)),
    }

    if role == "meta_writer":
        defaults.update(
            {
                "model": getattr(CFG, "METAWRITER_MODEL", "mini"),
                "effort": effort_selector_value(getattr(CFG, "METAWRITER_EFFORT", "m")),
                "max_tokens": int(getattr(CFG, "METAWRITER_TOKENS", 4096)),
            }
        )
    elif role == "context_rewriter":
        defaults.update(
            {
                "model": getattr(CFG, "BACKGROUND_CONTEXT_MODEL", "mini"),
                "effort": effort_selector_value(getattr(CFG, "BACKGROUND_CONTEXT_EFFORT", "l")),
                "max_tokens": int(getattr(CFG, "BACKGROUND_CONTEXT_MAX_TOKENS", 4096)),
            }
        )
    elif role == "local_repair":
        defaults.update({"max_tokens": 8192})
    elif role == "verifier":
        defaults.update({"effort": "l", "max_tokens": 4096})
    elif role == "discussion":
        defaults.update({"effort": "l"})
    elif role == "subagent_explore":
        defaults.update({"effort": "l", "max_tokens": 8192})
    elif role == "subagent_review":
        defaults.update({"effort": "m", "max_tokens": 8192})

    return defaults


def _default_parse_fallback(kind: str) -> dict:
    if kind == "verifier":
        return _default_role_config("verifier")
    return _default_role_config("main_planner")


def default_model_config() -> dict:
    return {
        "roles": {role: _default_role_config(role) for role in LLM_ROLES},
        "parse_fallbacks": {
            kind: _default_parse_fallback(kind) for kind in PARSE_FALLBACK_KINDS
        },
    }


def _normalize_role_entry(entry: dict | None, role: str) -> dict:
    from cursor_model_selection import normalize_cursor_params

    base = _default_role_config(role)
    entry = entry if isinstance(entry, dict) else {}

    source = str(entry.get("source") or base["source"]).strip().lower()
    if source not in LLM_SOURCES:
        source = base["source"]

    model = str(entry.get("model") or base["model"]).strip() or base["model"]
    effort = effort_selector_value(entry.get("effort") or base["effort"])
    cursor_params = normalize_cursor_params(entry.get("cursor_params"))

    try:
        max_tokens = int(entry.get("max_tokens") if entry.get("max_tokens") is not None else base["max_tokens"])
    except Exception:
        max_tokens = int(base["max_tokens"])

    if max_tokens <= 0:
        max_tokens = int(base["max_tokens"])

    out = {
        "source": source,
        "model": model,
        "effort": effort,
        "max_tokens": max_tokens,
    }
    if source == "cursor" and cursor_params:
        out["cursor_params"] = cursor_params
    return out


def normalize_model_config(config: dict | None) -> dict:
    base = default_model_config()
    config = config if isinstance(config, dict) else {}
    roles_in = config.get("roles") if isinstance(config.get("roles"), dict) else {}

    roles_out = {}
    for role in LLM_ROLES:
        roles_out[role] = _normalize_role_entry(roles_in.get(role), role)

    fallbacks_in = (
        config.get("parse_fallbacks")
        if isinstance(config.get("parse_fallbacks"), dict)
        else {}
    )
    fallbacks_out = {}
    for kind in PARSE_FALLBACK_KINDS:
        template_role = "verifier" if kind == "verifier" else "main_planner"
        fallbacks_out[kind] = _normalize_role_entry(
            fallbacks_in.get(kind),
            template_role,
        )

    return {
        "roles": roles_out,
        "parse_fallbacks": fallbacks_out,
    }


def load_model_config() -> dict:
    path = _config_path()
    if not path.exists():
        return default_model_config()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_model_config()

    return normalize_model_config(raw)


def save_model_config(config: dict) -> dict:
    normalized = normalize_model_config(config)
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(normalized, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return normalized


def get_parse_fallback(kind: str) -> dict:
    kind = str(kind or "").strip()
    if kind not in PARSE_FALLBACK_KINDS:
        raise ValueError(f"unknown parse fallback kind: {kind}")

    config = load_model_config()
    entry = (config.get("parse_fallbacks") or {}).get(kind) or _default_parse_fallback(kind)
    template_role = "verifier" if kind == "verifier" else "main_planner"
    entry = _normalize_role_entry(entry, template_role)
    entry = deepcopy(entry)
    entry["effort_normalized"] = normalize_effort(entry["effort"])
    return entry


def normalize_role_overrides(overrides: dict | None) -> dict:
    raw = overrides if isinstance(overrides, dict) else {}
    cleaned = {}
    for role, entry in raw.items():
        role_id = str(role or "").strip()
        if role_id not in LLM_ROLES:
            continue
        cleaned[role_id] = _normalize_role_entry(entry, role_id)
    return cleaned


@contextmanager
def role_override_scope(overrides: dict | None):
    token = _role_overrides.set(normalize_role_overrides(overrides) or None)
    try:
        yield
    finally:
        _role_overrides.reset(token)


def get_role_config(role: str) -> dict:
    role = str(role or "").strip()
    if role not in LLM_ROLES:
        raise ValueError(f"unknown LLM role: {role}")

    overrides = _role_overrides.get() or {}
    if role in overrides:
        entry = deepcopy(overrides[role])
    else:
        config = load_model_config()
        entry = config.get("roles", {}).get(role) or _default_role_config(role)
        entry = _normalize_role_entry(entry, role)
        entry = deepcopy(entry)
    entry["effort_normalized"] = normalize_effort(entry["effort"])
    return entry


def role_config_summary() -> dict:
    from cursor_model_selection import model_selection_label, normalize_cursor_params

    config = load_model_config()
    out = {}
    for role in LLM_ROLES:
        entry = config["roles"][role]
        model_label = entry["model"]
        if entry.get("source") == "cursor":
            model_label = model_selection_label(entry["model"], entry.get("cursor_params"))
        out[role] = {
            **entry,
            "label": ROLE_LABELS.get(role, role),
            "effort_label": normalize_effort(entry["effort"]),
            "model_label": model_label,
        }
    return out


def parse_fallback_summary() -> dict:
    from cursor_model_selection import model_selection_label

    config = load_model_config()
    out = {}
    for kind in PARSE_FALLBACK_KINDS:
        entry = config["parse_fallbacks"][kind]
        model_label = entry["model"]
        if entry.get("source") == "cursor":
            model_label = model_selection_label(entry["model"], entry.get("cursor_params"))
        out[kind] = {
            **entry,
            "label": PARSE_FALLBACK_LABELS.get(kind, kind),
            "effort_label": normalize_effort(entry["effort"]),
            "model_label": model_label,
        }
    return out


if __name__ == "__main__":
    cfg = default_model_config()
    assert set(cfg["roles"]) == set(LLM_ROLES)
    assert set(cfg["parse_fallbacks"]) == set(PARSE_FALLBACK_KINDS)
    assert get_role_config("main_planner")["source"] == "relay"
    with role_override_scope({"subagent_explore": {"model": "override-model", "source": "cursor"}}):
        overridden = get_role_config("subagent_explore")
        assert overridden["model"] == "override-model"
        assert overridden["source"] == "cursor"
    assert get_role_config("subagent_explore")["model"] != "override-model"
    assert get_parse_fallback("execution")["model"]
    saved = save_model_config(cfg)
    assert saved["roles"]["debug"]["model"] == "mini"
    print("MODEL_CONFIG SELF TEST PASSED")
