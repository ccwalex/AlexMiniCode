"""Append-only step progress events for Gen2 job UI."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

MODULE_METADATA = {
    "name": "job_progress",
    "type": "function",
    "description": "Emit structured step progress events to GEN2_JOB_DIR/steps.jsonl for live GUI polling.",
    "functions": [
        {
            "name": "emit",
            "inputs": {"event": "dict progress event"},
            "outputs": "None",
        },
        {
            "name": "label_for_call",
            "inputs": {"call": "dict normalized API call"},
            "outputs": "str human-readable step label",
        },
        {
            "name": "detail_for_call",
            "inputs": {"call": "dict normalized API call"},
            "outputs": "str path/cmd/task detail for right column",
        },
    ],
}

_RUNNING = {"planned", "started", "running", "verifying", "writing_meta", "repairing"}
_DONE = {"done", "skipped"}
_FAILED = {"failed", "timeout"}


def _job_dir():
    raw = os.environ.get("GEN2_JOB_DIR", "").strip()
    return raw or None


def enabled() -> bool:
    return bool(_job_dir())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _steps_path():
    job_dir = _job_dir()
    if not job_dir:
        return None
    return os.path.join(job_dir, "steps.jsonl")


def emit(event: dict):
    path = _steps_path()
    if not path:
        return

    payload = dict(event or {})
    payload.setdefault("ts", _now_iso())
    payload.setdefault("v", 1)

    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            handle.flush()
    except Exception:
        pass


def action_for_url(url: str) -> str:
    text = str(url or "").strip().lower()
    if not text:
        return "step"
    return text.lstrip("/").replace("/", "_") or "step"


def detail_for_call(call) -> str:
    if not isinstance(call, dict):
        return ""
    payload = call.get("payload") if isinstance(call.get("payload"), dict) else {}
    url = str(call.get("url") or "")
    if url == "/write":
        return str(payload.get("path") or "")
    if url == "/edit":
        return str(payload.get("path") or "")
    if url == "/read":
        return str(payload.get("path") or "")
    if url == "/shell":
        cmd = str(payload.get("cmd") or "")
        return cmd[:120]
    if url == "/subagent":
        task = str(payload.get("task") or "")
        return task[:120]
    if url == "/scratchpad":
        return str(payload.get("action") or "read")
    if url == "/drop_cache":
        paths = payload.get("paths") or []
        if isinstance(paths, list):
            return ", ".join(str(p) for p in paths[:3])
    if url == "/done":
        return str(payload.get("summary") or "")[:120]
    return ""


def label_for_call(call) -> str:
    if not isinstance(call, dict):
        return "step"
    url = str(call.get("url") or "")
    payload = call.get("payload") if isinstance(call.get("payload"), dict) else {}
    detail = detail_for_call(call)
    if url == "/write":
        return f"write {detail}".strip()
    if url == "/edit":
        return f"edit {detail}".strip()
    if url == "/read":
        return f"read {detail}".strip()
    if url == "/shell":
        return f"shell {detail}".strip()
    if url == "/subagent":
        role = str(payload.get("role") or "explore")
        mode = str(payload.get("mode") or "process")
        return f"subagent {role} ({mode})".strip()
    if url == "/scratchpad":
        return f"scratchpad {detail}".strip()
    if url == "/drop_cache":
        return "drop_cache"
    if url == "/request_feedback":
        return "request_feedback"
    if url == "/done":
        return "done"
    return action_for_url(url)


def emit_batch(batch_id: str, status: str, *, iteration=None, kind="main", total_steps=0):
    emit(
        {
            "event": "batch",
            "batch_id": batch_id,
            "status": status,
            "iteration": iteration,
            "kind": kind,
            "total_steps": total_steps,
        }
    )


def emit_plan(batch_id: str, calls):
    total = len(calls or [])
    for index, call in enumerate(calls or []):
        if not isinstance(call, dict):
            continue
        step_id = f"{batch_id}:{index}"
        emit(
            {
                "event": "step",
                "batch_id": batch_id,
                "step_id": step_id,
                "index": index,
                "status": "planned",
                "action": action_for_url(call.get("url")),
                "url": call.get("url"),
                "label": label_for_call(call),
                "detail": detail_for_call(call),
            }
        )
    emit_batch(batch_id, "planned", total_steps=total)


def emit_step(batch_id: str, index: int, call, status: str, *, phase=None, parallel_group=None, error=""):
    if not isinstance(call, dict):
        call = {}
    step_id = f"{batch_id}:{index}"
    if phase:
        step_id = f"{step_id}:{phase}"
    emit(
        {
            "event": "step",
            "batch_id": batch_id,
            "step_id": step_id,
            "index": index,
            "status": status,
            "action": action_for_url(call.get("url")),
            "url": call.get("url"),
            "label": label_for_call(call) if not phase else f"{phase} {detail_for_call(call)}".strip(),
            "detail": detail_for_call(call),
            "phase": phase,
            "parallel_group": parallel_group,
            "error": str(error or "")[:500],
        }
    )


def emit_substep(batch_id: str, index: int, call, phase: str, status: str, detail: str = ""):
    if not isinstance(call, dict):
        call = {}
    step_id = f"{batch_id}:{index}:{phase}"
    base_detail = detail or detail_for_call(call)
    emit(
        {
            "event": "substep",
            "batch_id": batch_id,
            "step_id": step_id,
            "index": index,
            "status": status,
            "action": phase,
            "url": call.get("url"),
            "label": f"{phase} {base_detail}".strip(),
            "detail": base_detail,
            "phase": phase,
        }
    )


def log_step(message: str):
    print(message, flush=True)


def build_snapshot(events):
    rows = {}
    batches = {}
    order = []

    for raw in events or []:
        if not isinstance(raw, dict):
            continue
        event_type = str(raw.get("event") or "")
        if event_type == "batch":
            batch_id = str(raw.get("batch_id") or "")
            if batch_id:
                batches[batch_id] = raw
            continue

        step_id = str(raw.get("step_id") or "")
        if not step_id:
            continue
        rows[step_id] = raw
        if step_id not in order:
            order.append(step_id)

    snapshot = [rows[step_id] for step_id in order if step_id in rows]
    return {"snapshot": snapshot, "batches": batches}


def parse_steps_jsonl(text: str, max_lines: int = 500):
    events = []
    if not text:
        return events
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except Exception:
            continue
    return events


if __name__ == "__main__":
    test_dir = os.path.join(os.path.dirname(__file__), "..", ".job_progress_test")
    os.makedirs(test_dir, exist_ok=True)
    os.environ["GEN2_JOB_DIR"] = os.path.abspath(test_dir)
    steps_path = _steps_path()
    if steps_path and os.path.exists(steps_path):
        os.remove(steps_path)

    calls = [
        {"url": "/write", "payload": {"path": "code/a.py", "content": "x"}},
        {"url": "/subagent", "payload": {"task": "inspect", "role": "explore", "mode": "readonly"}},
    ]
    batch_id = "iter-1-main"
    emit_plan(batch_id, calls)
    emit_step(batch_id, 0, calls[0], "running")
    emit_substep(batch_id, 0, calls[0], "verifying", "running", "code/a.py")
    emit_step(batch_id, 0, calls[0], "done")
    emit_step(batch_id, 1, calls[1], "running", parallel_group="readonly-1")
    emit_step(batch_id, 1, calls[1], "done", parallel_group="readonly-1")

    with open(steps_path, "r", encoding="utf-8") as handle:
        parsed = parse_steps_jsonl(handle.read())
    snap = build_snapshot(parsed)
    assert len(snap["snapshot"]) >= 3
    assert label_for_call(calls[0]) == "write code/a.py"
    print("JOB_PROGRESS SELF TEST PASSED")
