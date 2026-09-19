"""Context-isolated subagent execution via process workers."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path

from cfg import CFG
from model_config import get_role_config
from read_file import read_file
from render_file_context import render_file_context
from subagent_capabilities import normalize_subagent_role


MODULE_METADATA = {
    "name": "subagent_runner",
    "type": "function",
    "description": "Run blocking subagent tasks in isolated process workers.",
    "functions": [
        {
            "name": "run_subagent",
            "inputs": {
                "task": "str self-contained delegated task",
                "role": "str review or implement",
                "files": "list[str] optional project-relative context files",
                "timeout_seconds": "int blocking timeout",
            },
            "outputs": "summary-only dict; child planner state and file cache are never returned",
        },
        {
            "name": "run_review_subagents_parallel",
            "inputs": {
                "specs": "list of dicts with task, files, timeout_seconds"
            },
            "outputs": "list of summary-only dicts in spec order",
        },
    ],
}


ROLE_CONFIGS = {
    "review": "subagent_review",
    "implement": "subagent_implement",
}
MAX_RETURN_CHARS = 4000
MAX_FILES = 20
SUBAGENT_LOG_SUMMARY_CHARS = 2000
SUBAGENT_DEFAULT_TIMEOUT_SECONDS = 1200


def log_subagent_result(result, *, role=None, task=None):
    """Print subagent status and summary explicitly for job logs."""
    result = result if isinstance(result, dict) else {}
    role = role or result.get("role") or "review"
    task_text = str(task or "").strip()
    print(
        f"[Subagent] done role={role} "
        f"success={result.get('success')} status={result.get('status')} "
        f"error={str(result.get('error') or '')[:300]!r}",
        flush=True,
    )
    if task_text:
        print(f"[Subagent] task={task_text[:200]!r}", flush=True)
    summary = str(result.get("summary") or "").strip()
    if summary:
        if len(summary) > SUBAGENT_LOG_SUMMARY_CHARS:
            summary = summary[:SUBAGENT_LOG_SUMMARY_CHARS] + "\n[TRUNCATED]"
        print("[Subagent] summary:\n" + summary, flush=True)
    else:
        print("[Subagent] summary: (empty)", flush=True)
    artifacts = [str(path) for path in list(result.get("artifacts") or [])[:10] if str(path).strip()]
    if artifacts:
        print(f"[Subagent] artifacts: {', '.join(artifacts)}", flush=True)


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


def _summary_result(*, success, role, status, summary, artifacts=None, run_id=None, error=None):
    result = {
        "success": bool(success),
        "role": role,
        "mode": "process",
        "status": str(status or ("completed" if success else "failed")),
        "summary": _text(summary),
        "artifacts": list(artifacts or []),
    }
    if run_id:
        result["run_id"] = run_id
    if error:
        result["error"] = _text(error, 1000)
    return result


def _max_subagent_repair_loops():
    try:
        return max(1, int(getattr(CFG, "MAX_SUBAGENT_REPAIR_LOOPS", 10) or 10))
    except Exception:
        return 10


def _build_repair_task(original_task, prior_result, attempt, max_loops):
    parts = [str(original_task or "").strip(), ""]
    parts.append("<prior_subagent_failure>")
    parts.append(f"attempt: {attempt}/{max_loops}")
    status = str((prior_result or {}).get("status") or "").strip()
    if status:
        parts.append(f"status: {status}")
    error = str((prior_result or {}).get("error") or "").strip()
    if error:
        parts.append(f"error: {_text(error, 1000)}")
    summary = str((prior_result or {}).get("summary") or "").strip()
    if summary:
        parts.append(f"summary: {_text(summary, 2000)}")
    parts.append("</prior_subagent_failure>")
    parts.append(
        "A prior peer subagent with the same role failed. "
        "Repair the failure above and complete the delegated task."
    )
    return "\n".join(parts)


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
        "Be concise: use minimal output length; do not write for aesthetics.",
        "End with /done whose summary is a brief, minimal result for the parent planner.",
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
            "role": role,
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
        env["AGENT_SUBAGENT_ROLE"] = role
        # Child stdout is captured separately; do not let the worker rewrite
        # the parent job's steps.jsonl or rewritten_task.txt.
        env.pop("GEN2_JOB_DIR", None)
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


def run_subagent(task, role="review", files=None, timeout_seconds=SUBAGENT_DEFAULT_TIMEOUT_SECONDS, **kwargs):
    task = str(task or "").strip()
    try:
        role = normalize_subagent_role(role)
    except ValueError as exc:
        return _summary_result(
            success=False,
            role=str(role or "review"),
            status="rejected",
            summary="",
            error=str(exc),
        )
    if not task:
        return _summary_result(
            success=False,
            role=role,
            status="rejected",
            summary="",
            error="subagent task is required",
        )
    if _subagent_depth() >= 1:
        return _summary_result(
            success=False,
            role=role,
            status="rejected",
            summary="",
            error="nested subagent delegation is disabled",
        )
    try:
        timeout_seconds = max(1, min(int(timeout_seconds), 3600))
    except Exception:
        timeout_seconds = SUBAGENT_DEFAULT_TIMEOUT_SECONDS
    files = files if isinstance(files, list) else []

    original_task = task
    effective_task = task
    max_repair_loops = _max_subagent_repair_loops()
    last_result = None

    for attempt in range(1, max_repair_loops + 1):
        if attempt > 1:
            print(
                f"[Subagent] Repair attempt {attempt}/{max_repair_loops} (role={role})",
                flush=True,
            )

        last_result = _run_process(effective_task, role, files, timeout_seconds)
        if last_result.get("success"):
            return last_result

        status = str(last_result.get("status") or "").strip()
        if status in {"rejected", "timed_out"}:
            return last_result

        if attempt >= max_repair_loops:
            return last_result

        effective_task = _build_repair_task(
            original_task,
            last_result,
            attempt,
            max_repair_loops,
        )

    return last_result


def run_review_subagents_parallel(specs):
    """Run review subagents concurrently as isolated process workers."""
    items = list(specs or [])
    if not items:
        return []

    parent_ctx = copy_context()

    def run_one(spec):
        spec = spec if isinstance(spec, dict) else {}
        return run_subagent(
            task=spec.get("task"),
            role="review",
            files=spec.get("files", []),
            timeout_seconds=spec.get("timeout_seconds", SUBAGENT_DEFAULT_TIMEOUT_SECONDS),
        )

    def run_one_in_context(spec):
        return parent_ctx.copy().run(run_one, spec)

    if len(items) == 1:
        return [parent_ctx.run(run_one, items[0])]

    results = [None] * len(items)
    workers = min(8, len(items))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_one_in_context, spec) for spec in items]
        for index, (spec, future) in enumerate(zip(items, futures)):
            try:
                timeout_seconds = max(1, min(int(spec.get("timeout_seconds") or SUBAGENT_DEFAULT_TIMEOUT_SECONDS), 3600))
            except Exception:
                timeout_seconds = SUBAGENT_DEFAULT_TIMEOUT_SECONDS
            try:
                results[index] = future.result(timeout=timeout_seconds + 5)
            except Exception as exc:
                results[index] = _summary_result(
                    success=False,
                    role="review",
                    status="failed",
                    summary="",
                    error=f"review subagent failed: {exc}",
                )
    return results
