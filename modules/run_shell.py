import os
import re
import subprocess

from cfg import CFG


MODULE_METADATA = {
    "name": "run_shell",
    "type": "function",
    "description": "Execute an approved shell command from the project root, capture output, and write a run log.",
    "functions": [
        {
            "name": "run_shell",
            "inputs": {
                "cmd": "str shell command approved by verifier",
                "run_id": "str or int identifier used to name the run log file"
            },
            "outputs": "tuple (bool, str) where bool indicates command success and str contains combined stdout/stderr or exception text"
        }
    ]
}


def run_shell(cmd, run_id):
    """
    Execute an approved shell command from CFG.PROJECT_ROOT.

    Behavior:
    - normalizes python3 command prefix to python
    - prepends chrt scheduling for heavy command prefixes
    - captures stdout/stderr
    - writes combined output to runs/{run_id}.log
    - returns (success, output)

    No hard timeout is applied because project commands may include
    long-running DL training, evaluation, preprocessing, or benchmark runs.
    """

    # -------- FIX PYTHON3 -> PYTHON --------
    # Replace python3 only when used as the command:
    # start of command, after newline, after semicolon, or after &&
    cmd = re.sub(
        r'(^|(?<=\n)|(?<=;)|(?<=&&))\s*python3\b',
        r'\1 python',
        cmd,
    )

    # -------- AUTO SCHEDULING --------
    if any(cmd.strip().startswith(p) for p in CFG.HEAVY_COMMAND_PREFIXES):
        cmd = f"chrt -r 1 {cmd}"

    print(f"\n[EXEC CMD] {cmd}")

    log_path = os.path.join(CFG.RUNS_DIR, f"{run_id}.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=CFG.PROJECT_ROOT,
            capture_output=True,
            text=True,
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        output = stdout + ("\n" + stderr if stderr else "")

        # -------- PRINT OUTPUT --------
        if stdout:
            print("\n--- STDOUT ---")
            print(stdout[-1000:])

        if stderr:
            print("\n--- STDERR ---")
            print(stderr[-1000:])

        if not stdout and not stderr:
            print("\n--- NO OUTPUT ---")

        # -------- SAVE LOG --------
        with open(log_path, "w") as f:
            f.write(output)

        success = result.returncode == 0
        return success, output

    except Exception as e:
        output = str(e)
        print("\n❌ EXCEPTION:", output)

        with open(log_path, "w") as f:
            f.write(output)

        return False, output