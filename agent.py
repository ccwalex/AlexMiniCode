import os
import sys
from pathlib import Path


SOURCE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SOURCE_DIR.parent
MODULES_DIR = SOURCE_DIR / "modules"

os.chdir(PROJECT_ROOT)

for path in [str(SOURCE_DIR), str(MODULES_DIR)]:
    if path not in sys.path:
        sys.path.insert(0, path)


from modules.run_task_v2 import run_task_v2


try:
    from modules.run_task import run_task as run_task_v1
except Exception:
    run_task_v1 = None


def run_task(
    task,
    max_tokens=None,
    model=None,
    effort=None,
    llm_source=None,
    cursor_params=None,
    shell_instruction_prompt="",
    max_iterations=None,
    max_feedback_loops=None,
    max_retries=None,
    use_v2=True,
):
    """
    Stable public facade.

    Gen2 is default.
    Gen1 remains optionally available if modules.run_task exists.
    """

    if use_v2:
        return run_task_v2(
            task=task,
            max_tokens=max_tokens,
            model=model,
            effort=effort,
            llm_source=llm_source,
            cursor_params=cursor_params,
            shell_instruction_prompt=shell_instruction_prompt,
            max_iterations=max_iterations,
            max_feedback_loops=max_feedback_loops,
            max_retries=max_retries,
        )

    if run_task_v1 is None:
        raise RuntimeError("Gen1 run_task is not available.")

    return run_task_v1(
        task=task,
        max_tokens=max_tokens,
        model=model,
        effort=effort,
    )


def launch_web(host="127.0.0.1", port=7860):
    """
    Launch future web GUI.

    Supports either:
    - source_dir/web/gen2_web_gui.py
    - source_dir/gen2_web_gui.py
    """

    try:
        from web.gen2_web_gui import run_server
    except Exception:
        from gen2_web_gui import run_server

    return run_server(
        host=host,
        port=port,
        project_root=str(PROJECT_ROOT),
    )


def paths():
    return {
        "project_root": str(PROJECT_ROOT),
        "source_dir": str(SOURCE_DIR),
        "modules_dir": str(MODULES_DIR),
        "cwd": str(Path.cwd()),
    }