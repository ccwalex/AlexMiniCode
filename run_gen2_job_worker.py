import argparse
import contextlib
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def setup_paths():
    source_dir = Path(__file__).resolve().parent
    project_root = source_dir.parent
    modules_dir = source_dir / "modules"

    os.chdir(project_root)

    for path in [str(source_dir), str(modules_dir)]:
        if path not in sys.path:
            sys.path.insert(0, path)

    return project_root, source_dir, modules_dir


def read_json(path, default=None):
    path = Path(path)

    if not path.exists():
        return default

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_suffix(path.suffix + ".tmp")

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)

    tmp.replace(path)


def append_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


def update_status(job_dir, **updates):
    status_path = Path(job_dir) / "status.json"
    status = read_json(status_path, default={}) or {}

    status.update(updates)
    status["updated_at"] = utc_now()

    write_json(status_path, status)


def make_json_safe(obj):
    if obj is None:
        return None

    if isinstance(obj, (str, int, float, bool)):
        return obj

    if isinstance(obj, list):
        return [make_json_safe(x) for x in obj]

    if isinstance(obj, tuple):
        return [make_json_safe(x) for x in obj]

    if isinstance(obj, dict):
        return {
            str(k): make_json_safe(v)
            for k, v in obj.items()
        }

    if hasattr(obj, "to_dict"):
        try:
            return make_json_safe(obj.to_dict())
        except Exception:
            pass

    return str(obj)


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
            stream.flush()

    def flush(self):
        for stream in self.streams:
            stream.flush()


def run_job(job_dir):
    project_root, source_dir, modules_dir = setup_paths()

    job_dir = Path(job_dir).resolve()
    config_path = job_dir / "config.json"
    stdout_path = job_dir / "stdout.log"
    stderr_path = job_dir / "stderr.log"
    result_path = job_dir / "result.json"

    config = read_json(config_path)

    if not isinstance(config, dict):
        raise RuntimeError(f"Missing or invalid job config: {config_path}")

    task = config.get("task", "")

    if not task:
        raise RuntimeError("Job config missing task")

    from modules.run_task_v2 import run_task_v2

    update_status(
        job_dir,
        status="running",
        success=None,
        pid=os.getpid(),
        started_at=utc_now(),
        project_root=str(project_root),
        source_dir=str(source_dir),
    )

    with open(stdout_path, "a", encoding="utf-8") as stdout_file, open(stderr_path, "a", encoding="utf-8") as stderr_file:
        tee_stdout = Tee(sys.__stdout__, stdout_file)
        tee_stderr = Tee(sys.__stderr__, stderr_file)

        with contextlib.redirect_stdout(tee_stdout), contextlib.redirect_stderr(tee_stderr):
            print(f"[JOB START] {job_dir}")
            print(f"[TASK] {task}")

            result = run_task_v2(
                task=task,
                max_tokens=config.get("max_tokens"),
                model=config.get("model"),
                effort=config.get("effort"),
                llm_source=config.get("llm_source"),
                cursor_params=config.get("cursor_params"),
                shell_instruction_prompt=config.get("shell_instruction_prompt", ""),
                max_iterations=config.get("max_iterations"),
                max_feedback_loops=config.get("max_feedback_loops"),
                max_retries=config.get("max_retries"),
                role_overrides=config.get("role_overrides"),
            )

            safe_result = make_json_safe(result)

            write_json(result_path, safe_result)

            success = bool(result.get("success")) if isinstance(result, dict) else False
            status = "completed" if success else "failed"
            reason = result.get("reason", "") if isinstance(result, dict) else "run_task_v2 returned non-dict result"

            update_status(
                job_dir,
                status=status,
                success=success,
                ended_at=utc_now(),
                reason=reason,
            )

            print(f"[JOB END] status={status} success={success} reason={reason}")

            return 0 if success else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", required=True)
    args = parser.parse_args()

    job_dir = Path(args.job_dir).resolve()

    try:
        return run_job(job_dir)

    except Exception as e:
        tb = traceback.format_exc()

        append_text(job_dir / "stderr.log", "\n[WORKER EXCEPTION]\n")
        append_text(job_dir / "stderr.log", tb)

        write_json(
            job_dir / "result.json",
            {
                "success": False,
                "status": "failed",
                "reason": str(e),
                "traceback": tb,
            },
        )

        update_status(
            job_dir,
            status="failed",
            success=False,
            ended_at=utc_now(),
            reason=str(e),
        )

        print(tb, file=sys.stderr)

        return 1


if __name__ == "__main__":
    raise SystemExit(main())