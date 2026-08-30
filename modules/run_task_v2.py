MODULE_METADATA = {
    "name": "run_task_v2",
    "type": "function",
    "description": "Run the Gen2 planner-executor loop using compact API calls, structured feedback continuation, and Gen2 debug repair.",
    "functions": [
        {
            "name": "run_task_v2",
            "inputs": {
                "task": "str current user task",
                "max_tokens": "int or None planner max token budget",
                "model": "str or None model selector",
                "effort": "str or None effort selector l/m/h or low/medium/high",
                "llm_source": "str or None relay or cursor",
                "cursor_params": "list or None Cursor model parameters",
                "shell_instruction_prompt": "str shell permission/safety instructions",
                "max_iterations": "int or None",
                "max_feedback_loops": "int or None",
                "max_retries": "int or None"
            },
            "outputs": "dict with success bool, status, RunState, read_cache, outputs, and reason"
        }
    ]
}

import json

from cfg import CFG
from ensure_memory_files import ensure_memory_files
from build_prompt_v2 import build_prompt_v2
from call_llm import call_llm_role
from parse_api_plan import parse_api_plan
from structured_llm_retry import call_llm_role_with_parse_retry
from execute_api_plan import execute_api_plan
from execute_debug_v2 import execute_debug_v2
from extract_task_context import (
    extract_task_context,
    extract_module_registry_block,
    task_without_attached_context,
)
from render_file_context import render_file_context
from plugins.background_context_plugin import rewrite_task
from scratchpad import Scratchpad
from run_state import RunState
from append_run import append_run
from preview import preview
from model_config import get_role_config


def normalize_effort(effort):
    if effort is None:
        effort = getattr(CFG, "DEFAULT_EFFORT", "medium")

    if effort == "l":
        return "low"

    if effort == "m":
        return "medium"

    if effort == "h":
        return "high"

    if effort in ["low", "medium", "high"]:
        return effort

    return "medium"


def _cfg_int(name, fallback):
    value = getattr(CFG, name, fallback)
    try:
        return int(value)
    except Exception:
        return fallback


def _add_error(run_state, stage, error, context=None):
    if run_state is not None and hasattr(run_state, "add_error"):
        try:
            run_state.add_error(stage=stage, error=error, context=context)
        except Exception:
            pass


def _mark_completed(run_state, success):
    if run_state is not None and hasattr(run_state, "mark_completed"):
        try:
            run_state.mark_completed(success=success)
        except Exception:
            pass


def _append_run_safe(task, output):
    try:
        append_run(task, output)
    except Exception:
        pass


def _append_bounded(current, addition, max_chars=60000):
    addition = str(addition or "").strip()
    if not addition:
        return str(current or "")
    combined = f"{str(current or '').strip()}\n\n{addition}".strip()
    if len(combined) > max_chars:
        combined = "[TRUNCATED]\n" + combined[-max_chars:]
    return combined


def _shell_feedback_from_execution_result(execution_result, run_state=None):
    """
    Extract shell-only feedback for tool_feedback_context.

    File contents are not returned here; they are merged into file_context via read_cache.
    """
    blocks = []

    results = execution_result.get("results") or []
    for result in results:
        if not isinstance(result, dict) or result.get("url") != "/shell":
            continue

        payload = result.get("payload") or {}
        cmd = str(payload.get("cmd") or "").strip()
        output = result.get("output")

        if isinstance(output, dict):
            text = (
                output.get("output")
                or output.get("stdout")
                or output.get("stderr")
                or ""
            )
        else:
            text = output

        text = str(text or "").strip()
        if not text and not cmd:
            continue

        blocks.append(f'<shell cmd="{cmd}">')
        blocks.append(text or "(no output)")
        blocks.append("</shell>")

    if blocks:
        return "<shell_outputs>\n" + "\n".join(blocks) + "\n</shell_outputs>"

    return ""


def _subagent_feedback_from_execution_result(execution_result, max_chars=5000):
    """Return only bounded subagent deliverables, never child trace/context."""
    blocks = []
    for result in execution_result.get("results") or []:
        if not isinstance(result, dict) or result.get("url") != "/subagent":
            continue
        output = result.get("output")
        subagent_result = output.get("subagent_result") if isinstance(output, dict) else None
        if not isinstance(subagent_result, dict):
            continue
        safe_result = {
            "success": bool(subagent_result.get("success")),
            "status": str(subagent_result.get("status") or ""),
            "role": str(subagent_result.get("role") or ""),
            "mode": str(subagent_result.get("mode") or ""),
            "summary": str(subagent_result.get("summary") or ""),
            "artifacts": [
                str(path)[:300] for path in list(subagent_result.get("artifacts") or [])[:20]
            ],
            "run_id": str(subagent_result.get("run_id") or ""),
            "error": str(subagent_result.get("error") or "")[:1000],
        }
        encoded = json.dumps(safe_result, ensure_ascii=False)
        if len(encoded) > max_chars:
            overflow = len(encoded) - max_chars
            keep = max(0, len(safe_result["summary"]) - overflow - 100)
            safe_result["summary"] = safe_result["summary"][:keep] + "\n[TRUNCATED]"
            encoded = json.dumps(safe_result, ensure_ascii=False)
        if len(encoded) > max_chars:
            safe_result["artifacts"] = []
            safe_result["error"] = safe_result["error"][:200]
            encoded = json.dumps(safe_result, ensure_ascii=False)
        blocks.append(f"<subagent_result>{encoded}</subagent_result>")
    return "\n".join(blocks)


