"""
GitHub-aware git helpers for clone, remote setup, and push.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from cfg import CFG
from github_config import (
    authenticated_clone_url,
    load_github_config,
    normalize_github_config,
    public_clone_url,
    public_github_config,
    save_github_config,
)

MODULE_METADATA = {
    "name": "github_git",
    "type": "function",
    "description": "Test GitHub auth and run clone/remote/push git operations.",
}

CLONE_SAFE_NAMES = {".git", "agent", "agent_memory"}


def _project_root(root: str | None = None) -> Path:
    return Path(root or CFG.PROJECT_ROOT).resolve()


def _sanitize_output(text: str, secrets: list[str]) -> str:
    out = str(text or "")
    for secret in secrets:
        value = str(secret or "").strip()
        if not value:
            continue
        out = out.replace(value, "***")
        if value.startswith("ghp_") or value.startswith("github_pat_"):
            out = re.sub(rf"{re.escape(value[:4])}[A-Za-z0-9_]+", "***", out)
    return out


def _run_git(
    args: list[str],
    cwd: Path,
    *,
    timeout: int = 300,
    secrets: list[str] | None = None,
) -> dict:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    scrub = secrets or []
    return {
        "success": proc.returncode == 0,
        "cmd": "git " + " ".join(args),
        "returncode": proc.returncode,
        "stdout": _sanitize_output(proc.stdout, scrub),
        "stderr": _sanitize_output(proc.stderr, scrub),
    }


def _git_available() -> bool:
    try:
        proc = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _list_non_ignored_entries(root: Path) -> list[str]:
    entries = []
    for child in root.iterdir():
        if child.name in CLONE_SAFE_NAMES:
            continue
        entries.append(child.name)
    return entries


def test_github_login(config: dict | None = None) -> dict:
    cfg = normalize_github_config(config or load_github_config())
    token = cfg.get("token") or ""
    if not token:
        raise ValueError("GitHub token required")

    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "gen2-agent-github",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"GitHub auth failed ({exc.code}): {detail}") from exc

    login = str(body.get("login") or "").strip()
    if cfg.get("username") and login and login.lower() != cfg["username"].lower():
        return {
            "success": True,
            "login": login,
            "name": body.get("name") or "",
            "warning": f"token belongs to {login}, not configured username {cfg['username']}",
        }
    return {
        "success": True,
        "login": login,
        "name": body.get("name") or "",
    }


def git_repo_status(root: str | None = None) -> dict:
    root_path = _project_root(root)
    out = {
        "project_root": str(root_path),
        "git_available": _git_available(),
        "is_repo": (root_path / ".git").is_dir(),
        "branch": "",
        "remote": "",
        "remote_url": "",
        "dirty": False,
        "ahead": 0,
        "behind": 0,
        "non_repo_entries": _list_non_ignored_entries(root_path) if root_path.exists() else [],
    }
    if not out["git_available"] or not out["is_repo"]:
        return out

    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], root_path, timeout=30)
    if branch["success"]:
        out["branch"] = (branch["stdout"] or "").strip()

    remote_name = normalize_github_config(load_github_config()).get("remote") or "origin"
    remote = _run_git(["remote", "get-url", remote_name], root_path, timeout=30)
    if remote["success"]:
        out["remote"] = remote_name
        out["remote_url"] = _sanitize_output((remote["stdout"] or "").strip(), [load_github_config().get("token", "")])

    status = _run_git(["status", "--porcelain"], root_path, timeout=30)
    out["dirty"] = bool((status["stdout"] or "").strip())

    if out["branch"] and out["remote"]:
        counts = _run_git(
            ["rev-list", "--left-right", "--count", f"{remote_name}/{out['branch']}...HEAD"],
            root_path,
            timeout=30,
        )
        if counts["success"]:
            parts = (counts["stdout"] or "").strip().split()
            if len(parts) == 2:
                out["behind"] = int(parts[0] or 0)
                out["ahead"] = int(parts[1] or 0)
    return out


def configure_github_remote(config: dict | None = None, root: str | None = None) -> dict:
    cfg = normalize_github_config(config or load_github_config())
    if not cfg.get("repo"):
        raise ValueError("repo required")
    root_path = _project_root(root)
    if not (root_path / ".git").is_dir():
        init = _run_git(["init"], root_path, timeout=30)
        if not init["success"]:
            return {"success": False, "step": "init", **init}

    remote = cfg.get("remote") or "origin"
    url = public_clone_url(cfg)
    existing = _run_git(["remote", "get-url", remote], root_path, timeout=30)
    if existing["success"]:
        set_remote = _run_git(["remote", "set-url", remote, url], root_path, timeout=30)
    else:
        set_remote = _run_git(["remote", "add", remote, url], root_path, timeout=30)
    return {
        "success": set_remote["success"],
        "remote": remote,
        "remote_url": url,
        "git": set_remote,
    }


def clone_github_repo(
    config: dict | None = None,
    root: str | None = None,
    *,
    force: bool = False,
) -> dict:
    cfg = normalize_github_config(config or load_github_config())
    if not cfg.get("repo"):
        raise ValueError("repo required")
    root_path = _project_root(root)
    root_path.mkdir(parents=True, exist_ok=True)

    if (root_path / ".git").is_dir() and not force:
        raise ValueError("project already has a git repository; use force to re-clone")

    blocking = _list_non_ignored_entries(root_path)
    if blocking and not force:
        raise ValueError(
            "project root has existing files outside agent/ and agent_memory/: "
            + ", ".join(blocking[:8])
        )

    if force and (root_path / ".git").is_dir():
        import shutil

        shutil.rmtree(root_path / ".git")

    branch = cfg.get("branch") or "main"
    auth_url = authenticated_clone_url(cfg)
    clone = _run_git(
        ["clone", "--branch", branch, "--single-branch", auth_url, "."],
        root_path,
        timeout=600,
        secrets=[cfg.get("token", "")],
    )
    result = {
        "success": clone["success"],
        "branch": branch,
        "remote_url": public_clone_url(cfg),
        "git": clone,
    }
    if clone["success"]:
        configure_github_remote(cfg, str(root_path))
    return result


def push_github_repo(
    config: dict | None = None,
    root: str | None = None,
    *,
    message: str | None = None,
    add_all: bool = True,
) -> dict:
    cfg = normalize_github_config(config or load_github_config())
    if not cfg.get("token"):
        raise ValueError("GitHub token required")
    root_path = _project_root(root)
    if not (root_path / ".git").is_dir():
        raise ValueError("not a git repository")

    remote_cfg = configure_github_remote(cfg, str(root_path))
    if not remote_cfg.get("success"):
        return {"success": False, "step": "remote", **remote_cfg}

    steps = []
    if add_all:
        add = _run_git(["add", "-A"], root_path, timeout=120, secrets=[cfg.get("token", "")])
        steps.append({"step": "add", **add})
        if not add["success"]:
            return {"success": False, "steps": steps}

    if message:
        commit = _run_git(
            ["commit", "-m", str(message).strip()],
            root_path,
            timeout=120,
            secrets=[cfg.get("token", "")],
        )
        steps.append({"step": "commit", **commit})
        if not commit["success"] and "nothing to commit" not in (commit["stdout"] + commit["stderr"].lower()):
            return {"success": False, "steps": steps}

    branch = cfg.get("branch") or "main"
    current = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], root_path, timeout=30)
    if current["success"] and (current["stdout"] or "").strip():
        branch = (current["stdout"] or "").strip()

    remote = cfg.get("remote") or "origin"
    auth_url = authenticated_clone_url(cfg)
    _run_git(
        ["remote", "set-url", remote, auth_url],
        root_path,
        timeout=30,
        secrets=[cfg.get("token", "")],
    )
    push = subprocess.run(
        ["git", "push", "-u", remote, branch],
        cwd=str(root_path),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    _run_git(["remote", "set-url", remote, public_clone_url(cfg)], root_path, timeout=30)
    push_out = {
        "success": push.returncode == 0,
        "cmd": f"git push -u {remote} {branch}",
        "returncode": push.returncode,
        "stdout": _sanitize_output(push.stdout, [cfg.get("token", "")]),
        "stderr": _sanitize_output(push.stderr, [cfg.get("token", "")]),
    }
    steps.append({"step": "push", **push_out})
    return {
        "success": push_out["success"],
        "branch": branch,
        "remote": remote,
        "steps": steps,
    }


def github_bootstrap(root: str | None = None) -> dict:
    cfg = load_github_config()
    return {
        "success": True,
        "config": public_github_config(cfg),
        "status": git_repo_status(root),
        "public_clone_url": public_clone_url(cfg) if cfg.get("repo") else "",
    }


def github_save_and_bootstrap(data: dict, root: str | None = None) -> dict:
    saved = save_github_config(data.get("config") if isinstance(data, dict) else data)
    out = github_bootstrap(root)
    out["saved"] = public_github_config(saved)
    return out


if __name__ == "__main__":
    root = _project_root()
    print(json.dumps(github_bootstrap(str(root)), indent=2))
    print("GITHUB_GIT MODULE LOADED")
