"""
Persist GitHub credentials and repository settings for GUI git operations.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

from cfg import CFG

MODULE_METADATA = {
    "name": "github_config",
    "type": "function",
    "description": "Load and save GitHub login/repo settings used by the web GUI.",
    "functions": [
        {"name": "load_github_config", "inputs": {}, "outputs": "dict"},
        {"name": "save_github_config", "inputs": {"config": "dict"}, "outputs": "dict"},
        {"name": "public_github_config", "inputs": {}, "outputs": "dict"},
    ],
}

CONFIG_REL_PATH = "agent_memory/github_config.json"
REPO_RE = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")


def _config_path() -> Path:
    return Path(CFG.PROJECT_ROOT) / CONFIG_REL_PATH


def default_github_config() -> dict:
    return {
        "username": "",
        "token": "",
        "repo": "",
        "branch": "main",
        "remote": "origin",
        "clone_url": "",
    }


def _normalize_repo(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("http://") or text.startswith("https://") or text.startswith("git@"):
        parsed = urlparse(text.replace("git@", "https://", 1) if text.startswith("git@") else text)
        path = str(parsed.path or "").strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        if path.count("/") >= 1:
            owner, name = path.split("/", 1)
            return f"{owner}/{name}"
        return ""
    if text.endswith(".git"):
        text = text[:-4]
    if REPO_RE.match(text):
        return text
    return ""


def normalize_github_config(config: dict | None) -> dict:
    base = default_github_config()
    raw = config if isinstance(config, dict) else {}
    out = deepcopy(base)
    out["username"] = str(raw.get("username") or "").strip()
    out["token"] = str(raw.get("token") or "").strip()
    out["repo"] = _normalize_repo(raw.get("repo") or "")
    out["branch"] = str(raw.get("branch") or "main").strip() or "main"
    out["remote"] = str(raw.get("remote") or "origin").strip() or "origin"
    out["clone_url"] = str(raw.get("clone_url") or "").strip()
    return out


def mask_token(token: str) -> str:
    text = str(token or "").strip()
    if not text:
        return ""
    if len(text) <= 4:
        return "*" * len(text)
    return f"{'*' * 4}...{text[-4:]}"


def public_github_config(config: dict | None = None) -> dict:
    cfg = normalize_github_config(config if config is not None else load_github_config())
    return {
        "username": cfg["username"],
        "token_masked": mask_token(cfg["token"]),
        "token_configured": bool(cfg["token"]),
        "repo": cfg["repo"],
        "branch": cfg["branch"],
        "remote": cfg["remote"],
        "clone_url": cfg["clone_url"],
    }


def load_github_config() -> dict:
    path = _config_path()
    if not path.exists():
        return default_github_config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_github_config()
    return normalize_github_config(raw)


def save_github_config(config: dict) -> dict:
    current = load_github_config()
    incoming = normalize_github_config(config)
    merged = deepcopy(current)
    for key in default_github_config():
        if key not in incoming:
            continue
        value = incoming[key]
        if key == "token" and not value and current.get("token"):
            continue
        merged[key] = value
    normalized = normalize_github_config(merged)
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(normalized, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return normalized


def repo_parts(config: dict | None = None) -> tuple[str, str]:
    cfg = normalize_github_config(config or load_github_config())
    repo = cfg.get("repo") or ""
    if "/" not in repo:
        raise ValueError("repo must be owner/name")
    owner, name = repo.split("/", 1)
    return owner, name


def public_clone_url(config: dict | None = None) -> str:
    cfg = normalize_github_config(config or load_github_config())
    if cfg.get("clone_url"):
        return cfg["clone_url"]
    owner, name = repo_parts(cfg)
    return f"https://github.com/{owner}/{name}.git"


def authenticated_clone_url(config: dict | None = None) -> str:
    cfg = normalize_github_config(config or load_github_config())
    token = cfg.get("token") or ""
    if not token:
        raise ValueError("GitHub token required")
    base = public_clone_url(cfg)
    if base.startswith("https://"):
        return f"https://x-access-token:{token}@{base[len('https://'):]}"
    if base.startswith("git@github.com:"):
        return f"https://x-access-token:{token}@github.com/{base.split(':', 1)[1]}"
    return base


if __name__ == "__main__":
    import tempfile
    import shutil

    tmp = tempfile.mkdtemp()
    try:
        old = CFG.PROJECT_ROOT
        CFG.PROJECT_ROOT = tmp
        cfg = save_github_config(
            {
                "username": "demo",
                "token": "ghp_abcdefghijklmnop",
                "repo": "https://github.com/acme/widget.git",
                "branch": "dev",
            }
        )
        assert cfg["repo"] == "acme/widget"
        assert cfg["branch"] == "dev"
        pub = public_github_config(cfg)
        assert pub["token_configured"] is True
        assert "ghp_" not in pub["token_masked"]
        assert authenticated_clone_url(cfg).startswith("https://x-access-token:")
        CFG.PROJECT_ROOT = old
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("GITHUB_CONFIG SELF TEST PASSED")
