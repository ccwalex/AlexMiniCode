#!/usr/bin/env python3
"""Refresh an existing agent/ tree from GitHub without touching parent agent_memory/.

Usage:
  python3 update_from_github.py
  python3 update_from_github.py /path/to/project/agent
  python3 update_from_github.py --branch main --repo ccwalex/AlexMiniCode

Local modules/cfg.py and .env* are restored after the download.
"""
from __future__ import annotations

import argparse
import os
import shutil
import ssl
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

DEFAULT_REPO = "ccwalex/AlexMiniCode"
DEFAULT_BRANCH = "main"
KEEP_TOP = {".git", ".env"}
BACKUP_RELATIVE = [
    "modules/cfg.py",
]


def _zip_url(repo: str, branch: str) -> str:
    return f"https://github.com/{repo}/archive/refs/heads/{branch}.zip"


def _download(url: str, dest: Path) -> None:
    headers = {"User-Agent": "update_from_github.py"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    context = ssl.create_default_context()
    with urllib.request.urlopen(req, context=context, timeout=120) as resp, dest.open("wb") as out:
        shutil.copyfileobj(resp, out)


def _extracted_root(extract_dir: Path) -> Path:
    kids = [p for p in extract_dir.iterdir() if p.is_dir()]
    if len(kids) != 1:
        raise RuntimeError(f"expected one folder in zip, found {len(kids)}")
    return kids[0]


def _backup(agent_dir: Path) -> dict[str, bytes]:
    saved = {}
    for rel in BACKUP_RELATIVE:
        path = agent_dir / rel
        if path.is_file():
            saved[rel] = path.read_bytes()
    for path in agent_dir.glob(".env*"):
        if path.is_file():
            saved[path.name] = path.read_bytes()
    return saved


def _restore(agent_dir: Path, saved: dict[str, bytes]) -> None:
    for rel, data in saved.items():
        path = agent_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _clear_agent(agent_dir: Path) -> None:
    for child in agent_dir.iterdir():
        if child.name in KEEP_TOP or child.name.startswith(".env"):
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _copy_tree(src: Path, dest: Path) -> None:
    for child in src.iterdir():
        target = dest / child.name
        if child.name in KEEP_TOP or child.name.startswith(".env"):
            continue
        if child.is_dir():
            shutil.copytree(child, target)
        else:
            shutil.copy2(child, target)


def update_agent(agent_dir: Path, repo: str, branch: str) -> None:
    agent_dir = agent_dir.resolve()
    if not agent_dir.is_dir():
        raise SystemExit(f"not a directory: {agent_dir}")
    if not (agent_dir / "agent.py").is_file() and not (agent_dir / "modules").is_dir():
        raise SystemExit(f"does not look like an agent/: {agent_dir}")

    saved = _backup(agent_dir)
    url = _zip_url(repo, branch)
    print(f"Downloading {url}")

    with tempfile.TemporaryDirectory(prefix="agent-update-") as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / "agent.zip"
        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()
        _download(url, zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)
        new_root = _extracted_root(extract_dir)
        _clear_agent(agent_dir)
        _copy_tree(new_root, agent_dir)

    _restore(agent_dir, saved)
    kept = ", ".join(saved) if saved else "(none)"
    print(f"Updated {agent_dir} from {repo}@{branch}")
    print(f"Restored local files: {kept}")
    print("Parent agent_memory/ was not modified.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download the latest agent from GitHub into this folder.")
    parser.add_argument(
        "agent_dir",
        nargs="?",
        default=str(Path(__file__).resolve().parent),
        help="Path to the agent/ directory (default: this script's folder)",
    )
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"owner/name (default: {DEFAULT_REPO})")
    parser.add_argument("--branch", default=DEFAULT_BRANCH, help=f"branch (default: {DEFAULT_BRANCH})")
    args = parser.parse_args()
    update_agent(Path(args.agent_dir), args.repo, args.branch)


if __name__ == "__main__":
    main()
    sys.exit(0)
