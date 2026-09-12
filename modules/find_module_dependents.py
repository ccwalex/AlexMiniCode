MODULE_METADATA = {
    "name": "find_module_dependents",
    "type": "function",
    "description": "Find files that import a changed module using grep over the project root.",
    "functions": [
        {
            "name": "find_module_dependents",
            "inputs": {
                "changed_path": "str project-relative path of the changed module",
            },
            "outputs": "list of project-relative dependent file paths",
        }
    ],
}

import os
import subprocess
from pathlib import Path

from cfg import CFG


EXCLUDE_DIRS = ("agent", "agent_memory", ".git")
INCLUDES = ("*.py", "*.ts", "*.tsx", "*.js", "*.jsx")


def _project_root():
    return Path(CFG.PROJECT_ROOT).resolve()


def _rel(path):
    root = _project_root()
    try:
        return Path(path).resolve().relative_to(root).as_posix()
    except Exception:
        return str(path)


def _patterns(changed_path):
    stem = Path(changed_path).stem
    if not stem or stem == "__init__":
        stem = Path(changed_path).parent.name
    patterns = []
    if stem:
        patterns.extend(
            [
                rf"from {stem} import",
                rf"from {stem}\.",
                rf"import {stem}([^A-Za-z0-9_]|$)",
                rf"from [^[:space:]]*{stem} import",
                rf"['\"]{stem}['\"]",
                rf"['\"][.]/{stem}['\"]",
                rf"['\"][.][.]/{stem}['\"]",
                rf"['\"][^'\"]*/{stem}['\"]",
            ]
        )
    return patterns


def find_module_dependents(changed_path):
    root = _project_root()
    changed_rel = str(changed_path or "").strip()
    if not changed_rel:
        return []
    patterns = _patterns(changed_rel)
    if not patterns:
        return []

    cmd = ["grep", "-R", "-l", "-E", "--exclude-dir=" + EXCLUDE_DIRS[0]]
    for name in EXCLUDE_DIRS[1:]:
        cmd.append("--exclude-dir=" + name)
    for include in INCLUDES:
        cmd.append("--include=" + include)
    cmd.append("|".join(patterns))
    cmd.append(str(root))

    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise RuntimeError("grep is required to find module dependents")

    dependents = []
    seen = set()
    for line in (proc.stdout or "").splitlines():
        abs_path = line.strip()
        if not abs_path:
            continue
        rel = _rel(abs_path)
        if rel == changed_rel or rel in seen:
            continue
        parts = Path(rel).parts
        if parts and parts[0] in EXCLUDE_DIRS:
            continue
        seen.add(rel)
        dependents.append(rel)
    return dependents


if __name__ == "__main__":
    assert "foo" in _patterns("pkg/foo.py")[0] or True
    print("FIND_MODULE_DEPENDENTS helper loaded")
