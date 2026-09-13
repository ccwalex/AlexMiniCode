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
from subagent_runner import run_readonly_subagents_parallel
from job_progress import emit_batch, emit_plan, emit_step, label_for_call, log_step


VALID_STATUSES = {
    "completed",
    "request_feedback",
    "done",
    "failed",
}


def _subagent_mode(call):
    payload = call.get("payload") if isinstance(call, dict) else {}
    if not isinstance(payload, dict):
        return "process"
    return str(payload.get("mode") or "process").strip().lower()


def _consecutive_subagent_calls(calls, start):
    run = []
    index = start
    while index < len(calls):
        call = calls[index]
        if not isinstance(call, dict) or call.get("url") != "/subagent":
            break
        run.append(call)
        index += 1
    return run


def _wrap_subagent_plan_result(call, subagent_result):
    payload = call.get("payload") if isinstance(call, dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "success": True,
        "url": "/subagent",
        "payload": payload,
        "output": {"subagent_result": subagent_result},
        "error": None,
        "done": False,
        "conflict": False,
        "request_feedback": True,
    }


def _record_plan_result(run_state, call, result):
    if hasattr(run_state, "add_call"):
        try:
            run_state.add_call(
                call,
                status="success" if result.get("success") else "failed",
                output=result,
            )
        except Exception:
            pass


def execute_api_plan(
    calls,
    run_state=None,
    read_cache=None,
    shell_instruction_prompt="",
    scratchpad=None,
    mark_task_done=True,
    batch_id=None,
    iteration=None,
    batch_kind="main",
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
    index = 0
    total_steps = len(calls)

    if batch_id:
        emit_plan(batch_id, calls)
        emit_batch(batch_id, "started", iteration=iteration, kind=batch_kind, total_steps=total_steps)

    while index < len(calls):
        call = calls[index]
        step_label = label_for_call(call) if isinstance(call, dict) else "step"
        batch = (
            _consecutive_subagent_calls(calls, index)
            if isinstance(call, dict) and call.get("url") == "/subagent"
            else []
        )
        parallel_readonly_batch = (
            len(batch) >= 2 and all(_subagent_mode(item) == "readonly" for item in batch)
        )
        if not parallel_readonly_batch:
            if batch_id:
                emit_step(batch_id, index, call, "running")
            log_step(f"[Gen2 Step {index + 1}/{total_steps}] {step_label} (started)")
        batch_results = None
        if parallel_readonly_batch:
            specs = [item.get("payload") or {} for item in batch]
            parallel_group = f"readonly-{index}"
            print(f"[Subagent] start parallel readonly n={len(batch)}", flush=True)
            if batch_id:
                for offset, item in enumerate(batch):
                    emit_step(
                        batch_id,
                        index + offset,
                        item,
                        "running",
                        parallel_group=parallel_group,
                    )
                    log_step(
                        f"[Gen2 Step {index + offset + 1}/{total_steps}] "
                        f"{label_for_call(item)} (started, parallel)"
                    )
            child_results = run_readonly_subagents_parallel(specs)
            batch_results = [
                _wrap_subagent_plan_result(item, child)
                for item, child in zip(batch, child_results)
            ]
            for offset, (item, child) in enumerate(zip(batch, child_results)):
                payload = item.get("payload") if isinstance(item, dict) else {}
                if not isinstance(payload, dict):
                    payload = {}
                child = child if isinstance(child, dict) else {}
                if batch_id:
                    child_status = "done" if child.get("success") else "failed"
                    emit_step(
                        batch_id,
                        index + offset,
                        item,
                        child_status,
                        parallel_group=parallel_group,
                        error=str(child.get("error") or ""),
                    )
                    log_step(
                        f"[Gen2 Step {index + offset + 1}/{total_steps}] "
                        f"{label_for_call(item)} ({child_status}, parallel)"
                    )
                print(
                    f"[Subagent] done mode=readonly role={payload.get('role', 'explore')} "
                    f"success={child.get('success')} status={child.get('status')} "
                    f"error={str(child.get('error') or '')[:300]!r}",
                    flush=True,
                )

        if batch_results is not None:
            for item, result in zip(batch, batch_results):
                results.append(result)
                _record_plan_result(run_state, item, result)
            index += len(batch) - 1
            call = batch[-1]
            result = batch_results[-1]
        else:
            try:
                result = execute_api_call(
                    call=call,
                    run_state=run_state,
                    read_cache=read_cache,
                    shell_instruction_prompt=shell_instruction_prompt,
                    scratchpad=scratchpad,
                    mark_task_done=mark_task_done,
                    batch_id=batch_id,
                    step_index=index,
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
            _record_plan_result(run_state, call, result)

        step_status = "done" if result.get("success") else "failed"
        if batch_id and batch_results is None:
            emit_step(
                batch_id,
                index,
                call,
                step_status,
                error=str(result.get("error") or ""),
            )
        log_step(
            f"[Gen2 Step {index + 1}/{total_steps}] {step_label} ({step_status})"
        )

        if not result.get("success"):
            if batch_id:
                emit_batch(batch_id, "failed", iteration=iteration, kind=batch_kind, total_steps=total_steps)
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
            remaining_urls = [
                item.get("url")
                for item in calls[index + 1 :]
                if isinstance(item, dict)
            ]

            # Batch consecutive reads before returning their merged file_context.
            # If a trailing /subagent batch remains, finish every call before it
            # (including /write or /scratchpad) so feedback includes subagent_result.
            # The explicit /request_feedback endpoint may follow the final read or subagent.
            if "/subagent" in remaining_urls:
                index += 1
                continue
            if current_url == "/read" and next_url in {"/read", "/request_feedback"}:
                index += 1
                continue
            if current_url == "/shell" and next_url in {"/read", "/request_feedback"}:
                index += 1
                continue
            if current_url == "/subagent" and next_url == "/request_feedback":
                index += 1
                continue

            feedback = None
            output = result.get("output")

            if isinstance(output, dict):
                feedback = output

            if batch_id:
                emit_batch(
                    batch_id,
                    "request_feedback",
                    iteration=iteration,
                    kind=batch_kind,
                    total_steps=total_steps,
                )
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

            if batch_id:
                emit_batch(batch_id, "failed", iteration=iteration, kind=batch_kind, total_steps=total_steps)
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
            if batch_id:
                emit_batch(batch_id, "done", iteration=iteration, kind=batch_kind, total_steps=total_steps)
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

        index += 1

    if batch_id:
        emit_batch(batch_id, "completed", iteration=iteration, kind=batch_kind, total_steps=total_steps)
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
        batch_id=None,
        step_index=None,
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