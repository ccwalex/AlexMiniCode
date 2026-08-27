"""
Client for invoking the Gen2 web GUI agent as a subagent.

Supports two modes:
- HTTP: call a running gen2_web_gui_tracked server (default)
- direct: import and call subagent_run/subagent_status in-process
"""

from __future__ import annotations

import json
from typing import Any

MODULE_METADATA = {
    "name": "call_gen2_subagent",
    "type": "function",
    "description": "Call the Gen2 web GUI agent as a subagent from another agent via HTTP or direct import.",
    "functions": [
        {
            "name": "call_gen2_subagent",
            "inputs": {
                "prompt": "str task for the Gen2 agent",
                "selected_files": "list[str] optional project-relative file paths",
                "selected_registry_groups": "list[str] optional tracked folder paths",
                "model": "str optional model selector",
                "effort": "str optional effort l/m/h",
                "max_tokens": "int optional planner token budget",
                "shell_instruction_prompt": "str optional shell policy prompt",
                "wait": "bool wait for job completion, default True",
                "timeout_seconds": "int max wait time when wait=True, default 3600",
                "poll_interval_seconds": "float poll interval when wait=True, default 2",
                "include_logs": "bool include stdout/stderr in response, default False",
                "init_if_needed": "bool initialize agent memory before run, default True",
                "base_url": "str Gen2 server base URL for HTTP mode",
                "mode": "str 'http' or 'direct', default 'http'",
            },
            "outputs": "dict subagent run result with success, job_id, status, reason, result, job",
        },
        {
            "name": "gen2_subagent_status",
            "inputs": {
                "base_url": "str Gen2 server base URL for HTTP mode",
                "mode": "str 'http' or 'direct', default 'http'",
            },
            "outputs": "dict service status with queue and current job info",
        },
    ],
}


def _http_post(base_url: str, path: str, payload: dict | None = None) -> dict:
    import requests

    url = base_url.rstrip("/") + path
    response = requests.post(
        url,
        json=payload or {},
        timeout=max(30, int((payload or {}).get("timeout_seconds", 3600)) + 60),
    )
    response.raise_for_status()
    return response.json()


def _http_get(base_url: str, path: str) -> dict:
    import requests

    url = base_url.rstrip("/") + path
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


def _direct_import():
    import importlib.util
    from pathlib import Path

    gui_path = Path(__file__).resolve().parent.parent / "gen2_web_gui_tracked.py"
    spec = importlib.util.spec_from_file_location("gen2_web_gui_tracked", gui_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load gen2 module from {gui_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def gen2_subagent_status(base_url: str = "http://127.0.0.1:7860", mode: str = "http") -> dict:
    if mode == "direct":
        mod = _direct_import()
        return mod.subagent_status()
    return _http_get(base_url, "/api/subagent/status")


def call_gen2_subagent(
    prompt: str,
    selected_files: list[str] | None = None,
    selected_registry_groups: list[str] | None = None,
    model: str | None = None,
    effort: str | None = None,
    max_tokens: int | None = None,
    shell_instruction_prompt: str | None = None,
    wait: bool = True,
    timeout_seconds: int = 3600,
    poll_interval_seconds: float = 2,
    include_logs: bool = False,
    init_if_needed: bool = True,
    base_url: str = "http://127.0.0.1:7860",
    mode: str = "http",
    **extra: Any,
) -> dict:
    payload = {
        "prompt": prompt,
        "selected_files": selected_files or [],
        "selected_registry_groups": selected_registry_groups or [],
        "wait": wait,
        "timeout_seconds": timeout_seconds,
        "poll_interval_seconds": poll_interval_seconds,
        "include_logs": include_logs,
        "init_if_needed": init_if_needed,
    }
    if model is not None:
        payload["model"] = model
    if effort is not None:
        payload["effort"] = effort
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if shell_instruction_prompt is not None:
        payload["shell_instruction_prompt"] = shell_instruction_prompt
    payload.update(extra)

    if mode == "direct":
        mod = _direct_import()
        return mod.subagent_run(payload)

    return _http_post(base_url, "/api/subagent/run", payload)


if __name__ == "__main__":
    status = gen2_subagent_status(mode="direct")
    print(json.dumps(status, indent=2, ensure_ascii=False))
    print("CALL_GEN2_SUBAGENT STATUS SELF TEST PASSED")