def _done_summary(execution_result):
    for result in reversed(execution_result.get("results") or []):
        if isinstance(result, dict) and result.get("url") == "/done":
            return str(result.get("output") or "").strip()
    return ""


def _format_debug_remarks(debug_result, max_output_chars=8000):
    if not isinstance(debug_result, dict):
        return ""

    reason = str(debug_result.get("reason") or "").strip()
    status = str(debug_result.get("status") or "").strip()
    debug_outputs = str(debug_result.get("outputs") or "").strip()

    if len(debug_outputs) > max_output_chars:
        debug_outputs = debug_outputs[-max_output_chars:]
        debug_outputs = f"[TRUNCATED]\n{debug_outputs}"

    return f"""
<debug_repair_context>
<status>{status or "debug_repaired"}</status>
<reason>{reason or "Debug repair succeeded."}</reason>
<debug_outputs>
{debug_outputs or "(no debug outputs captured)"}
</debug_outputs>
</debug_repair_context>
"""


def _split_main_plan_after_failure(calls, execution_result):
    """
    Split a failed main-plan execution into completed, failed, and remaining calls.

    Returns (failed_index, completed_calls, remaining_calls).
    failed_index is -1 when no results were recorded.
    """
    calls = calls or []
    results = execution_result.get("results") or []
    if not results:
        return -1, [], list(calls)

    failed_index = len(results) - 1
    return failed_index, calls[:failed_index], calls[failed_index + 1:]


def _calls_consumed_by_execution(calls, execution_result):
    results = execution_result.get("results") or []
    if not results:
        return 0
    return min(len(results), len(calls or []))


def _call_planner_llm(system_prompt, user_prompt, max_tokens, model, effort, llm_source=None, cursor_params=None):
    messages = [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "user",
            "content": user_prompt,
        },
    ]

    return call_llm_role_with_parse_retry(
        role="main_planner",
        messages=messages,
        is_valid=lambda raw: bool(
            parse_api_plan(raw).get("success")
            and parse_api_plan(raw).get("calls")
        ),
        parse_fallback_kind="execution",
        llm_call=call_llm_role,
        max_tokens=max_tokens,
        thinking=effort,
        model=model,
        source=llm_source,
        cursor_params=cursor_params,
        timeout=None,
    )
    

