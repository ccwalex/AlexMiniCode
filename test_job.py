import contextlib
import json
import os
import sys
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


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


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


def as_int(value, default):
    if value is None:
        return default
    try:
        return int(value)
    except Exception:
        return default


def build_effective_config(config):
    """
    Normalize config exactly before calling run_task_v2.

    This prevents silent fallback to CFG.DEFAULT_MODEL when model is absent,
    empty, typoed, or malformed.
    """

    if not isinstance(config, dict):
        config = {}

    return {
        "task": config.get("task") or (
            "Create code/hello_job_worker.py that prints "
            "'hello from job worker', then run it."
        ),
        "model": config.get("model") or "",
        "effort": config.get("effort") or "l",
        "max_tokens": as_int(config.get("max_tokens"), 4096),
        "shell_instruction_prompt": config.get("shell_instruction_prompt") or (
            "Allow creating files under code/. "
            "Allow running python scripts under code/. "
            "Do not allow deleting files or modifying files through shell redirection."
        ),
        "max_iterations": config.get("max_iterations", 5),
        "max_feedback_loops": config.get("max_feedback_loops", 3),
        "max_retries": config.get("max_retries", 2),
    }


def create_dummy_job(project_root):
    job_dir = project_root / "agent_memory" / "jobs" / "dummy_job_worker_test"
    job_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "task": "Create code/hello_job_worker.py that prints 'hello from job worker', then run it.",
        "model": "nova",
        "effort": "m",
        "max_tokens": 4096,
        "shell_instruction_prompt": (
            "Allow creating files under code/. "
            "Allow running python scripts under code/. "
            "Do not allow deleting files or modifying files through shell redirection."
        ),
        "max_iterations": 5,
        "max_feedback_loops": 3,
        "max_retries": 2,
    }

    write_json(job_dir / "config.json", config)
    write_json(
        job_dir / "status.json",
        {
            "job_id": "dummy_job_worker_test",
            "status": "queued",
            "success": None,
            "created_at": utc_now(),
            "updated_at": utc_now(),
        },
    )

    # Reset logs/result for clean test.
    write_text(job_dir / "stdout.log", "")
    write_text(job_dir / "stderr.log", "")
    result_path = job_dir / "result.json"
    if result_path.exists():
        result_path.unlink()

    return job_dir


def run_job(job_dir):
    project_root, source_dir, modules_dir = setup_paths()

    job_dir = Path(job_dir).resolve()
    config_path = job_dir / "config.json"
    stdout_path = job_dir / "stdout.log"
    stderr_path = job_dir / "stderr.log"
    result_path = job_dir / "result.json"

    raw_config = read_json(config_path)

    print("[RAW CONFIG]")
    print(json.dumps(raw_config, indent=2, ensure_ascii=False))

    config = build_effective_config(raw_config)

    print("[EFFECTIVE CONFIG]")
    print(json.dumps(config, indent=2, ensure_ascii=False))

    write_json(job_dir / "effective_config.json", config)

    from modules.run_task_v2 import run_task_v2

    update_status(
        job_dir,
        status="running",
        success=None,
        pid=os.getpid(),
        started_at=utc_now(),
        project_root=str(project_root),
        source_dir=str(source_dir),
        modules_dir=str(modules_dir),
        model=config["model"],
        effort=config["effort"],
        max_tokens=config["max_tokens"],
    )

    with open(stdout_path, "a", encoding="utf-8") as stdout_file, open(stderr_path, "a", encoding="utf-8") as stderr_file:
        tee_stdout = Tee(sys.__stdout__, stdout_file)
        tee_stderr = Tee(sys.__stderr__, stderr_file)

        with contextlib.redirect_stdout(tee_stdout), contextlib.redirect_stderr(tee_stderr):
            print(f"[JOB START] {job_dir}")
            print("[PROJECT ROOT]", project_root)
            print("[SOURCE DIR]", source_dir)
            print("[MODULES DIR]", modules_dir)
            print("[EFFECTIVE CONFIG]")
            print(json.dumps(config, indent=2, ensure_ascii=False))

            result = run_task_v2(
                task=config["task"],
                max_tokens=config["max_tokens"],
                model=config["model"],
                effort=config["effort"],
                shell_instruction_prompt=config["shell_instruction_prompt"],
                max_iterations=config["max_iterations"],
                max_feedback_loops=config["max_feedback_loops"],
                max_retries=config["max_retries"],
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
    project_root, source_dir, modules_dir = setup_paths()
    job_dir = create_dummy_job(project_root)

    try:
        exit_code = run_job(job_dir)

        print("\n[DUMMY JOB DIR]")
        print(job_dir)

        print("\n[STATUS]")
        print((job_dir / "status.json").read_text(encoding="utf-8"))

        print("\n[EFFECTIVE CONFIG]")
        print((job_dir / "effective_config.json").read_text(encoding="utf-8"))

        print("\n[RESULT]")
        print((job_dir / "result.json").read_text(encoding="utf-8"))

        return exit_code

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