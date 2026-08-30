MODULE_METADATA = {
    "name": "execute_api_plan",
    "type": "function",
    "description": "Execute a normalized list of Gen2 API calls in order using execute_api_call, stopping on failure, request_feedback, or done.",
    "functions": [
        {
            "name": "execute_api_plan",
            "inputs": {
                "calls": "list of normalized API call dictionaries",
                "run_state": "RunState object or None",
                "read_cache": "dict or None mapping paths to cached content",
                "shell_instruction_prompt": "str shell permission/safety instruction prompt",
                "scratchpad": "Scratchpad object or None for in-task RAM notes",
                "mark_task_done": "bool whether /done should mark the shared RunState complete"
            },
            "outputs": "dict with success bool, status, run_state, read_cache, results, failure info, feedback, done bool, conflict bool, and error string or None"
        }
    ]
}


from execute_api_call import execute_api_call
from run_state import RunState


VALID_STATUSES = {
    "completed",
    "request_feedback",
    "done",
    "failed",
}


def execute_api_plan(
    calls,
    run_state=None,
    read_cache=None,
    shell_instruction_prompt="",
    scratchpad=None,
    mark_task_done=True,
):
    """
    Execute a normalized list of Gen2 API calls.

    This function does not parse raw planner output.
    This function does not call LLM.
    This function does not run debug.
    This function simply executes already-normalized API calls in order.
    """

    if not isinstance(calls, list):
        return {
            "success": False,
            "status": "failed",
            "run_state": run_state,
            "read_cache": read_cache if read_cache is not None else {},
            "results": [],
            "failed_call": None,
            "failed_result": None,
            "feedback": None,
            "done": False,
            "conflict": False,
            "error": "calls must be a list",
        }

    if run_state is None:
        run_state = RunState()

    if read_cache is None:
        read_cache = {}

    results = []

    for index, call in enumerate(calls):
        try:
            result = execute_api_call(
                call=call,
                run_state=run_state,
                read_cache=read_cache,
                shell_instruction_prompt=shell_instruction_prompt,
                scratchpad=scratchpad,
                mark_task_done=mark_task_done,
            )
        except Exception as e:
            result = {
                "success": False,
                "url": call.get("url") if isinstance(call, dict) else None,
                "payload": call.get("payload", {}) if isinstance(call, dict) else {},
                "output": None,
                "error": f"execute_api_call raised exception: {str(e)}",
                "done": False,
                "conflict": False,
                "request_feedback": False,
            }

        results.append(result)

        if hasattr(run_state, "add_call"):
            try:
                run_state.add_call(
                    call,
                    status="success" if result.get("success") else "failed",
                    output=result,
                )
            except Exception:
                pass

        if not result.get("success"):
            if hasattr(run_state, "add_error"):
                try:
                    run_state.add_error(
                        stage="execute_api_plan",
                        error=result.get("error", "API call failed"),
                        context={
                            "call": call,
                            "result": result,
                        },
                    )
                except Exception:
                    pass

            return {
                "success": False,
                "status": "failed",
                "run_state": run_state,
                "read_cache": read_cache,
                "results": results,
                "failed_call": call,
                "failed_result": result,
                "feedback": None,
                "done": False,
                "conflict": False,
                "error": result.get("error", "API call failed"),
            }

        if result.get("request_feedback"):
            current_url = result.get("url")
            next_call = calls[index + 1] if index + 1 < len(calls) else None
            next_url = next_call.get("url") if isinstance(next_call, dict) else None

            # Batch consecutive reads before returning their merged file_context.
            # The explicit /request_feedback endpoint may follow the final read.
            if current_url == "/read" and next_url in {"/read", "/request_feedback"}:
                continue

            feedback = None
            output = result.get("output")

            if isinstance(output, dict):
                feedback = output

            return {
                "success": True,
                "status": "request_feedback",
                "run_state": run_state,
                "read_cache": read_cache,
                "results": results,
                "failed_call": None,
                "failed_result": None,
                "feedback": feedback,
                "done": False,
                "conflict": False,
                "error": None,
            }

        if result.get("conflict"):
            conflict_output = result.get("output")
            conflict_text = ""
            if isinstance(conflict_output, dict):
                conflict_text = str(conflict_output.get("conflict") or "")
            elif conflict_output is not None:
                conflict_text = str(conflict_output)

            if hasattr(run_state, "mark_completed"):
                try:
                    run_state.mark_completed(success=False)
                except Exception:
                    pass

            return {
                "success": False,
                "status": "failed",
                "run_state": run_state,
                "read_cache": read_cache,
                "results": results,
                "failed_call": None,
                "failed_result": None,
                "feedback": None,
                "done": False,
                "conflict": True,
                "conflict_output": conflict_output,
                "error": conflict_text or "Task terminated via /conflict",
            }

        if result.get("done"):
            return {
                "success": True,
                "status": "done",
                "run_state": run_state,
                "read_cache": read_cache,
                "results": results,
                "failed_call": None,
                "failed_result": None,
                "feedback": None,
                "done": True,
                "conflict": False,
                "error": None,
            }

    return {
        "success": True,
        "status": "completed",
        "run_state": run_state,
        "read_cache": read_cache,
        "results": results,
        "failed_call": None,
        "failed_result": None,
        "feedback": None,
        "done": False,
        "conflict": False,
        "error": None,
    }


