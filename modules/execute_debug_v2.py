from build_debug_prompt_v2 import build_debug_prompt_v2
from build_feedback_context import build_shell_feedback_context
from render_file_context import render_file_context
from call_llm import call_llm_role
from parse_api_plan import parse_api_plan
from structured_llm_retry import call_llm_role_with_parse_retry
from execute_api_plan import execute_api_plan
from run_state import RunState
from cfg import CFG
from preview import preview
from scratchpad import Scratchpad

MODULE_METADATA = {
    "name": "execute_debug_v2",
    "type": "function",
    "description": "Run Gen2 debug loop for runtime execution failures using build_debug_prompt_v2, call_llm, parse_api_plan, and execute_api_plan.",
    "functions": [
        {
            "name": "execute_debug_v2",
            "inputs": {
                "task": "str original user task",
                "failed_call": "dict failed Gen2 API call",
                "failed_result": "dict failed execution result",
                "run_state": "RunState object or None",
                "read_cache": "dict or None mapping paths to cached content",
                "shell_instruction_prompt": "str shell permission/safety instructions",
                "max_tokens": "int or None",
                "model": "str or None",
                "effort": "str or None",
                "max_debug_iterations": "int or None",
                "main_scratchpad": "Scratchpad object or None; debug loop uses a separate debug scratchpad",
                "main_plan_calls": "list or None; full main planner call list that failed",
                "failed_main_plan_index": "int index of failed call in main_plan_calls, or -1",
                "completed_main_plan_calls": "list or None; main-plan calls completed before failure",
                "remaining_main_plan_calls": "list or None; main-plan calls the main loop will resume after debug"
            },
            "outputs": "dict with success bool, status, plan, results, run_state, read_cache, outputs, and reason"
        }
    ]
}

from model_config import get_role_config


def normalize_effort(effort):
    if effort == "l":
        return "low"
    if effort == "m":
        return "medium"
    if effort == "h":
        return "high"
    if effort in ["low", "medium", "high"]:
        return effort
    return "medium"

def _brief_call(call):
    if not isinstance(call, dict):
        return str(call)

    url = str(call.get("url") or "?")
    payload = call.get("payload")
    if not isinstance(payload, dict):
        return url

    parts = []
    for key, value in list(payload.items())[:4]:
        text = str(value).replace("\n", "\\n")
        if len(text) > 80:
            text = text[:77] + "..."
        parts.append(f"{key}={text}")

    suffix = f" ({', '.join(parts)})" if parts else ""
    return f"{url}{suffix}"


def _print_debug_section(title, body, max_chars=2500):
    print(f"\n[Debug Loop] {title}")
    if body is None:
        print("(none)")
        return

    text = preview(body, max_chars) if not isinstance(body, str) else body[:max_chars]
    print(text or "(empty)")


def _print_debug_start(task, failed_call, failed_result, *, model, effort, max_debug_iterations):
    print("\n" + "=" * 72)
    print("[Debug Loop] START — runtime execution failure; entering debug repair")
    print(
        "[Debug Loop] This repairs failed API steps. "
        "Structured Retry (earlier in logs) only retries malformed planner JSON."
    )
    print(f"[Debug Loop] model={model} effort={effort} max_iterations={max_debug_iterations}")
    task_preview = str(task or "").strip().replace("\n", " ")
    if len(task_preview) > 240:
        task_preview = task_preview[:237] + "..."
    print(f"[Debug Loop] task: {task_preview or '(empty)'}")
    _print_debug_section("Failed call", failed_call)
    _print_debug_section("Failed result", failed_result)


def _print_debug_iteration_start(iteration, max_debug_iterations, *, retry=False):
    label = "retry after in-debug failure" if retry else "initial repair attempt"
    print(f"\n[Debug Loop] --- iteration {iteration}/{max_debug_iterations} ({label}) ---")


