"""
Initialize git for the project root when available and ensure agent paths are ignored.
"""

from __future__ import annotations

import os
import subprocess

from cfg import CFG

MODULE_METADATA = {
    "name": "ensure_project_git",
    "type": "function",
    "description": "Run git init when needed and ensure agent/ and agent_memory/ are listed in .gitignore.",
    "functions": [
        {
            "name": "ensure_project_git",
            "inputs": {"project_root": "str | None project root; defaults to CFG.PROJECT_ROOT"},
            "outputs": "dict with git setup status",
        }
    ],
}

AGENT_IGNORE_ENTRIES = (
    "agent/",
    "agent_memory/",
)
AGENT_IGNORE_SECTION = "# Agent harness and runtime memory"


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


def _has_git_repo(root: str) -> bool:
    return os.path.isdir(os.path.join(root, ".git"))


def _normalize_ignore_name(entry: str) -> str:
    return str(entry or "").strip().rstrip("/")


def _gitignore_has_entry(lines: list[str], entry: str) -> bool:
    target = _normalize_ignore_name(entry)
    for line in lines:
        text = str(line or "").strip()
        if not text or text.startswith("#"):
            continue
        normalized = _normalize_ignore_name(text)
        if normalized == target:
            return True
        if normalized in {f"**/{target}", f"{target}/**"}:
            return True
        if normalized.endswith(f"/{target}") or normalized.endswith(target):
            return True
    return False


def _ensure_gitignore(root: str) -> list[str]:
    path = os.path.join(root, ".gitignore")
    lines: list[str] = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()

    added: list[str] = []
    for entry in AGENT_IGNORE_ENTRIES:
        if _gitignore_has_entry(lines, entry):
            continue
        added.append(entry)

    if not added:
        return added

    if lines and lines[-1].strip():
        lines.append("")
    if not any(line.strip() == AGENT_IGNORE_SECTION for line in lines):
        lines.append(AGENT_IGNORE_SECTION)
    lines.extend(added)

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")
    return added


def ensure_project_git(project_root=None) -> dict:
    root = os.path.abspath(str(project_root or CFG.PROJECT_ROOT))
    result = {
        "success": True,
        "project_root": root,
        "git_available": False,
        "repo_initialized": False,
        "gitignore_updated": False,
        "gitignore_added": [],
        "already_repo": False,
        "message": "",
    }

    if not _git_available():
        result["message"] = "git not available; skipped project git setup"
        return result

    result["git_available"] = True

    if _has_git_repo(root):
        result["already_repo"] = True
    else:
        proc = subprocess.run(
            ["git", "init"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            result["success"] = False
            result["message"] = (proc.stderr or proc.stdout or "git init failed").strip()
            return result
        result["repo_initialized"] = True

    added = _ensure_gitignore(root)
    result["gitignore_added"] = added
    result["gitignore_updated"] = bool(added)

    if result["repo_initialized"] and result["gitignore_updated"]:
        result["message"] = (
            "initialized git repository and added "
            + ", ".join(added)
            + " to .gitignore"
        )
    elif result["repo_initialized"]:
        result["message"] = "initialized git repository"
    elif result["gitignore_updated"]:
        result["message"] = "added " + ", ".join(added) + " to .gitignore"
    else:
        result["message"] = "git repository ready"
    return result


if __name__ == "__main__":
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp()
    try:
        out = ensure_project_git(tmp)
        assert out["git_available"] is True
        assert out["repo_initialized"] is True
        assert out["gitignore_updated"] is True
        assert set(out["gitignore_added"]) == set(AGENT_IGNORE_ENTRIES)
        assert os.path.isdir(os.path.join(tmp, ".git"))
        with open(os.path.join(tmp, ".gitignore"), "r", encoding="utf-8") as f:
            body = f.read()
        for entry in AGENT_IGNORE_ENTRIES:
            assert entry in body

        out2 = ensure_project_git(tmp)
        assert out2["already_repo"] is True
        assert out2["repo_initialized"] is False
        assert out2["gitignore_updated"] is False
        assert out2["gitignore_added"] == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("ENSURE_PROJECT_GIT SELF TEST PASSED")
