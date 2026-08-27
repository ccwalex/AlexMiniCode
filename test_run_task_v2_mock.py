import os
import sys


CODE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CODE_DIR)
MODULES_DIR = os.path.join(CODE_DIR, "modules")

if CODE_DIR not in sys.path:
    sys.path.insert(0, CODE_DIR)

if MODULES_DIR not in sys.path:
    sys.path.insert(0, MODULES_DIR)


import modules.run_task_v2 as rt2


def main():
    original_build_prompt_v2 = rt2.build_prompt_v2
    original_call_llm = rt2.call_llm
    original_parse_api_plan = rt2.parse_api_plan
    original_execute_api_plan = rt2.execute_api_plan
    original_execute_debug_v2 = rt2.execute_debug_v2
    original_append_run = rt2.append_run

    state = {
        "execute_count": 0,
        "append_count": 0,
    }

    def fake_build_prompt_v2(task, context=""):
        return (
            "system prompt",
            f"user prompt task={task}\ncontext={context}",
        )

    def fake_call_llm(*args, **kwargs):
        return [
            {
                "url": "/done",
                "payload": {
                    "summary": "mock done"
                }
            }
        ]

    def fake_parse_api_plan(raw):
        return {
            "success": True,
            "calls": [
                {
                    "url": "/done",
                    "payload": {
                        "summary": "mock done"
                    }
                }
            ],
            "error": None,
        }

    def fake_execute_api_plan(calls, run_state=None, read_cache=None, shell_instruction_prompt=""):
        state["execute_count"] += 1

        return {
            "success": True,
            "status": "done",
            "run_state": run_state,
            "read_cache": read_cache if read_cache is not None else {},
            "results": [
                {
                    "success": True,
                    "url": "/done",
                    "payload": {
                        "summary": "mock done"
                    },
                    "output": {
                        "summary": "mock done"
                    },
                    "error": None,
                    "done": True,
                    "request_feedback": False,
                }
            ],
            "failed_call": None,
            "failed_result": None,
            "feedback": None,
            "done": True,
            "error": None,
        }

    def fake_execute_debug_v2(*args, **kwargs):
        raise AssertionError("execute_debug_v2 should not be called in this smoke test")

    def fake_append_run(task, result):
        state["append_count"] += 1

    rt2.build_prompt_v2 = fake_build_prompt_v2
    rt2.call_llm = fake_call_llm
    rt2.parse_api_plan = fake_parse_api_plan
    rt2.execute_api_plan = fake_execute_api_plan
    rt2.execute_debug_v2 = fake_execute_debug_v2
    rt2.append_run = fake_append_run

    try:
        result = rt2.run_task_v2(
            "mock Gen2 run_task_v2 smoke test",
            max_tokens=128,
            model="nova",
            effort="l",
            shell_instruction_prompt="No shell commands needed in this mock test.",
            max_iterations=3,
            max_feedback_loops=2,
            max_retries=2,
        )

        print(result)

        assert result["success"] is True, result
        assert result["status"] == "done", result
        assert state["execute_count"] == 1, state
        assert state["append_count"] == 1, state

        print("TEST_RUN_TASK_V2_MOCK PASSED")

    finally:
        rt2.build_prompt_v2 = original_build_prompt_v2
        rt2.call_llm = original_call_llm
        rt2.parse_api_plan = original_parse_api_plan
        rt2.execute_api_plan = original_execute_api_plan
        rt2.execute_debug_v2 = original_execute_debug_v2
        rt2.append_run = original_append_run


if __name__ == "__main__":
    main()