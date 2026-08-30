"""Isolated worker for one blocking planner subagent."""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if hasattr(value, "to_dict"):
        try:
            return _json_safe(value.to_dict())
        except Exception:
            pass
    return str(value)


def _write_result(path, result):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(_json_safe(result), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()

    source_dir = Path(__file__).resolve().parent
    modules_dir = source_dir / "modules"
    for path in (str(source_dir), str(modules_dir)):
        if path not in sys.path:
            sys.path.insert(0, path)

    os.environ["AGENT_SUBAGENT_DEPTH"] = "1"
    try:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        from modules.run_task_v2 import run_task_v2

        result = run_task_v2(
            task=config["task"],
            max_tokens=config.get("max_tokens"),
            model=config.get("model"),
            effort=config.get("effort"),
            llm_source=config.get("llm_source"),
            cursor_params=config.get("cursor_params"),
            shell_instruction_prompt=config.get("shell_instruction_prompt", ""),
            max_iterations=config.get("max_iterations"),
            max_feedback_loops=config.get("max_feedback_loops"),
            max_retries=config.get("max_retries"),
        )
        _write_result(args.result, result)
        return 0 if isinstance(result, dict) and result.get("success") else 1
    except Exception as exc:
        _write_result(
            args.result,
            {
                "success": False,
                "status": "failed",
                "reason": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