def _print_debug_plan(plan):
    print("\n[Debug Loop] Planned API calls")
    if not plan:
        print("  (empty plan)")
        return

    for index, call in enumerate(plan):
        print(f"  {index}: {_brief_call(call)}")


def _print_debug_execution(status, exec_res, results):
    print(f"\n[Debug Loop] Execution status: {status}")

    if not results:
        print("[Debug Loop] No step results returned")
        return

    print("[Debug Loop] Step results:")
    for index, result in enumerate(results):
        if not isinstance(result, dict):
            print(f"  {index}: {result}")
            continue

        step_status = "ok" if result.get("success") else "failed"
        url = result.get("url", "?")
        detail = result.get("error") or result.get("reason") or ""
        if not detail and isinstance(result.get("output"), dict):
            detail = str(result.get("output"))[:120]
        detail = str(detail).replace("\n", " ").strip()
        if len(detail) > 120:
            detail = detail[:117] + "..."
        suffix = f" — {detail}" if detail else ""
        print(f"  {index}: [{step_status}] {url}{suffix}")

    if status == "failed":
        _print_debug_section("Next failed call", exec_res.get("failed_call"), max_chars=1500)
        _print_debug_section("Next failed result", exec_res.get("failed_result"), max_chars=1500)
    elif status == "request_feedback":
        feedback = exec_res.get("feedback")
        if feedback:
            _print_debug_section("Feedback payload", feedback, max_chars=1500)


def _print_debug_end(success, status, reason):
    outcome = "SUCCESS" if success else "FAILED"
    print("\n" + "=" * 72)
    print(f"[Debug Loop] END — {outcome} status={status}")
    print(f"[Debug Loop] reason: {reason or '(none)'}")
    print("=" * 72 + "\n")


