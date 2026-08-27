import json
import time

from cfg import CFG
from render_read_cache import render_read_cache
from build_debug_prompt import build_debug_prompt
from call_planner import call_planner
from preview import preview
from extract_plan import extract_plan
from build_plan_module_registry import build_plan_module_registry
from verify_step import verify_step
from read_file import read_file
from write_file import write_file
from run_shell import run_shell
from write_llm_memory import write_llm_memory


MODULE_METADATA = {
    "name": "execute_debug",
    "type": "function",
    "description": "Run a nested debug planner-executor loop to repair a runtime failure while preserving validation intent.",
    "functions": [
        {
            "name": "execute_debug",
            "inputs": {
                "task": "str original user task",
                "failed_step": "dict step that failed during execution",
                "error_output": "str or object containing failure output",
                "executed_trace": "list of previously executed trace dicts",
                "max_tokens": "int or None; debug planner max token budget, defaults to CFG.DEFAULT_MAX_TOKENS",
                "read_cache": "dict or None; cached file reads shared with main task loop"
            },
            "outputs": "dict with success bool, executed/debug plan, executed_trace, outputs, and reason"
        }
    ]
}


def execute_debug(
    task,
    failed_step,
    error_output,
    executed_trace,
    max_tokens=None,
    read_cache=None,
):
    if max_tokens is None:
        max_tokens = CFG.DEFAULT_MAX_TOKENS

    if read_cache is None:
        read_cache = {}

    debug_context = ""
    debug_executed = []
    collected_outputs = ""

    debug_feedback_loops = 0
    last_feedback_output_len = 0

    for debug_iter in range(CFG.MAX_DEBUG_ITERATIONS):
        attached_context = render_read_cache(read_cache)

        system, user = build_debug_prompt(
            task=task,
            failed_step=failed_step,
            error_output=error_output,
            executed_trace=executed_trace + debug_executed,
            context=debug_context + "\n" + attached_context,
        )

        raw = call_planner(
            system,
            user,
            max_tokens=max_tokens,
            provider="gemini",
            model="gemini-3.1-pro-preview",
            gemini_config=None,
        )

        print("\n[Debug Planner Output]")
        print(preview(raw))

        debug_plan_steps = extract_plan(raw)

        if not debug_plan_steps:
            return {
                "success": False,
                "plan": [x["step"] for x in debug_executed],
                "executed_trace": debug_executed,
                "outputs": collected_outputs,
                "reason": "debug planner returned no parseable plan",
            }

        print("\n[Debug Plan]")
        for i, step in enumerate(debug_plan_steps):
            print(f"D{i}: {step}")

        temp_debug_modules_registry = build_plan_module_registry(debug_plan_steps)

        requested_feedback = False
        runtime_failed = False
        validation_succeeded = False
        saw_validation_command = False

        for i, step in enumerate(debug_plan_steps):
            action = step.get("action")

            decision = verify_step(
                step,
                modules_override=temp_debug_modules_registry,
                read_cache=read_cache,
            )

            if not decision.get("approved"):
                return {
                    "success": False,
                    "plan": [x["step"] for x in debug_executed],
                    "executed_trace": debug_executed,
                    "outputs": collected_outputs,
                    "reason": f"debug step rejected: {decision.get('reason')}",
                }

            step["_verification"] = decision

            if action == "read_file":
                success, output = read_file(step["path"])
                output_tail = str(output)[-4000:]

                collected_outputs += (
                    f"\n[debug read_file {step['path']}]\n"
                    f"{output_tail}\n"
                )

                if success:
                    read_cache[step["path"]] = str(output)

                debug_executed.append(
                    {
                        "step": step,
                        "success": success,
                        "output": output_tail,
                    }
                )

                if not success:
                    return {
                        "success": False,
                        "plan": [x["step"] for x in debug_executed],
                        "executed_trace": debug_executed,
                        "outputs": collected_outputs,
                        "reason": f"debug read_file failed: {output_tail}",
                    }

                continue

            if action == "write_file":
                success, output = write_file(
                    step["path"],
                    step["content"],
                    verification=step.get("_verification"),
                )

                if success:
                    read_cache[step["path"]] = step["content"]

                collected_outputs += (
                    f"\n[debug write_file {step['path']}]\n"
                    f"{output}\n"
                )

                debug_executed.append(
                    {
                        "step": step,
                        "success": success,
                        "output": output,
                    }
                )

                if not success:
                    return {
                        "success": False,
                        "plan": [x["step"] for x in debug_executed],
                        "executed_trace": debug_executed,
                        "outputs": collected_outputs,
                        "reason": f"debug write_file failed: {output}",
                    }

                continue

            if action == "run_shell":
                saw_validation_command = True

                run_id = f"debug_{int(time.time())}_{i}"
                success, output = run_shell(step["cmd"], run_id)
                output_tail = str(output)[-4000:]

                collected_outputs += (
                    f"\n[debug run_shell {step['cmd']}]\n"
                    f"{output_tail}\n"
                )

                debug_executed.append(
                    {
                        "step": step,
                        "success": success,
                        "output": output_tail,
                    }
                )

                if not success:
                    failed_step = step
                    error_output = output
                    runtime_failed = True

                    debug_context += f"""
<debug_runtime_failure>
Command failed during debug:

{json.dumps(step, indent=2, ensure_ascii=False)}

Output:
{output_tail}
</debug_runtime_failure>
"""
                    break

                validation_succeeded = True

                return {
                    "success": True,
                    "plan": [x["step"] for x in debug_executed],
                    "executed_trace": debug_executed,
                    "outputs": collected_outputs,
                    "reason": "debug validation command succeeded",
                }

            if action == "write_llm_memory":
                success, output = write_llm_memory(
                    step["issue"],
                    step["solution"],
                    step.get("check", ""),
                    step.get("confidence", "medium"),
                )

                collected_outputs += f"\n[debug write_llm_memory]\n{output}\n"

                debug_executed.append(
                    {
                        "step": step,
                        "success": success,
                        "output": output,
                    }
                )

                if not success:
                    return {
                        "success": False,
                        "plan": [x["step"] for x in debug_executed],
                        "executed_trace": debug_executed,
                        "outputs": collected_outputs,
                        "reason": f"debug write_llm_memory failed: {output}",
                    }

                continue

            if action == "request_feedback":
                requested_feedback = True

                if i != len(debug_plan_steps) - 1:
                    return {
                        "success": False,
                        "plan": [x["step"] for x in debug_executed],
                        "executed_trace": debug_executed,
                        "outputs": collected_outputs,
                        "reason": "debug request_feedback was not the final action",
                    }

                new_feedback = collected_outputs[last_feedback_output_len:]

                if not new_feedback.strip():
                    return {
                        "success": False,
                        "plan": [x["step"] for x in debug_executed],
                        "executed_trace": debug_executed,
                        "outputs": collected_outputs,
                        "reason": (
                            "debug planner requested feedback but no new output was "
                            "collected since the previous feedback round"
                        ),
                    }

                debug_feedback_loops += 1

                if debug_feedback_loops > CFG.MAX_DEBUG_FEEDBACK_LOOPS:
                    return {
                        "success": False,
                        "plan": [x["step"] for x in debug_executed],
                        "executed_trace": debug_executed,
                        "outputs": collected_outputs,
                        "reason": (
                            "debug feedback loop limit reached "
                            f"({CFG.MAX_DEBUG_FEEDBACK_LOOPS})"
                        ),
                    }

                last_feedback_output_len = len(collected_outputs)

                debug_context += f"""
<debug_feedback>
Feedback round {debug_feedback_loops}/{CFG.MAX_DEBUG_FEEDBACK_LOOPS}.

New debug outputs since previous feedback:

{new_feedback[-10000:]}
</debug_feedback>

<debug_feedback_instruction>
Use the new feedback above.
Do not request feedback again unless you first read/execute something that produces new information.
</debug_feedback_instruction>
"""

                debug_executed.append(
                    {
                        "step": step,
                        "success": True,
                        "output": f"debug feedback round {debug_feedback_loops} requested",
                    }
                )

                break

            return {
                "success": False,
                "plan": [x["step"] for x in debug_executed],
                "executed_trace": debug_executed,
                "outputs": collected_outputs,
                "reason": f"unknown debug action: {action}",
            }

        if requested_feedback:
            continue

        if runtime_failed:
            continue

        if validation_succeeded:
            return {
                "success": True,
                "plan": [x["step"] for x in debug_executed],
                "executed_trace": debug_executed,
                "outputs": collected_outputs,
                "reason": "debug validation command succeeded",
            }

        if not saw_validation_command:
            return {
                "success": False,
                "plan": [x["step"] for x in debug_executed],
                "executed_trace": debug_executed,
                "outputs": collected_outputs,
                "reason": (
                    "debug plan completed without request_feedback, runtime failure, "
                    "or validation run_shell; refusing to loop silently"
                ),
            }

        return {
            "success": False,
            "plan": [x["step"] for x in debug_executed],
            "executed_trace": debug_executed,
            "outputs": collected_outputs,
            "reason": "debug iteration ended in unexpected state",
        }

    return {
        "success": False,
        "plan": [x["step"] for x in debug_executed],
        "executed_trace": debug_executed,
        "outputs": collected_outputs,
        "reason": "max debug iterations reached",
    }