def run_task_v2(
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
):
    planner_cfg = get_role_config("main_planner")

    if max_tokens is None:
        max_tokens = planner_cfg["max_tokens"]

    if model is None:
        model = planner_cfg["model"]

    if llm_source is None:
        llm_source = planner_cfg.get("source")

    if cursor_params is None:
        cursor_params = planner_cfg.get("cursor_params")

    effort = normalize_effort(effort if effort is not None else planner_cfg["effort"])

    if max_iterations is None:
        max_iterations = _cfg_int("MAX_ITERATIONS", 10)

    if max_feedback_loops is None:
        max_feedback_loops = _cfg_int("MAX_FEEDBACK_LOOPS", 5)

    if max_retries is None:
        max_retries = _cfg_int("MAX_RETRIES", 3)

    print(f"\n=== GEN2 TASK: {task} ===\n")
    print(f"max_tokens={max_tokens}")
    print(f"model={model}")
    print(f"llm_source={llm_source}")
    print(f"effort={effort}")

    ensure_memory_files()

    run_state = RunState(task=task)
    read_cache = {}
    attached_paths = []
    attached_code_tables = {}
    
    if extract_task_context is not None:
        try:
            extracted_context = extract_task_context(task)
    
            read_cache.update(extracted_context.get("read_cache", {}))
            attached_paths = extracted_context.get("attached_paths", [])
            attached_code_tables = extracted_context.get("code_tables", {})
    
            if attached_paths:
                print("\n[Attached Files Parsed]")
                for path in attached_paths:
                    print(f"- {path}")
    
        except Exception as e:
            _add_error(
                run_state,
                "extract_task_context",
                str(e),
                context=None,
            )
    
    shell_context = ""
    execution_notes = ""
    outputs = ""
    main_scratchpad = Scratchpad(name="main")
    original_task = task
    module_registry_block = extract_module_registry_block(task)
    task_for_rewrite = task_without_attached_context(task) or task

    try:
        rewrite_result = rewrite_task(
            task=task_for_rewrite,
        )

        if isinstance(rewrite_result, dict) and rewrite_result.get("success"):
            rewritten = str(rewrite_result.get("rewritten_task") or "").strip()
            if rewritten:
                task = rewritten
            print("\n[Rewritten Task]")
            print(task[:4000] if task else "(empty)")
            if rewrite_result.get("skipped"):
                print("(task rewrite skipped; using original task)")
        else:
            reason = rewrite_result.get("error", "task rewrite failed") if isinstance(rewrite_result, dict) else "task rewrite failed"
            print(f"\n[Task Rewrite Warning] {reason}")
            print("(continuing with original task text, without duplicated attached context)")
            task = task_for_rewrite
            _add_error(run_state, "task_rewrite_plugin", reason, context=rewrite_result)
    except Exception as e:
        reason = f"task rewrite plugin failed: {e}"
        print(f"\n[Task Rewrite Warning] {reason}")
        print("(continuing with original task text, without duplicated attached context)")
        task = task_for_rewrite
        _add_error(run_state, "task_rewrite_plugin", reason)

    # Keep original task available for diagnostics without changing planner input.
    run_state.original_task = original_task
    run_state.rewritten_task = task

    iteration = 0
    feedback_loops = 0
    retries = 0

    while True:
        if iteration >= max_iterations:
            reason = "max iterations reached"
            print(f"\n{reason}")
            _add_error(run_state, "run_task_v2", reason)
            _mark_completed(run_state, False)

            return {
                "success": False,
                "status": "max_iterations",
                "run_state": run_state,
                "read_cache": read_cache,
                "outputs": outputs,
                "reason": reason,
            }

        # Allow the planner to consume the result of the final permitted
        # feedback-producing call. Fail only if another feedback turn exceeded
        # the configured allowance.
        if feedback_loops > max_feedback_loops:
            reason = "max feedback loops reached"
            print(f"\n{reason}")
            _add_error(run_state, "run_task_v2", reason)
            _mark_completed(run_state, False)

            return {
                "success": False,
                "status": "max_feedback_loops",
                "run_state": run_state,
                "read_cache": read_cache,
                "outputs": outputs,
                "reason": reason,
            }

        if retries >= max_retries:
            reason = "max retries reached"
            print(f"\n{reason}")
            _add_error(run_state, "run_task_v2", reason)
            _mark_completed(run_state, False)

            return {
                "success": False,
                "status": "max_retries",
                "run_state": run_state,
                "read_cache": read_cache,
                "outputs": outputs,
                "reason": reason,
            }

        iteration += 1

        print(f"\n[Gen2 Iteration {iteration}]")

        try:
            merged_file_context = render_file_context(
                read_cache,
                code_tables=attached_code_tables,
                path_order=attached_paths,
            )
            system_prompt, user_prompt = build_prompt_v2(
                task,
                shell_context,
                main_scratchpad.read(),
                iteration=iteration,
                file_context=merged_file_context,
                module_registry=module_registry_block,
                execution_notes=execution_notes,
            )
        except Exception as e:
            reason = f"build_prompt_v2 failed: {str(e)}"
            print(reason)
            _add_error(run_state, "build_prompt_v2", reason)
            retries += 1
            continue

        try:
            raw = _call_planner_llm(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                model=model,
                effort=effort,
                llm_source=llm_source,
                cursor_params=cursor_params,
            )
        except Exception as e:
            reason = f"planner LLM call failed: {str(e)}"
            print(reason)
            _add_error(run_state, "call_llm", reason)
            retries += 1
            continue

        print("\n[Gen2 Planner Output]")
        try:
            print(preview(raw))
        except Exception:
            print(str(raw)[:2000])

        parsed = parse_api_plan(raw)

        if not parsed.get("success"):
            reason = parsed.get("error", "parse_api_plan failed")
            print(f"\nParse failed: {reason}")

            _add_error(
                run_state,
                "parse_api_plan",
                reason,
                context={"raw": str(raw)[:4000]},
            )

            execution_notes = _append_bounded(execution_notes, f"""
<planner_parse_error>
{reason}
</planner_parse_error>
""")

            retries += 1
            continue

        calls = parsed.get("calls", [])

        print("\n[Gen2 API Calls]")
        for i, call in enumerate(calls):
            print(f"{i}: {call}")

        execution_result = execute_api_plan(
            calls=calls,
            run_state=run_state,
            read_cache=read_cache,
            shell_instruction_prompt=shell_instruction_prompt,
            scratchpad=main_scratchpad,
            mark_task_done=False,
        )
        read_cache = execution_result.get("read_cache", read_cache)

        status = execution_result.get("status")
        outputs += f"\n[execute_api_plan status={status}]\n"
        outputs += json.dumps(
            {
                "success": execution_result.get("success"),
                "status": execution_result.get("status"),
                "done": execution_result.get("done"),
                "error": execution_result.get("error"),
            },
            indent=2,
            ensure_ascii=False,
        )
        outputs += "\n"

        if status == "request_feedback":
            shell_feedback = _shell_feedback_from_execution_result(
                execution_result,
                run_state=run_state,
            )
            subagent_feedback = _subagent_feedback_from_execution_result(execution_result)

            if shell_feedback.strip():
                shell_context = _append_bounded(shell_context, shell_feedback)
            if subagent_feedback.strip():
                execution_notes = _append_bounded(execution_notes, subagent_feedback)

            feedback_loops += 1
            print("\n[Request Feedback Triggered]")
            print(f"[file_context paths] {list(read_cache.keys())}")
            if shell_feedback.strip():
                print(shell_feedback[:4000])
            if subagent_feedback.strip():
                print(subagent_feedback[:4000])
            continue

        if status == "done":
            reason = f"Gen2 task completed with status: {status}"
            print(f"\n{reason}")

            _mark_completed(run_state, True)
            _append_run_safe(task, outputs)

            return {
                "success": True,
                "status": status,
                "run_state": run_state,
                "read_cache": read_cache,
                "outputs": outputs,
                "reason": reason,
                "summary": _done_summary(execution_result),
            }

        if status == "completed":
            execution_notes = _append_bounded(
                execution_notes,
                "<completed_batch>\n"
                "The previous API batch executed successfully but did not call /done. "
                "Do not repeat these successful calls. Continue the task and use /done only "
                "when all requirements are complete.\n"
                f"<successful_calls>{json.dumps(calls, ensure_ascii=False)}</successful_calls>\n"
                "</completed_batch>",
            )
            print("\n[Main Loop] API batch completed without /done; requesting another planner turn")
            continue

        if status == "failed" and execution_result.get("conflict"):
            conflict_output = execution_result.get("conflict_output")
            conflict_text = execution_result.get("error") or "Task terminated via /conflict"
            if isinstance(conflict_output, dict):
                conflict_text = str(conflict_output.get("conflict") or conflict_text)

            reason = f"Gen2 task failed due to conflict: {conflict_text}"
            print(f"\n{reason}")

            _mark_completed(run_state, False)
            _append_run_safe(task, outputs)

            return {
                "success": False,
                "status": "failed",
                "run_state": run_state,
                "read_cache": read_cache,
                "outputs": outputs,
                "reason": reason,
                "conflict": True,
                "conflict_output": conflict_output,
            }

        if status == "failed":
            active_calls = calls
            active_execution = execution_result
            debug_cycle = 0
            max_debug_cycles = _cfg_int("MAX_DEBUG_CYCLES", 3)

            while True:
                debug_cycle += 1
                if debug_cycle > max_debug_cycles:
                    reason = f"maximum debug/resume cycles reached ({max_debug_cycles})"
                    print(f"\n[Main Loop] {reason}")
                    execution_notes = _append_bounded(
                        execution_notes,
                        "<debug_cycle_limit>\n"
                        f"{reason}\n"
                        f"<active_calls>{json.dumps(active_calls, ensure_ascii=False)}</active_calls>\n"
                        "</debug_cycle_limit>",
                    )
                    _add_error(run_state, "debug_cycle_limit", reason, context={"active_calls": active_calls})
                    retries += 1
                    break

                failed_call = active_execution.get("failed_call")
                failed_result = active_execution.get("failed_result")

                print("\n" + "=" * 72)
                print(f"[Main Loop] Planned execution failed — invoking debug repair (cycle {debug_cycle})")
                print("[Main Loop] Structured Retry only fixes malformed planner JSON before execution.")
                print("[Main Loop] Debug Loop repairs runtime failures from executed API steps.")
                print(f"[Main Loop] failed_call={preview(failed_call, 1500)}")
                print(f"[Main Loop] failed_result={preview(failed_result, 1500)}")

                failed_index, completed_calls, remaining_calls = _split_main_plan_after_failure(
                    active_calls,
                    active_execution,
                )

                debug_result = execute_debug_v2(
                    task=task,
                    failed_call=failed_call,
                    failed_result=failed_result,
                    run_state=run_state,
                    read_cache=read_cache,
                    shell_instruction_prompt=shell_instruction_prompt,
                    max_tokens=None,
                    model=None,
                    effort=None,
                    max_debug_iterations=None,
                    main_scratchpad=main_scratchpad,
                    main_plan_calls=active_calls,
                    failed_main_plan_index=failed_index,
                    completed_main_plan_calls=completed_calls,
                    remaining_main_plan_calls=remaining_calls,
                    module_registry=module_registry_block,
                    code_tables=attached_code_tables,
                    attached_paths=attached_paths,
                )

                outputs += "\n[execute_debug_v2]\n"
                outputs += json.dumps(
                    {
                        "success": debug_result.get("success") if isinstance(debug_result, dict) else False,
                        "status": debug_result.get("status") if isinstance(debug_result, dict) else "failed",
                        "reason": debug_result.get("reason") if isinstance(debug_result, dict) else "no debug result",
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                outputs += "\n"

                if isinstance(debug_result, dict):
                    run_state = debug_result.get("run_state", run_state)
                    read_cache = debug_result.get("read_cache", read_cache)
                    debug_shell_feedback = str(debug_result.get("shell_feedback") or "").strip()
                    if debug_shell_feedback:
                        shell_context = _append_bounded(shell_context, debug_shell_feedback)

                if isinstance(debug_result, dict) and debug_result.get("conflict"):
                    reason = debug_result.get("reason", "Debug terminated via /conflict")
                    print(f"\n{reason}")

                    _mark_completed(run_state, False)
                    _append_run_safe(task, outputs)

                    return {
                        "success": False,
                        "status": "failed",
                        "run_state": run_state,
                        "read_cache": read_cache,
                        "outputs": outputs,
                        "reason": reason,
                        "conflict": True,
                        "conflict_output": debug_result.get("conflict_output"),
                    }

                if not (isinstance(debug_result, dict) and debug_result.get("success")):
                    reason = (
                        debug_result.get("reason", "debug failed")
                        if isinstance(debug_result, dict)
                        else "debug returned no result"
                    )

                    print(f"\n[Main Loop] Debug repair failed (cycle {debug_cycle}): {reason}")

                    _add_error(
                        run_state,
                        "execute_debug_v2",
                        reason,
                        context={
                            "failed_call": failed_call,
                            "failed_result": failed_result,
                        },
                    )

                    execution_notes = _append_bounded(execution_notes, f"""
<debug_failure>
{reason}
<completed_main_plan_calls>
{json.dumps(completed_calls, ensure_ascii=False)}
</completed_main_plan_calls>
<remaining_main_plan_calls>
{json.dumps(remaining_calls, ensure_ascii=False)}
</remaining_main_plan_calls>
</debug_failure>
""")

                    retries += 1
                    break

                execution_notes = _append_bounded(
                    execution_notes,
                    _format_debug_remarks(debug_result),
                )

                if not remaining_calls:
                    execution_notes = _append_bounded(
                        execution_notes,
                        "<debug_repaired_no_remaining_calls>\n"
                        "The failed step was repaired, but the main task has not emitted /done. "
                        "Review task completion and emit /done only if all requirements are satisfied.\n"
                        "</debug_repaired_no_remaining_calls>",
                    )
                    print(
                        f"\n[Main Loop] Debug repair succeeded (cycle {debug_cycle}); "
                        "no remaining calls, returning to planner for explicit completion"
                    )
                    break

                print(f"\n[Main Loop] Debug repair succeeded (cycle {debug_cycle}); resuming {len(remaining_calls)} remaining main-plan step(s)")
                for j, call in enumerate(remaining_calls):
                    print(f"  resume {j}: {call}")

                pending_resume_calls = list(remaining_calls)
                resume_result = None
                resume_status = None
                resume_needs_replan = False

                while pending_resume_calls:
                    resume_result = execute_api_plan(
                        calls=pending_resume_calls,
                        run_state=run_state,
                        read_cache=read_cache,
                        shell_instruction_prompt=shell_instruction_prompt,
                        scratchpad=main_scratchpad,
                        mark_task_done=False,
                    )
                    read_cache = resume_result.get("read_cache", read_cache)

                    resume_status = resume_result.get("status")
                    outputs += f"\n[execute_api_plan resume status={resume_status}]\n"
                    outputs += json.dumps(
                        {
                            "success": resume_result.get("success"),
                            "status": resume_result.get("status"),
                            "done": resume_result.get("done"),
                            "error": resume_result.get("error"),
                        },
                        indent=2,
                        ensure_ascii=False,
                    )
                    outputs += "\n"

                    if resume_status == "request_feedback":
                        shell_feedback = _shell_feedback_from_execution_result(
                            resume_result,
                            run_state=run_state,
                        )
                        subagent_feedback = _subagent_feedback_from_execution_result(resume_result)

                        if shell_feedback.strip():
                            shell_context = _append_bounded(shell_context, shell_feedback)
                        if subagent_feedback.strip():
                            execution_notes = _append_bounded(execution_notes, subagent_feedback)

                        consumed = _calls_consumed_by_execution(
                            pending_resume_calls,
                            resume_result,
                        )
                        pending_resume_calls = pending_resume_calls[consumed:]
                        feedback_loops += 1

                        if pending_resume_calls:
                            print(
                                f"\n[Main Loop] Request feedback during resume; "
                                f"continuing {len(pending_resume_calls)} remaining step(s) without replanning"
                            )
                            if shell_feedback.strip():
                                print(shell_feedback[:4000])
                            if subagent_feedback.strip():
                                print(subagent_feedback[:4000])
                            continue

                        print("\n[Request Feedback Triggered After Debug Resume]")
                        if shell_feedback.strip():
                            print(shell_feedback[:4000])
                        if subagent_feedback.strip():
                            print(subagent_feedback[:4000])
                        break

                    if resume_status == "done":
                        reason = f"Gen2 task completed after debug repair and plan resume: {resume_status}"
                        print(f"\n{reason}")

                        _mark_completed(run_state, True)
                        _append_run_safe(task, outputs)

                        return {
                            "success": True,
                            "status": resume_status,
                            "run_state": run_state,
                            "read_cache": read_cache,
                            "outputs": outputs,
                            "reason": reason,
                            "summary": _done_summary(resume_result),
                        }

                    if resume_status == "completed":
                        execution_notes = _append_bounded(
                            execution_notes,
                            "<completed_resume_batch>\n"
                            "All resumed calls executed, but no /done call was reached. "
                            "Do not repeat these successful calls. Continue with a planner turn "
                            "for explicit task completion.\n"
                            f"<successful_calls>{json.dumps(pending_resume_calls, ensure_ascii=False)}</successful_calls>\n"
                            "</completed_resume_batch>",
                        )
                        print(
                            "\n[Main Loop] Resumed batch completed without /done; "
                            "returning to planner"
                        )
                        resume_needs_replan = True
                        break

                    if resume_status == "failed" and resume_result.get("conflict"):
                        conflict_output = resume_result.get("conflict_output")
                        conflict_text = resume_result.get("error") or "Task terminated via /conflict"
                        if isinstance(conflict_output, dict):
                            conflict_text = str(conflict_output.get("conflict") or conflict_text)

                        reason = f"Gen2 task failed due to conflict after debug resume: {conflict_text}"
                        print(f"\n{reason}")

                        _mark_completed(run_state, False)
                        _append_run_safe(task, outputs)

                        return {
                            "success": False,
                            "status": "failed",
                            "run_state": run_state,
                            "read_cache": read_cache,
                            "outputs": outputs,
                            "reason": reason,
                            "conflict": True,
                            "conflict_output": conflict_output,
                        }

                    if resume_status == "failed":
                        print(
                            f"\n[Main Loop] Resume failed after debug cycle {debug_cycle}; "
                            "starting another debug repair for the new failure"
                        )
                        active_calls = pending_resume_calls
                        active_execution = resume_result
                        break

                    reason = f"unknown execute_api_plan status after debug resume: {resume_status}"
                    print(reason)
                    _add_error(run_state, "execute_api_plan", reason, context=resume_result)
                    retries += 1
                    break

                if resume_status == "failed":
                    continue

                if resume_needs_replan:
                    break

                if resume_status == "request_feedback":
                    break

                if resume_status not in ["done", None]:
                    break

            continue

        reason = f"unknown execute_api_plan status: {status}"
        print(reason)

        _add_error(run_state, "execute_api_plan", reason, context=execution_result)
        retries += 1


if __name__ == "__main__":
    original_rewrite_task = rewrite_task
    original_build_prompt_v2 = build_prompt_v2
    original_call_llm = call_llm_role
    original_parse_api_plan = parse_api_plan
    original_execute_api_plan = execute_api_plan
    original_execute_debug_v2 = execute_debug_v2
    original_append_run = append_run

    call_counter = {
        "parse": 0,
        "execute": 0,
        "debug": 0,
        "append": 0,
    }

    def fake_rewrite_task(*args, **kwargs):
        return {
            "success": True,
            "rewritten_task": "rewritten demo task",
            "error": None,
        }

    def fake_build_prompt_v2(task, context="", scratchpad_content="", iteration=None, file_context="", module_registry="", execution_notes="", read_cache=None):
        _ = read_cache
        return "system", f"user: {task}\n{context}\n{scratchpad_content}\n{module_registry}\n{file_context}\n{execution_notes}"

    def fake_call_llm(*args, **kwargs):
        return [{"url": "/done", "payload": {"summary": "ok"}}]

    def fake_parse_success(raw):
        return {
            "success": True,
            "calls": [{"url": "/done", "payload": {"summary": "ok"}}],
            "error": None,
        }

    def fake_execute_done(calls, run_state=None, read_cache=None, shell_instruction_prompt="", **kwargs):
        return {
            "success": True,
            "status": "done",
            "run_state": run_state,
            "read_cache": read_cache,
            "results": [
                {
                    "success": True,
                    "url": "/done",
                    "payload": {"summary": "ok"},
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

    def fake_append_run(task, result):
        call_counter["append"] += 1

    globals()["rewrite_task"] = fake_rewrite_task
    globals()["build_prompt_v2"] = fake_build_prompt_v2
    globals()["call_llm_role"] = fake_call_llm
    globals()["parse_api_plan"] = fake_parse_success
    globals()["execute_api_plan"] = fake_execute_done
    globals()["append_run"] = fake_append_run

    result = run_task_v2("demo direct done", max_iterations=3)
    assert result["success"] is True, result
    assert result["status"] == "done", result

    # Request feedback then done.
    execute_calls = {"n": 0}

    def fake_execute_feedback_then_done(calls, run_state=None, read_cache=None, shell_instruction_prompt="", **kwargs):
        execute_calls["n"] += 1

        if execute_calls["n"] == 1:
            return {
                "success": True,
                "status": "request_feedback",
                "run_state": run_state,
                "read_cache": read_cache,
                "results": [
                    {
                        "success": True,
                        "url": "/read",
                        "payload": {"path": "a.py"},
                        "output": {"feedback": "<feedback>abc</feedback>"},
                        "done": False,
                        "request_feedback": True,
                    }
                ],
                "failed_call": None,
                "failed_result": None,
                "feedback": {"feedback": "<feedback>abc</feedback>"},
                "done": False,
                "error": None,
            }

        return fake_execute_done(calls, run_state, read_cache, shell_instruction_prompt)

    globals()["execute_api_plan"] = fake_execute_feedback_then_done

    result = run_task_v2("demo feedback then done", max_iterations=4, max_feedback_loops=3)
    assert result["success"] is True, result
    assert execute_calls["n"] == 2, execute_calls

    # Parse failure then success.
    parse_calls = {"n": 0}

    def fake_parse_fail_then_success(raw):
        parse_calls["n"] += 1

        if parse_calls["n"] == 1:
            return {
                "success": False,
                "calls": [],
                "error": "fake parse failure",
            }

        return fake_parse_success(raw)

    globals()["parse_api_plan"] = fake_parse_fail_then_success
    globals()["execute_api_plan"] = fake_execute_done

    result = run_task_v2("demo parse fail then success", max_iterations=4, max_retries=3)
    assert result["success"] is True, result
    assert parse_calls["n"] >= 2, parse_calls

    # Execution failure repaired by debug with no remaining plan steps.
    debug_execution_calls = {"n": 0}

    def fake_execute_failed(calls, run_state=None, read_cache=None, shell_instruction_prompt="", **kwargs):
        debug_execution_calls["n"] += 1
        if debug_execution_calls["n"] > 1:
            return fake_execute_done(calls, run_state, read_cache, shell_instruction_prompt)
        return {
            "success": False,
            "status": "failed",
            "run_state": run_state,
            "read_cache": read_cache,
            "results": [
                {
                    "success": False,
                    "url": "/shell",
                    "payload": {"cmd": "python code/test.py"},
                    "error": "runtime failure",
                }
            ],
            "failed_call": {"url": "/shell", "payload": {"cmd": "python code/test.py"}},
            "failed_result": {"success": False, "error": "runtime failure"},
            "feedback": None,
            "done": False,
            "error": "runtime failure",
        }

    def fake_execute_debug_success(*args, **kwargs):
        call_counter["debug"] += 1
        return {
            "success": True,
            "status": "debug_repaired",
            "plan": [],
            "results": [],
            "run_state": kwargs.get("run_state"),
            "read_cache": kwargs.get("read_cache"),
            "outputs": "debug ok",
            "reason": "fixed",
        }

    globals()["parse_api_plan"] = fake_parse_success
    globals()["execute_api_plan"] = fake_execute_failed
    globals()["execute_debug_v2"] = fake_execute_debug_success

    result = run_task_v2("demo debug", max_iterations=3, max_retries=2)
    assert result["success"] is True, result
    assert call_counter["debug"] >= 1, call_counter

    # Execution failure repaired by debug, then remaining plan resumes.
    resume_calls = {"n": 0}

    def fake_parse_two_step_plan(raw):
        return {
            "success": True,
            "calls": [
                {"url": "/shell", "payload": {"cmd": "python code/test.py"}},
                {"url": "/done", "payload": {"summary": "ok"}},
            ],
            "error": None,
        }

    def fake_execute_fail_then_resume_done(calls, run_state=None, read_cache=None, shell_instruction_prompt="", **kwargs):
        resume_calls["n"] += 1

        if len(calls) == 2:
            return {
                "success": False,
                "status": "failed",
                "run_state": run_state,
                "read_cache": read_cache,
                "results": [
                    {
                        "success": False,
                        "url": "/shell",
                        "payload": {"cmd": "python code/test.py"},
                        "error": "runtime failure",
                    }
                ],
                "failed_call": {"url": "/shell", "payload": {"cmd": "python code/test.py"}},
                "failed_result": {"success": False, "error": "runtime failure"},
                "feedback": None,
                "done": False,
                "error": "runtime failure",
            }

        return fake_execute_done(calls, run_state, read_cache, shell_instruction_prompt)

    globals()["parse_api_plan"] = fake_parse_two_step_plan
    globals()["execute_api_plan"] = fake_execute_fail_then_resume_done
    globals()["execute_debug_v2"] = fake_execute_debug_success

    result = run_task_v2("demo debug resume", max_iterations=3, max_retries=2)
    assert result["success"] is True, result
    assert resume_calls["n"] == 2, resume_calls

    # Debug repair + resume with request_feedback mid-resume should continue without replanning.
    resume_feedback_calls = {"n": 0, "batches": []}

    def fake_parse_three_step_plan(raw):
        return {
            "success": True,
            "calls": [
                {"url": "/shell", "payload": {"cmd": "python code/test.py"}},
                {"url": "/read", "payload": {"path": "a.py"}},
                {"url": "/request_feedback", "payload": {}},
                {"url": "/done", "payload": {"summary": "ok"}},
            ],
            "error": None,
        }

    def fake_execute_fail_then_feedback_resume(calls, run_state=None, read_cache=None, shell_instruction_prompt="", **kwargs):
        resume_feedback_calls["n"] += 1
        resume_feedback_calls["batches"].append(list(calls))

        if len(calls) == 4:
            return {
                "success": False,
                "status": "failed",
                "run_state": run_state,
                "read_cache": read_cache,
                "results": [
                    {
                        "success": False,
                        "url": "/shell",
                        "payload": {"cmd": "python code/test.py"},
                        "error": "runtime failure",
                    }
                ],
                "failed_call": {"url": "/shell", "payload": {"cmd": "python code/test.py"}},
                "failed_result": {"success": False, "error": "runtime failure"},
                "feedback": None,
                "done": False,
                "error": "runtime failure",
            }

        if len(calls) == 3:
            return {
                "success": True,
                "status": "request_feedback",
                "run_state": run_state,
                "read_cache": read_cache,
                "results": [
                    {
                        "success": True,
                        "url": "/read",
                        "payload": {"path": "a.py"},
                        "output": {"feedback": "<feedback>abc</feedback>"},
                        "done": False,
                        "request_feedback": True,
                    },
                    {
                        "success": True,
                        "url": "/request_feedback",
                        "payload": {},
                        "output": {"feedback": "<feedback>abc</feedback>"},
                        "done": False,
                        "request_feedback": True,
                    },
                ],
                "failed_call": None,
                "failed_result": None,
                "feedback": {"feedback": "<feedback>abc</feedback>"},
                "done": False,
                "error": None,
            }

        return fake_execute_done(calls, run_state, read_cache, shell_instruction_prompt)

    globals()["parse_api_plan"] = fake_parse_three_step_plan
    globals()["execute_api_plan"] = fake_execute_fail_then_feedback_resume
    globals()["execute_debug_v2"] = fake_execute_debug_success

    result = run_task_v2("demo debug resume feedback", max_iterations=3, max_retries=2, max_feedback_loops=3)
    assert result["success"] is True, result
    assert resume_feedback_calls["n"] == 3, resume_feedback_calls
    assert resume_feedback_calls["batches"][1] == [
        {"url": "/read", "payload": {"path": "a.py"}},
        {"url": "/request_feedback", "payload": {}},
        {"url": "/done", "payload": {"summary": "ok"}},
    ], resume_feedback_calls
    assert resume_feedback_calls["batches"][2] == [
        {"url": "/done", "payload": {"summary": "ok"}},
    ], resume_feedback_calls

    # Max retries.
    def fake_parse_always_fail(raw):
        return {
            "success": False,
            "calls": [],
            "error": "always fail",
        }

    globals()["parse_api_plan"] = fake_parse_always_fail
    globals()["execute_api_plan"] = fake_execute_done

    result = run_task_v2("demo max retries", max_iterations=10, max_retries=2)
    assert result["success"] is False, result
    assert result["status"] == "max_retries", result

    globals()["rewrite_task"] = original_rewrite_task
    globals()["build_prompt_v2"] = original_build_prompt_v2
    globals()["call_llm_role"] = original_call_llm
    globals()["parse_api_plan"] = original_parse_api_plan
    globals()["execute_api_plan"] = original_execute_api_plan
    globals()["execute_debug_v2"] = original_execute_debug_v2
    globals()["append_run"] = original_append_run

    print("RUN_TASK_V2 SELF TEST PASSED")