if __name__ == "__main__":
    original_execute_api_call = execute_api_call

    executed_urls = []

    def fake_execute_api_call(
        call,
        run_state=None,
        read_cache=None,
        shell_instruction_prompt="",
        scratchpad=None,
        mark_task_done=True,
    ):
        url = call.get("url")
        payload = call.get("payload", {})
        executed_urls.append(url)

        if url == "/read":
            if read_cache is not None:
                read_cache[payload.get("path", "unknown")] = "abc"

            return {
                "success": True,
                "url": url,
                "payload": payload,
                "output": {
                    "content": "abc"
                },
                "error": None,
                "done": False,
                "request_feedback": True,
            }

        if url == "/done":
            return {
                "success": True,
                "url": url,
                "payload": payload,
                "output": {
                    "summary": payload.get("summary", "")
                },
                "error": None,
                "done": True,
                "request_feedback": False,
            }

        if url == "/bad":
            return {
                "success": False,
                "url": url,
                "payload": payload,
                "output": None,
                "error": "bad call",
                "done": False,
                "request_feedback": False,
            }

        return {
            "success": True,
            "url": url,
            "payload": payload,
            "output": {
                "ok": True
            },
            "error": None,
            "done": False,
            "request_feedback": False,
        }

    globals()["execute_api_call"] = fake_execute_api_call

    # 1. Completed plan.
    executed_urls.clear()
    result = execute_api_plan([
        {
            "url": "/write",
            "payload": {
                "path": "a.py",
                "content": "x"
            }
        }
    ])

    assert result["success"] is True, result
    assert result["status"] == "completed", result
    assert len(result["results"]) == 1, result
    assert executed_urls == ["/write"], executed_urls

    # 2. Request feedback stops after first call.
    executed_urls.clear()
    result = execute_api_plan([
        {
            "url": "/read",
            "payload": {
                "path": "a.py"
            }
        },
        {
            "url": "/write",
            "payload": {
                "path": "b.py",
                "content": "x"
            }
        }
    ])

    assert result["success"] is True, result
    assert result["status"] == "request_feedback", result
    assert len(result["results"]) == 1, result
    assert executed_urls == ["/read"], executed_urls

    # 3. Done.
    executed_urls.clear()
    result = execute_api_plan([
        {
            "url": "/done",
            "payload": {
                "summary": "ok"
            }
        }
    ])

    assert result["success"] is True, result
    assert result["status"] == "done", result
    assert result["done"] is True, result
    assert executed_urls == ["/done"], executed_urls

    # 4. Failure.
    executed_urls.clear()
    result = execute_api_plan([
        {
            "url": "/bad",
            "payload": {}
        }
    ])

    assert result["success"] is False, result
    assert result["status"] == "failed", result
    assert result["failed_call"] is not None, result
    assert result["failed_result"] is not None, result
    assert executed_urls == ["/bad"], executed_urls

    # 5. Invalid calls input.
    result = execute_api_plan("not a list")

    assert result["success"] is False, result
    assert result["status"] == "failed", result

    globals()["execute_api_call"] = original_execute_api_call

    print("EXECUTE_API_PLAN SELF TEST PASSED")