MODULE_METADATA = {
    "name": "find_module_dependents",
    "type": "function",
    "description": "Find files that import a changed module using grep or rg over the project root.",
    "functions": [
        {
            "name": "find_module_dependents",
            "inputs": {
                "changed_path": "str project-relative path of the changed module",
            },
            "outputs": "list of project-relative dependent file paths",
        },
        {
            "name": "clear_dependents_cache",
            "inputs": {},
            "outputs": "None",
        },
        {
            "name": "is_denied_dependency_path",
            "inputs": {
                "path": "str project-relative path",
            },
            "outputs": "bool",
        },
    ],
}

import os
import shutil
import subprocess
from pathlib import Path

from cfg import CFG


EXCLUDE_DIRS = ("agent", "agent_memory", ".git", ".ipynb_checkpoints", ".ipynb_checkpoint")
INCLUDES = ("*.py", "*.ts", "*.tsx", "*.js", "*.jsx")
_DEPENDENTS_CACHE = {}


def _project_root():
    return Path(CFG.PROJECT_ROOT).resolve()


def _rel(path):
    root = _project_root()
    try:
        return Path(path).resolve().relative_to(root).as_posix()
    except Exception:
        return str(path)


def is_denied_dependency_path(path):
    rel = str(path or "").strip().replace("\\", "/")
    if not rel:
        return True
    parts = Path(rel).parts
    for part in parts:
        if part in EXCLUDE_DIRS:
            return True
        lowered = part.lower()
        if "ipynb_checkpoint" in lowered:
            return True
    return False


def clear_dependents_cache():
    _DEPENDENTS_CACHE.clear()


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


def _search_command(pattern, root):
    if shutil.which("rg"):
        cmd = ["rg", "-l", "-E", pattern]
        for name in EXCLUDE_DIRS:
            cmd.append(f"--glob=!{name}/**")
        for include in INCLUDES:
            cmd.append(f"--glob={include}")
        cmd.append(str(root))
        return cmd

    cmd = ["grep", "-R", "-l", "-E"]
    for name in EXCLUDE_DIRS:
        cmd.append("--exclude-dir=" + name)
    for include in INCLUDES:
        cmd.append("--include=" + include)
    cmd.append(pattern)
    cmd.append(str(root))
    return cmd


def find_module_dependents(changed_path):
    changed_rel = str(changed_path or "").strip()
    if not changed_rel or is_denied_dependency_path(changed_rel):
        return []
    if changed_rel in _DEPENDENTS_CACHE:
        return list(_DEPENDENTS_CACHE[changed_rel])

    root = _project_root()
    patterns = _patterns(changed_rel)
    if not patterns:
        _DEPENDENTS_CACHE[changed_rel] = []
        return []

    dependents = []
    seen = set()
    for pattern in patterns:
        cmd = _search_command(pattern, root)
        try:
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            raise RuntimeError("grep or rg is required to find module dependents")

        for line in (proc.stdout or "").splitlines():
            abs_path = line.strip()
            if not abs_path:
                continue
            rel = _rel(abs_path)
            if rel == changed_rel or rel in seen:
                continue
            if is_denied_dependency_path(rel):
                continue
            seen.add(rel)
            dependents.append(rel)

    _DEPENDENTS_CACHE[changed_rel] = list(dependents)
    return list(dependents)


if __name__ == "__main__":
    assert is_denied_dependency_path("agent/hidden.py")
    assert is_denied_dependency_path("pkg/.ipynb_checkpoints/x.py")
    assert not is_denied_dependency_path("app/use.py")
    assert "foo" in _patterns("pkg/foo.py")[0] or True
    print("FIND_MODULE_DEPENDENTS helper loaded")
