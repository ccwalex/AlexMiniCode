"""Persist and reuse rewritten tasks when restarting jobs."""

from __future__ import annotations

import json
import os
from pathlib import Path

from extract_task_context import extract_file_context_block, extract_module_registry_block


MODULE_METADATA = {
    "name": "job_restart",
    "type": "function",
    "description": "Load, persist, and apply rewritten tasks for job restarts.",
    "functions": [
        {
            "name": "load_rewritten_task",
            "inputs": {
                "job_dir": "str job directory path",
                "config": "dict optional job config",
                "result": "dict optional job result",
            },
            "outputs": "str rewritten task text or empty string",
        },
        {
            "name": "persist_rewritten_task",
            "inputs": {
                "job_dir": "str job directory path",
                "rewritten_task": "str rewritten planner task text",
            },
            "outputs": "bool whether persistence succeeded",
        },
        {
            "name": "build_restart_task",
            "inputs": {
                "original_task": "str original submitted task package",
                "rewritten_task": "str rewritten task text",
                "selected_files": "list[str] optional GUI-selected files",
                "selected_registry_groups": "list optional GUI-selected registry groups",
                "final_task_builder": "callable optional (prompt, files, groups) -> task",
            },
            "outputs": "str task package for the restarted job",
        },
        {
            "name": "build_restart_config",
            "inputs": {
                "config": "dict job config",
                "job_dir": "str job directory path",
                "result": "dict optional job result",
                "final_task_builder": "callable optional (prompt, files, groups) -> task",
            },
            "outputs": "dict updated restart config",
        },
    ],
}

REWRITTEN_TASK_FILENAME = "rewritten_task.txt"


def _read_text(path):
    path = Path(path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def load_rewritten_task(job_dir, config=None, result=None):
    config = config if isinstance(config, dict) else {}
    result = result if isinstance(result, dict) else {}

    from_config = str(config.get("rewritten_task") or "").strip()
    if from_config:
        return from_config

    from_file = _read_text(Path(job_dir) / REWRITTEN_TASK_FILENAME)
    if from_file:
        return from_file

    run_state = result.get("run_state") if isinstance(result.get("run_state"), dict) else {}
    from_result = str(run_state.get("rewritten_task") or "").strip()
    if from_result:
        return from_result

    return ""


def persist_rewritten_task(job_dir, rewritten_task):
    rewritten_task = str(rewritten_task or "").strip()
    if not rewritten_task:
        return False

    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / REWRITTEN_TASK_FILENAME).write_text(rewritten_task + "\n", encoding="utf-8")

    config_path = job_dir / "config.json"
    if not config_path.exists():
        return True

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return True

    if not isinstance(config, dict):
        return True

    config["rewritten_task"] = rewritten_task
    tmp = config_path.with_suffix(config_path.suffix + ".tmp")
    tmp.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(config_path)
    return True


def build_restart_task(
    original_task,
    rewritten_task,
    selected_files=None,
    selected_registry_groups=None,
    final_task_builder=None,
):
    rewritten_task = str(rewritten_task or "").strip()
    if not rewritten_task:
        return str(original_task or "").strip()

    files = list(selected_files or [])
    groups = list(selected_registry_groups or [])
    if final_task_builder and (files or groups):
        return str(final_task_builder(rewritten_task, files, groups)).strip()

    original_task = str(original_task or "").strip()
    module_registry = extract_module_registry_block(original_task)
    file_context = extract_file_context_block(original_task)

    parts = [
        "<user_request>",
        rewritten_task,
        "</user_request>",
    ]
    if module_registry:
        parts.append(module_registry)
    if file_context:
        parts.append(file_context)
    return "\n\n".join(parts)


def build_restart_config(config, job_dir, result=None, final_task_builder=None):
    config = dict(config) if isinstance(config, dict) else {}
    job_dir = Path(job_dir)

    if result is None and (job_dir / "result.json").exists():
        try:
            result = json.loads((job_dir / "result.json").read_text(encoding="utf-8"))
        except Exception:
            result = None

    rewritten_task = load_rewritten_task(job_dir, config, result)
    if not rewritten_task:
        return config

    out = dict(config)
    out["task"] = build_restart_task(
        out.get("task", ""),
        rewritten_task,
        selected_files=out.get("selected_files"),
        selected_registry_groups=out.get("selected_registry_groups"),
        final_task_builder=final_task_builder,
    )
    out["rewritten_task"] = rewritten_task
    out["skip_task_rewrite"] = True
    return out


def persist_rewritten_task_from_env(rewritten_task):
    job_dir = os.environ.get("GEN2_JOB_DIR", "").strip()
    if not job_dir:
        return False
    return persist_rewritten_task(job_dir, rewritten_task)


if __name__ == "__main__":
    original = (
        "<user_request>\nFix parser.\n</user_request>\n\n"
        "<module_registry>\n<group name=\"core\" />\n</module_registry>\n\n"
        "<file_context>\n<file_1 path=\"a.py\">\n<content>\nprint('hi')\n</content>\n</file_1>\n</file_context>"
    )
    rebuilt = build_restart_task(
        original,
        "Rewritten: fix parser with project context folded in.",
    )
    assert "Rewritten: fix parser" in rebuilt
    assert "<module_registry>" in rebuilt
    assert "<file_context>" in rebuilt
    assert "<user_request>" in rebuilt
    print("job_restart self-test passed")