def execute_debug_v2(
    task,
    failed_call,
    failed_result,
    run_state=None,
    read_cache=None,
    shell_instruction_prompt="",
    max_tokens=None,
    model=None,
    effort=None,
    max_debug_iterations=None,
    main_scratchpad=None,
    main_plan_calls=None,
    failed_main_plan_index=-1,
    completed_main_plan_calls=None,
    remaining_main_plan_calls=None,
    module_registry="",
    code_tables=None,
    attached_paths=None,
):
    if run_state is None:
        run_state = RunState(task=task)
    if read_cache is None:
        read_cache = {}

    debug_cfg = get_role_config("debug")
    max_tokens = max_tokens if max_tokens is not None else debug_cfg["max_tokens"]
    model = model if model is not None else debug_cfg["model"]
    effort = effort if effort is not None else debug_cfg["effort"]
    if max_debug_iterations is None:
        max_debug_iterations = getattr(CFG, "MAX_DEBUG_ITERATIONS", 3)

    final_effort = normalize_effort(effort)

    outputs_str = ""
    debug_scratchpad = Scratchpad(name="debug")
    debug_shell_start_index = len(getattr(run_state, "shells", []))

    original_failed_call = failed_call
    original_failed_result = failed_result
    
    current_failed_call = failed_call
    current_failed_result = failed_result

    _print_debug_start(
        task,
        original_failed_call,
        original_failed_result,
        model=model,
        effort=final_effort,
        max_debug_iterations=max_debug_iterations,
    )

    for i in range(max_debug_iterations):
        _print_debug_iteration_start(
            i + 1,
            max_debug_iterations,
            retry=i > 0 or current_failed_call is not original_failed_call,
        )

        merged_file_context = render_file_context(
            read_cache,
            code_tables=code_tables,
            path_order=attached_paths,
        )

        system_prompt, user_prompt = build_debug_prompt_v2(
            task=task,
            failed_call=current_failed_call,
            failed_result=current_failed_result,
            run_state=run_state,
            context=outputs_str,
            shell_instruction_prompt=shell_instruction_prompt,
            original_failed_call=original_failed_call,
            original_failed_result=original_failed_result,
            debug_iteration=i + 1,
            max_debug_iterations=max_debug_iterations,
            scratchpad_content=debug_scratchpad.read(),
            main_plan_calls=main_plan_calls,
            failed_main_plan_index=failed_main_plan_index,
            completed_main_plan_calls=completed_main_plan_calls,
            remaining_main_plan_calls=remaining_main_plan_calls,
            file_context=merged_file_context,
            module_registry=module_registry,
            main_scratchpad_content=main_scratchpad.read() if main_scratchpad is not None else "",
            shell_feedback_start_index=debug_shell_start_index,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        llm_response = call_llm_role_with_parse_retry(
            role="debug",
            messages=messages,
            is_valid=lambda raw: bool(parse_api_plan(raw).get("success")),
            parse_fallback_kind="execution",
            llm_call=call_llm_role,
            max_tokens=max_tokens,
            thinking=final_effort,
            model=model,
            timeout=None,
        )

        print("\n[Debug Loop] Planner output")
        try:
            print(preview(llm_response, 2500))
        except Exception:
            print(str(llm_response)[:2500])

        parse_res = parse_api_plan(llm_response)
        if not parse_res.get("success"):
            reason = parse_res.get("error", "Failed to parse API plan.")
            print(f"\n[Debug Loop] Parse failed: {reason}")
            _print_debug_end(False, "failed", reason)
            return {
                "success": False,
                "status": "failed",
                "plan": [],
                "results": [],
                "run_state": run_state,
                "read_cache": read_cache,
                "outputs": outputs_str,
                "reason": reason
            }

        plan = parse_res.get("calls", [])
        _print_debug_plan(plan)

        exec_res = execute_api_plan(
            calls=plan,
            run_state=run_state,
            read_cache=read_cache,
            shell_instruction_prompt=shell_instruction_prompt,
            scratchpad=debug_scratchpad,
            mark_task_done=False,
        )

        run_state = exec_res.get("run_state", run_state)
        read_cache = exec_res.get("read_cache", read_cache)
        results = exec_res.get("results", [])
        status = exec_res.get("status", "failed")

        outputs_str += f"\n--- Iteration {i+1} ---\n"
        outputs_str += preview(exec_res, 1000)

        _print_debug_execution(status, exec_res, results)

        if status == "request_feedback":
            print("[Debug Loop] request_feedback received; continuing to next debug iteration")
            continue
        elif status == "done":
            reason = "Debug repaired successfully via /done."
            shell_feedback = build_shell_feedback_context(
                run_state,
                max_chars=12000,
                start_index=debug_shell_start_index,
            ).get("feedback", "")
            _print_debug_end(True, "debug_repaired", reason)
            return {
                "success": True,
                "status": "debug_repaired",
                "plan": plan,
                "results": results,
                "run_state": run_state,
                "read_cache": read_cache,
                "outputs": outputs_str,
                "reason": reason,
                "shell_feedback": shell_feedback,
            }
        elif status == "completed":
            current_failed_call = None
            current_failed_result = {
                "error": (
                    "Debug batch executed without /done. "
                    "Return /done only after the failed step has been revalidated."
                )
            }
            print("[Debug Loop] Batch completed without /done; requesting explicit repair completion")
            continue
        elif status == "failed" and exec_res.get("conflict"):
            conflict_output = exec_res.get("conflict_output")
            conflict_text = exec_res.get("error") or "Debug terminated via /conflict"
            if isinstance(conflict_output, dict):
                conflict_text = str(conflict_output.get("conflict") or conflict_text)

            _print_debug_end(False, "failed", conflict_text)
            return {
                "success": False,
                "status": "failed",
                "plan": plan,
                "results": results,
                "run_state": run_state,
                "read_cache": read_cache,
                "outputs": outputs_str,
                "reason": conflict_text,
                "conflict": True,
                "conflict_output": conflict_output,
            }
        elif status == "failed":
            current_failed_call = exec_res.get("failed_call")
            current_failed_result = exec_res.get("failed_result")
            print("[Debug Loop] Debug plan step failed; retrying with new failure context")
            continue
        else:
            current_failed_call = None
            current_failed_result = {"error": f"Unknown status: {status}"}
            print(f"[Debug Loop] Unknown execution status '{status}'; retrying debug iteration")
            continue

    reason = f"Failed after {max_debug_iterations} iterations."
    _print_debug_end(False, "failed", reason)
    return {
        "success": False,
        "status": "failed",
        "plan": [],
        "results": [],
        "run_state": run_state,
        "read_cache": read_cache,
        "outputs": outputs_str,
        "reason": reason,
    }

if __name__ == "__main__":
    # Monkeypatch for self-testing
    _call_llm_idx = 0
    _parse_api_plan_idx = 0
    _execute_api_plan_idx = 0
    
    def mock_build_debug_prompt_v2(*args, **kwargs):
        return "system", "user"
    
    def mock_call_llm(*args, **kwargs):
        return "{}"

    def mock_call_llm_role(*args, **kwargs):
        return "{}"
    
    def mock_parse_api_plan_1(raw):
        return {
            "success": True,
            "calls": [
                {"url": "/shell", "payload": {"cmd": "python code/test.py"}},
                {"url": "/done", "payload": {"summary": "fixed"}}
            ],
            "error": None
        }
    
    def mock_execute_api_plan_1(*args, **kwargs):
        return {
            "success": True,
            "status": "done",
            "run_state": kwargs.get("run_state"),
            "read_cache": kwargs.get("read_cache"),
            "results": [
                {"success": True, "url": "/shell", "payload": {"cmd": "python code/test.py"}, "done": False, "request_feedback": False},
                {"success": True, "url": "/done", "payload": {"summary": "fixed"}, "done": True, "request_feedback": False}
            ],
            "failed_call": None,
            "failed_result": None,
            "feedback": None,
            "done": True,
            "error": None
        }

    globals()["build_debug_prompt_v2"] = mock_build_debug_prompt_v2
    globals()["call_llm"] = mock_call_llm
    globals()["call_llm_role"] = mock_call_llm_role
    globals()["parse_api_plan"] = mock_parse_api_plan_1
    globals()["execute_api_plan"] = mock_execute_api_plan_1
    
    # Test case 1: direct debug success.
    r1 = execute_debug_v2("task1", {}, {})
    assert r1["success"] == True
    assert r1["status"] == "debug_repaired"

    # Test case 2: request_feedback then success.
    _iter_count = 0
    def mock_execute_api_plan_2(*args, **kwargs):
        global _iter_count
        _iter_count += 1
        if _iter_count == 1:
            return {"status": "request_feedback"}
        return {"status": "done", "results": [{"success": True, "url": "/done"}]}
    globals()["execute_api_plan"] = mock_execute_api_plan_2
    r2 = execute_debug_v2("task2", {}, {})
    assert r2["success"] == True

    # Test case 3: parse failure.
    def mock_parse_api_plan_3(raw):
        return {"success": False, "error": "bad"}
    globals()["parse_api_plan"] = mock_parse_api_plan_3
    r3 = execute_debug_v2("task3", {}, {})
    assert r3["success"] == False
    assert r3["reason"] == "bad"

    # Test case 4: repeated failed execution.
    globals()["parse_api_plan"] = mock_parse_api_plan_1
    def mock_execute_api_plan_4(*args, **kwargs):
        return {"status": "failed", "failed_call": {}, "failed_result": {}}
    globals()["execute_api_plan"] = mock_execute_api_plan_4
    r4 = execute_debug_v2("task4", {}, {})
    assert r4["success"] == False
    assert r4["status"] == "failed"

    print("EXECUTE_DEBUG_V2 SELF TEST PASSED")
