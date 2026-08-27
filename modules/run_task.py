import json
import time

from cfg import CFG
from ensure_memory_files import ensure_memory_files
from extract_context_files import extract_context_files
from read_file import read_file
from render_read_cache import render_read_cache
from build_prompt import build_prompt
from call_planner import call_planner
from preview import preview
from extract_plan import extract_plan
from build_plan_module_registry import build_plan_module_registry
from verify_step import verify_step
from repair_write_step import repair_write_step
from write_file import write_file
from write_llm_memory import write_llm_memory
from run_shell import run_shell
from execute_debug import execute_debug
from append_run import append_run


MODULE_METADATA = {
    "name": "run_task",
    "type": "function",
    "description": "Run the main planner-verifier-executor loop for one agent task.",
    "functions": [
        {
            "name": "run_task",
            "inputs": {
                "task": "str current user task",
                "max_tokens": "int or None; planner max token budget, defaults to CFG.DEFAULT_MAX_TOKENS",
                "model": "str or None; model selector, defaults to CFG.DEFAULT_MODEL",
                "effort": "str or None; effort selector l/m/h or low/medium/high, defaults to CFG.DEFAULT_EFFORT"
            },
            "outputs": "None; executes task loop, writes files/runs commands as approved, and appends run summary"
        }
    ]
}


