import os
import json
import time

from safe_path import safe_path


MODULE_METADATA = {
    "name": "append_run",
    "type": "function",
    "description": "Append a compact task/result record to agent_memory/execution/recent_runs.json.",
    "functions": [
        {
            "name": "append_run",
            "inputs": {
                "task": "str task description",
                "result": "str result or output summary to store, truncated to 1000 characters"
            },
            "outputs": "None; appends run record to recent_runs.json on disk"
        }
    ]
}


def append_run(task, result):
    path = safe_path("agent_memory/execution/recent_runs.json")

    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except Exception:
            data = []
    else:
        data = []

    data.append(
        {
            "task": task,
            "result": result[:1000],
            "time": time.time(),
        }
    )

    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "w") as f:
        json.dump(data, f, indent=2)