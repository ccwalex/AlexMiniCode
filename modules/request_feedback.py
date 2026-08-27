from build_feedback_context import build_shell_feedback_context
from run_state import RunState

MODULE_METADATA = {
    "name": "request_feedback",
    "type": "function",
    "description": "Thin endpoint helper that returns shell-only feedback for the next planner turn.",
    "functions": [
        {
            "name": "request_feedback",
            "inputs": {
                "run_state": "RunState object containing execution state",
                "read_cache": "dict or None mapping file paths to cached content (unused; files live in file_context)",
                "max_chars": "int maximum approximate feedback length"
            },
            "outputs": "dict with success bool, feedback string, and error string or None"
        }
    ]
}


def request_feedback(run_state, read_cache=None, max_chars=12000):
    _ = read_cache
    try:
        return build_shell_feedback_context(run_state, max_chars)
    except Exception as e:
        return {
            "success": False,
            "feedback": "",
            "error": str(e),
        }


if __name__ == "__main__":
    rs = RunState(task="demo request feedback")
    rs.add_shell("ls -l", True, "total 0")

    result = request_feedback(rs, {"test.py": "def hello():\n    pass"})
    assert result["success"] is True, "Expected success to be True"
    assert "<shell_outputs>" in result["feedback"], "Expected <shell_outputs> in feedback"
    assert "total 0" in result["feedback"], "Expected shell output in feedback"
    assert "<read_file" not in result["feedback"], "Reads should not be in request_feedback output"
    print("REQUEST_FEEDBACK SELF TEST PASSED")