def run_task(
    task,
    max_tokens=None,
    model=None,
    effort=None,
):
    if max_tokens is None:
        max_tokens = CFG.DEFAULT_MAX_TOKENS

    if model is None:
        model = CFG.DEFAULT_MODEL

    if effort is None:
        effort = CFG.DEFAULT_EFFORT

    print(f"\n=== TASK: {task} ===\n")
    print(f"max_tokens={max_tokens}")

    provider = "gemini"

    if model == "pro":
        provider = "gemini"
        model = "gemini-3.1-pro-preview"
    elif model == "nova":
        provider = None
        model = None
    else:
        provider = "gemini"

    if effort == "m":
        effort = "medium"
    elif effort == "h":
        effort = "high"
    elif effort == "l":
        effort = "low"
    elif effort not in ["low", "medium", "high"]:
        effort = "medium"

    ensure_memory_files()

    read_cache = {}
    retries = 0
    iteration = 0
    feedback_loops = 0
    context = ""

    context_files = extract_context_files(task)

    for path in context_files:
        success, output = read_file(path)

        if success:
            read_cache[path] = str(output)
        else:
            context += f"""
<context_file_error path="{path}">
{output}
</context_file_error>
"""

    executed_trace = []
    active_plan = None

    while True:
        if iteration >= CFG.MAX_ITERATIONS:
            print("Max iterations reached")
            break

        if feedback_loops >= CFG.MAX_FEEDBACK_LOOPS:
            print("Too many feedback/debug loops")
            break

        if retries >= CFG.MAX_RETRIES:
            print("Max retries reached")
            break

        iteration += 1

        if active_plan is None:
            attached_context = render_read_cache(read_cache)

            system, user = build_prompt(
                task,
                context + "\n" + attached_context,
            )

            raw = call_planner(
                system,
                user,
                max_tokens=max_tokens,
                model=model,
                provider=provider,
                thinking=effort,
            )

            print("\n[Planner Output]")
            print(preview(raw))

            active_plan = extract_plan(raw)

            if not active_plan:
                print("Failed to parse plan")
                break

        print("\n[Active Plan]")
        for i, step in enumerate(active_plan):
            print(f"{i}: {step}")

        temp_modules_registry = build_plan_module_registry(active_plan)

        validation_failed = False
        rejection_reasons = []

        for i, step in enumerate(active_plan):
            decision = verify_step(
                step,
                modules_override=temp_modules_registry,
                read_cache=read_cache,
            )

            if decision.get("approved"):
                if step.get("action") == "run_shell" and decision.get("command"):
                    step["cmd"] = decision["command"]

                step["_verification"] = decision
                continue

            reason = decision.get("reason", "verification rejected")
            print(f"\nStep {i} rejected: {reason}")

            if step.get("action") == "write_file":
                repaired_ok = False
                last_repair_reason = reason

                for repair_attempt in range(CFG.MAX_WRITE_REPAIR_ATTEMPTS):
                    print(
                        f"Attempting local content repair for step {i} "
                        f"(attempt {repair_attempt + 1}/{CFG.MAX_WRITE_REPAIR_ATTEMPTS})"
                    )

                    repaired = repair_write_step(
                        step=step,
                        rejection_reason=last_repair_reason,
                        modules_override=temp_modules_registry,
                    )

                    if not repaired or not repaired.get("success"):
                        last_repair_reason = (
                            repaired.get("reason", "unknown repair failure")
                            if isinstance(repaired, dict)
                            else "no repair response"
                        )
                        continue

                    step["content"] = repaired["content"]

                    repaired_decision = verify_step(
                        step,
                        modules_override=temp_modules_registry,
                        read_cache=read_cache,
                    )

                    if repaired_decision.get("approved"):
                        print(f"Step {i} repaired and approved")
                        step["_verification"] = repaired_decision
                        repaired_ok = True
                        break

                    last_repair_reason = repaired_decision.get(
                        "reason",
                        "repaired write_file still rejected",
                    )

                if repaired_ok:
                    continue

                validation_failed = True
                rejection_reasons.append(
                    f"Step {i}: write_file repair failed: {last_repair_reason}"
                )
                continue

            if step.get("action") == "run_shell":
                replacement_cmd = decision.get("command")

                if replacement_cmd:
                    print(f"Attempting shell command replacement for step {i}: {replacement_cmd}")

                    original_cmd = step.get("cmd", "")
                    step["cmd"] = replacement_cmd

                    repaired_decision = verify_step(
                        step,
                        modules_override=temp_modules_registry,
                        read_cache=read_cache,
                    )

                    if repaired_decision.get("approved"):
                        print(f"Step {i} shell command replaced and approved")
                        step["_verification"] = repaired_decision
                        continue

                    step["cmd"] = original_cmd

                    print(
                        "Replacement command still rejected: "
                        f"{repaired_decision.get('reason', 'unknown rejection')}"
                    )

                print(f"Deleting rejected shell step {i}: {reason}")
                step["_delete"] = True
                continue

            validation_failed = True
            rejection_reasons.append(f"Step {i}: {reason}")

        active_plan = [
            step for step in active_plan
            if not step.get("_delete")
        ]

        if not active_plan:
            validation_failed = True
            rejection_reasons.append("All plan steps were deleted during validation.")

        if validation_failed:
            print("\nPlan rejected after attempted local repairs")

            context += "\n<verifier_rejection_feedback>\n"
            context += "\n".join(rejection_reasons)
            context += "\n</verifier_rejection_feedback>\n"

            active_plan = None
            retries += 1
            continue

        collected_outputs = ""
        execution_failed = False
        request_feedback_triggered = False

        failed_index = None
        failed_step = None
        failed_output = ""

        i = 0

        while i < len(active_plan):
            step = active_plan[i]
            action = step.get("action")

            print(f"\n[Step {i}] {step}")

            if action == "read_file":
                success, output = read_file(step["path"])
                output_tail = str(output)

                collected_outputs += (
                    f"\n[read_file {step['path']}]\n"
                    f"{output_tail}\n"
                )

                if success:
                    read_cache[step["path"]] = str(output)

                executed_trace.append(
                    {
                        "index": i,
                        "step": step,
                        "success": success,
                        "output": output_tail,
                    }
                )

                if not success:
                    print("\nRead failed")
                    print(output_tail)

                    execution_failed = True
                    failed_index = i
                    failed_step = step
                    failed_output = output
                    break

                i += 1
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
                    f"\n[write_file {step['path']}]\n"
                    f"{output}\n"
                )

                executed_trace.append(
                    {
                        "index": i,
                        "step": step,
                        "success": success,
                        "output": output,
                    }
                )

                if not success:
                    print("\nWrite failed")
                    print(output)

                    execution_failed = True
                    failed_index = i
                    failed_step = step
                    failed_output = output
                    break

                i += 1
                continue

            if action == "write_llm_memory":
                success, output = write_llm_memory(
                    step["issue"],
                    step["solution"],
                    step.get("check", ""),
                    step.get("confidence", "medium"),
                )

                collected_outputs += f"\n[write_llm_memory]\n{output}\n"

                executed_trace.append(
                    {
                        "index": i,
                        "step": step,
                        "success": success,
                        "output": output,
                    }
                )

                if not success:
                    print("\nMemory write failed")
                    print(output)

                    execution_failed = True
                    failed_index = i
                    failed_step = step
                    failed_output = output
                    break

                i += 1
                continue

            if action == "run_shell":
                run_id = f"run_{int(time.time())}_{i}"
                success, output = run_shell(step["cmd"], run_id)

                output_tail = str(output)[-4000:]

                collected_outputs += (
                    f"\n[run_shell {step['cmd']}]\n"
                    f"{output_tail}\n"
                )

                executed_trace.append(
                    {
                        "index": i,
                        "step": step,
                        "success": success,
                        "output": output_tail,
                    }
                )

                if not success:
                    print("\nExecution failed")

                    execution_failed = True
                    failed_index = i
                    failed_step = step
                    failed_output = output
                    break

                i += 1
                continue

            if action == "request_feedback":
                print("\nRequesting feedback")

                feedback_loops += 1
                request_feedback_triggered = True

                context += f"""
<request_feedback_context>
Previously executed steps and outputs:

{collected_outputs}
</request_feedback_context>
"""

                active_plan = None
                break

            print(f"Unknown action: {action}")

            execution_failed = True
            failed_index = i
            failed_step = step
            failed_output = f"Unknown action: {action}"
            break

        if request_feedback_triggered:
            continue

        if execution_failed and failed_step is not None:
            print("\nEntering nested debug executor")

            feedback_loops += 1

            debug_result = execute_debug(
                task=task,
                failed_step=failed_step,
                error_output=failed_output,
                executed_trace=executed_trace,
                max_tokens=max_tokens,
                read_cache=read_cache,
            )

            if not debug_result or not debug_result.get("success"):
                reason = (
                    debug_result.get("reason", "unknown debug failure")
                    if isinstance(debug_result, dict)
                    else "no debug result"
                )

                print(f"Debug executor failed: {reason}")

                context += f"""
<debug_failure>
Debug executor failed.

Reason:
{reason}

Failed step:
{json.dumps(failed_step, indent=2, ensure_ascii=False)}

Error:
{str(failed_output)[-4000:]}
</debug_failure>
"""

                retries += 1
                active_plan = None
                continue

            debug_steps = debug_result.get("plan", [])

            if not debug_steps:
                reason = "debug executor succeeded but returned no plan"
                print(reason)

                debug_outputs = ""
                debug_plan = []

                if isinstance(debug_result, dict):
                    debug_outputs = debug_result.get("outputs", "")
                    debug_plan = debug_result.get("plan", [])

                context += f"""
<debug_failure>
Debug executor failed.

Reason:
{reason}

Original failed step:
{json.dumps(failed_step, indent=2, ensure_ascii=False)}

Original error:
{str(failed_output)[-4000:]}

Executed debug plan before failure:
{json.dumps(debug_plan, indent=2, ensure_ascii=False)[:6000]}

Debug outputs collected before failure:
{str(debug_outputs)[-10000:]}
</debug_failure>
"""

                retries += 1
                active_plan = None
                continue

            debug_outputs = debug_result.get("outputs", "")
            collected_outputs += f"\n[debug_outputs]\n{debug_outputs}\n"

            for item in debug_result.get("executed_trace", []):
                executed_trace.append(
                    {
                        "index": f"debug_after_{failed_index}",
                        "step": item.get("step", {}),
                        "success": item.get("success", False),
                        "output": item.get("output", ""),
                    }
                )

            remaining_plan = active_plan[failed_index + 1:]

            if remaining_plan:
                print("\n[Continuing Remaining Plan After Debug]")
                active_plan = remaining_plan

                for j, step in enumerate(active_plan):
                    print(f"{j}: {step}")

                continue

            print("\nPlan completed successfully after debug")

            append_run(task, collected_outputs)
            return

        if not execution_failed:
            print("\nPlan completed successfully")

            append_run(task, collected_outputs)
            return

    print("\nTask ended without completion")