"""Sequential, context-isolated subagent execution."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from call_llm import call_llm_role
from model_config import get_role_config
from read_file import read_file
from render_file_context import render_file_context


MODULE_METADATA = {
    "name": "subagent_runner",
    "type": "function",
    "description": "Run one blocking subagent task in an isolated process or a read-only in-process LLM call.",
    "functions": [
        {
            "name": "run_subagent",
            "inputs": {
                "task": "str self-contained delegated task",
                "role": "str explore, review, or implement",
                "mode": "str process or readonly",
                "files": "list[str] optional project-relative context files",
                "timeout_seconds": "int blocking timeout",
            },
            "outputs": "summary-only dict; child planner state and file cache are never returned",
        }
    ],
}


ROLE_CONFIGS = {
    "explore": "subagent_explore",
    "review": "subagent_review",
    "implement": "subagent_implement",
}
MAX_RETURN_CHARS = 4000
MAX_FILES = 20


def _subagent_depth():
    try:
        return max(0, int(os.environ.get("AGENT_SUBAGENT_DEPTH", "0") or 0))
    except Exception:
        return 0


def _text(value, limit=MAX_RETURN_CHARS):
    text = str(value or "").strip()
    if len(text) > limit:
        return text[:limit] + "\n[TRUNCATED]"
    return text


def _extract_llm_text(raw):
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, dict):
        return str(raw or "")
    for key in ("content", "text", "output", "response"):
        value = raw.get(key)
        if isinstance(value, str):
            return value
    choices = raw.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        if isinstance(message.get("content"), str):
            return message["content"]
        if isinstance(first.get("text"), str):
            return first["text"]
    candidates = raw.get("candidates")
    if isinstance(candidates, list) and candidates:
        first = candidates[0] if isinstance(candidates[0], dict) else {}
        content = first.get("content") if isinstance(first.get("content"), dict) else {}
        parts = content.get("parts") if isinstance(content.get("parts"), list) else []
        return "".join(
            part.get("text", "")
            for part in parts
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return json.dumps(raw, ensure_ascii=False)


def _call_with_timeout(call, timeout_seconds):
    """Enforce an in-process deadline on Unix main-thread LLM calls."""
    if (
        threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "SIGALRM")
        or not hasattr(signal, "setitimer")
    ):
        return call()

    def handle_timeout(signum, frame):
        raise TimeoutError(f"subagent timed out after {timeout_seconds}s")

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, handle_timeout)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, float(timeout_seconds))
    try:
        return call()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


def _load_file_context(files):
    cache = {}
    errors = []
    normalized = []
    for item in files or []:
        path = str(item or "").strip()
        if not path or path in normalized:
            continue
        if len(normalized) >= MAX_FILES:
            errors.append(f"file limit reached ({MAX_FILES})")
            break
        normalized.append(path)
        success, content = read_file(path)
        if success:
            cache[path] = content
        else:
            errors.append(f"{path}: {content}")
    return render_file_context(cache, path_order=normalized), errors


def _summary_result(*, success, role, mode, status, summary, artifacts=None, run_id=None, error=None):
    result = {
        "success": bool(success),
        "role": role,
        "mode": mode,
        "status": str(status or ("completed" if success else "failed")),
        "summary": _text(summary),
        "artifacts": list(artifacts or []),
    }
    if run_id:
        result["run_id"] = run_id
    if error:
        result["error"] = _text(error, 1000)
    return result


def _run_readonly(task, role, files, timeout_seconds):
    if role == "implement":
        return _summary_result(
            success=False,
            role=role,
            mode="readonly",
            status="rejected",
            summary="",
            error="implement role requires process mode",
        )

    file_context, read_errors = _load_file_context(files)
    role_name = ROLE_CONFIGS[role]
    cfg = get_role_config(role_name)
    system_prompt = (
        "You are a read-only subagent. Complete only the delegated analysis task. "
        "Do not propose tool calls, execute commands, modify files, or delegate work. "
        "Return a concise, self-contained result for the parent planner."
    )
    user_parts = [f"<delegated_task>\n{task}\n</delegated_task>"]
    if file_context:
        user_parts.append(file_context)
    if read_errors:
        user_parts.append("<file_errors>\n" + "\n".join(read_errors) + "\n</file_errors>")

    try:
        raw = _call_with_timeout(
            lambda: call_llm_role(
                role=role_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "\n\n".join(user_parts)},
                ],
                max_tokens=cfg.get("max_tokens"),
                thinking=cfg.get("effort_normalized"),
                model=cfg.get("model"),
                source=cfg.get("source"),
                cursor_params=cfg.get("cursor_params"),
                timeout=timeout_seconds,
            ),
            timeout_seconds,
        )
        summary = _extract_llm_text(raw)
        return _summary_result(
            success=bool(summary.strip()),
            role=role,
            mode="readonly",
            status="completed" if summary.strip() else "failed",
            summary=summary,
            error=None if summary.strip() else "read-only subagent returned no text",
        )
    except Exception as exc:
        return _summary_result(
            success=False,
            role=role,
            mode="readonly",
            status="failed",
            summary="",
            error=f"read-only subagent failed: {exc}",
        )


def _artifacts_from_result(result):
    state = result.get("run_state") if isinstance(result, dict) else None
    if not isinstance(state, dict):
        return []
    artifacts = []
    for key in ("writes", "edits"):
        for entry in state.get(key) or []:
            if not isinstance(entry, dict) or not entry.get("success"):
                continue
            path = str(entry.get("path") or "").strip()
            if path and path not in artifacts:
                artifacts.append(path)
    return artifacts


def _run_process(task, role, files, timeout_seconds):
    file_context, read_errors = _load_file_context(files)
    task_parts = [
        "<delegated_task>",
        task,
        "</delegated_task>",
        "<delegation_rules>",
        "Complete this task independently. Do not delegate to another subagent.",
        "End with /done whose summary is a concise result for the parent planner.",
        "</delegation_rules>",
    ]
    if file_context:
        task_parts.append(file_context)
    if read_errors:
        task_parts.extend(["<file_errors>", "\n".join(read_errors), "</file_errors>"])

    role_cfg = get_role_config(ROLE_CONFIGS[role])
    run_id = "subagent_" + uuid.uuid4().hex[:12]
    worker = Path(__file__).resolve().parent.parent / "run_subagent_worker.py"
    if not worker.exists():
        return _summary_result(
            success=False,
            role=role,
            mode="process",
            status="failed",
            summary="",
            run_id=run_id,
            error=f"subagent worker not found: {worker}",
        )

    with tempfile.TemporaryDirectory(prefix=f"{run_id}_") as temp_dir:
        temp_path = Path(temp_dir)
        config_path = temp_path / "config.json"
        result_path = temp_path / "result.json"
        stdout_path = temp_path / "stdout.log"
        stderr_path = temp_path / "stderr.log"
        config = {
            "task": "\n".join(task_parts),
            "model": role_cfg.get("model"),
            "effort": role_cfg.get("effort"),
            "llm_source": role_cfg.get("source"),
            "cursor_params": role_cfg.get("cursor_params"),
            "max_tokens": role_cfg.get("max_tokens"),
            "max_iterations": 10,
            "max_feedback_loops": 6,
            "max_retries": 2,
        }
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        env = os.environ.copy()
        env["AGENT_SUBAGENT_DEPTH"] = "1"
        command = [
            sys.executable,
            "-u",
            str(worker),
            "--config",
            str(config_path),
            "--result",
            str(result_path),
        ]

        try:
            with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open(
                "w", encoding="utf-8"
            ) as stderr_file:
                process = subprocess.Popen(
                    command,
                    cwd=os.getcwd(),
                    env=env,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    start_new_session=True,
                )
                previous_handlers = {}

                def forward_parent_signal(signum, frame):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    signal.signal(signum, signal.SIG_DFL)
                    os.kill(os.getpid(), signum)

                if threading.current_thread() is threading.main_thread():
                    for parent_signal in (signal.SIGTERM, signal.SIGINT):
                        previous_handlers[parent_signal] = signal.getsignal(parent_signal)
                        signal.signal(parent_signal, forward_parent_signal)
                try:
                    try:
                        return_code = process.wait(timeout=timeout_seconds)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait()
                        return _summary_result(
                            success=False,
                            role=role,
                            mode="process",
                            status="timed_out",
                            summary="",
                            run_id=run_id,
                            error=f"subagent timed out after {timeout_seconds}s",
                        )
                finally:
                    for parent_signal, previous_handler in previous_handlers.items():
                        signal.signal(parent_signal, previous_handler)
        except Exception as exc:
            return _summary_result(
                success=False,
                role=role,
                mode="process",
                status="failed",
                summary="",
                run_id=run_id,
                error=f"could not run subagent worker: {exc}",
            )

        try:
            child_result = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception as exc:
            stderr = stderr_path.read_text(encoding="utf-8") if stderr_path.exists() else ""
            return _summary_result(
                success=False,
                role=role,
                mode="process",
                status="failed",
                summary="",
                run_id=run_id,
                error=f"subagent worker returned {return_code} without a valid result: {exc}; {_text(stderr, 500)}",
            )

        child_success = bool(child_result.get("success"))
        summary = child_result.get("summary") or (
            child_result.get("reason") if not child_success else ""
        )
        success = bool(child_success and str(summary or "").strip())
        return _summary_result(
            success=success,
            role=role,
            mode="process",
            status=child_result.get("status"),
            summary=summary,
            artifacts=_artifacts_from_result(child_result),
            run_id=run_id,
            error=(
                None
                if success
                else child_result.get("reason")
                or ("successful subagent returned an empty /done summary" if child_success else f"worker exited {return_code}")
            ),
        )


def run_subagent(task, role="explore", mode="process", files=None, timeout_seconds=600):
    task = str(task or "").strip()
    role = str(role or "explore").strip().lower()
    mode = str(mode or "process").strip().lower()
    if not task:
        return _summary_result(
            success=False,
            role=role,
            mode=mode,
            status="rejected",
            summary="",
            error="subagent task is required",
        )
    if role not in ROLE_CONFIGS:
        return _summary_result(
            success=False,
            role=role,
            mode=mode,
            status="rejected",
            summary="",
            error=f"unsupported subagent role: {role}",
        )
    if mode not in {"process", "readonly"}:
        return _summary_result(
            success=False,
            role=role,
            mode=mode,
            status="rejected",
            summary="",
            error=f"unsupported subagent mode: {mode}",
        )
    if _subagent_depth() >= 1:
        return _summary_result(
            success=False,
            role=role,
            mode=mode,
            status="rejected",
            summary="",
            error="nested subagent delegation is disabled",
        )
    try:
        timeout_seconds = max(1, min(int(timeout_seconds), 3600))
    except Exception:
        timeout_seconds = 600
    files = files if isinstance(files, list) else []
    if mode == "readonly":
        return _run_readonly(task, role, files, timeout_seconds)
    return _run_process(task, role, files, timeout_seconds)
