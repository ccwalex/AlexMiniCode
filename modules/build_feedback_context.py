import json

from generate_trace import generate_trace
from infer_code_type import infer_code_type
from build_block_table import build_block_table

MODULE_METADATA = {
    "name": "build_feedback_context",
    "type": "function",
    "description": "Build next-turn planner/debug feedback context from RunState, trace data, cached reads, block tables, and successful inspection outputs.",
    "functions": [
        {
            "name": "build_feedback_context",
            "inputs": {
                "run_state": "RunState object containing execution state",
                "read_cache": "dict or None mapping file paths to cached content",
                "max_chars": "int maximum approximate feedback length"
            },
            "outputs": "dict with success bool, feedback string, trace dict, and error string or None"
        }
    ]
}

def truncate_text(text, max_chars, tail=False):
    if not isinstance(text, str):
        text = str(text)
    if len(text) <= max_chars:
        return text
    if tail:
        return "[TRUNCATED]...\n" + text[-max_chars:]
    return text[:max_chars] + "\n...[TRUNCATED]"

def build_shell_feedback_context(run_state, max_chars=8000, start_index=0):
    """Build shell-only feedback for tool_feedback_context."""
    if run_state is None:
        return {"success": False, "feedback": "", "error": "Invalid run_state"}

    feedback_parts = ["<shell_outputs>"]

    shells = getattr(run_state, "shells", [])
    try:
        start_index = max(0, int(start_index))
    except Exception:
        start_index = 0

    if shells:
        for sh in shells[start_index:]:
            cmd = sh.get("cmd", "")
            out = sh.get("output", "")
            feedback_parts.append(f'<shell cmd="{cmd}">')
            feedback_parts.append(truncate_text(out, 2000, tail=True))
            feedback_parts.append("</shell>")

    feedback_parts.append("</shell_outputs>")

    feedback_str = "\n".join(feedback_parts)
    if feedback_str.strip() == "<shell_outputs>\n</shell_outputs>":
        feedback_str = ""

    feedback_str = truncate_text(feedback_str, max_chars, tail=True)

    return {
        "success": True,
        "feedback": feedback_str,
        "error": None,
    }


def append_bounded(current, addition, max_chars=60000):
    addition = str(addition or "").strip()
    if not addition:
        return str(current or "")
    combined = f"{str(current or '').strip()}\n\n{addition}".strip()
    if len(combined) > max_chars:
        combined = "[TRUNCATED]\n" + combined[-max_chars:]
    return combined


def shell_feedback_from_execution_result(execution_result, run_state=None):
    """Extract shell-only feedback for tool_feedback_context."""
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


def subagent_feedback_from_execution_result(execution_result, max_chars=5000):
    """Return bounded subagent deliverables from an execution batch."""
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


def merge_execution_turn_feedback(
    execution_result,
    run_state=None,
    shell_context="",
    execution_notes="",
):
    """Merge shell and subagent results from one executed batch into planner context."""
    shell_feedback = shell_feedback_from_execution_result(
        execution_result,
        run_state=run_state,
    )
    subagent_feedback = subagent_feedback_from_execution_result(execution_result)
    if shell_feedback.strip():
        shell_context = append_bounded(shell_context, shell_feedback)
    if subagent_feedback.strip():
        execution_notes = append_bounded(execution_notes, subagent_feedback)
    return shell_context, execution_notes


def format_completed_batch_note(calls, *, tag="completed_batch", guidance=None):
    guidance = guidance or (
        "The previous API batch executed successfully but did not call /done. "
        "Do not repeat these successful calls. Continue the task and use /done only "
        "when all requirements are complete."
    )
    return (
        f"<{tag}>\n"
        f"{guidance}\n"
        f"<successful_calls>{json.dumps(calls, ensure_ascii=False)}</successful_calls>\n"
        f"</{tag}>"
    )


def build_feedback_context(run_state, read_cache=None, max_chars=12000):
    if run_state is None:
        return {"success": False, "feedback": "", "trace": {}, "error": "Invalid run_state"}

    if read_cache is None:
        read_cache = {}

    try:
        trace = generate_trace(run_state)
    except Exception as e:
        return {"success": False, "feedback": "", "trace": {}, "error": f"Failed to generate trace: {str(e)}"}

    feedback_parts = []
    feedback_parts.append("<run_feedback>")
    
    # Summary
    feedback_parts.append("<summary>")
    feedback_parts.append(str(trace.get("summary", "")))
    feedback_parts.append("</summary>")

    # Reads
    feedback_parts.append("<reads>")
    if read_cache:
        for path, content in read_cache.items():
            c_type = infer_code_type(path, content)
            feedback_parts.append(f"<read_file path=\"{path}\" type=\"{c_type}\">")
            
            content_snippet = truncate_text(content, 4000, tail=False)
            feedback_parts.append("<content>")
            feedback_parts.append(content_snippet)
            feedback_parts.append("</content>")
            
            if c_type in ["py", "html", "react", "ts"]:
                try:
                    blocks = build_block_table(content, path, c_type, 10, 1)
                    if blocks:
                        feedback_parts.append("<block_table>")
                        for b in blocks:
                            row = (f"id={b.get('id', '')} type={b.get('type', '')} "
                                   f"name={b.get('name', '')} lines={b.get('start_line', '')}-{b.get('end_line', '')} "
                                   f"parent={b.get('parent', '')} depth={b.get('depth', '')} vars={b.get('vars_defined', '')}")
                            feedback_parts.append(row)
                        feedback_parts.append("</block_table>")
                except Exception:
                    pass
            feedback_parts.append("</read_file>")
    feedback_parts.append("</reads>")

    # Shells
    feedback_parts.append("<shell_outputs>")
    if getattr(run_state, 'shells', []):
        for sh in run_state.shells:
            cmd = sh.get('cmd', '')
            out = sh.get('output', '')
            feedback_parts.append(f"Command: {cmd}")
            feedback_parts.append(truncate_text(out, 2000, tail=True))
    feedback_parts.append("</shell_outputs>")

    # Writes
    feedback_parts.append("<writes>")
    if getattr(run_state, 'writes', []):
        for wr in run_state.writes:
            feedback_parts.append(str(wr))
    feedback_parts.append("</writes>")

    # Edits
    feedback_parts.append("<edits>")
    if getattr(run_state, 'edits', []):
        for ed in run_state.edits:
            feedback_parts.append(str(ed))
    feedback_parts.append("</edits>")

    # Verifier Decisions
    feedback_parts.append("<verifier_decisions>")
    if getattr(run_state, 'verifier_decisions', []):
        for vd in run_state.verifier_decisions:
            feedback_parts.append(str(vd))
    feedback_parts.append("</verifier_decisions>")

    # Metadata Refreshes
    feedback_parts.append("<metadata_refreshes>")
    if getattr(run_state, 'metadata_refreshes', []):
        for mr in run_state.metadata_refreshes:
            feedback_parts.append(str(mr))
    feedback_parts.append("</metadata_refreshes>")

    # Errors
    feedback_parts.append("<errors>")
    if getattr(run_state, 'errors', []):
        for err in run_state.errors:
            feedback_parts.append(str(err))
    feedback_parts.append("</errors>")

    feedback_parts.append("</run_feedback>")

    feedback_str = "\n".join(feedback_parts)
    feedback_str = truncate_text(feedback_str, max_chars, tail=True)

    return {
        "success": True,
        "feedback": feedback_str,
        "trace": trace,
        "error": None
    }

if __name__ == "__main__":
    from run_state import RunState
    
    run_state = RunState(task="demo feedback task")
    run_state.add_call({"url": "/test"})
    run_state.add_read("fake.py", True)
    run_state.add_shell("ls -l", True, "total 0")
    run_state.add_write("fake.py", True, "", "")
    run_state.add_edit("fake.py", True, "", [])
    run_state.add_verifier_decision("write", "fake.py", True, "ok")
    run_state.add_metadata_refresh("fake.py", True, "")
    run_state.add_error("demo", "demo error")
    
    read_cache = {
        "fake.py": "def foo():\n    pass\n"
    }
    
    result = build_feedback_context(run_state, read_cache, max_chars=8000)
    
    assert result["success"] is True, "Expected success to be True"
    fb = result["feedback"]
    assert "<run_feedback>" in fb
    assert "<summary>" in fb
    assert "<reads>" in fb
    assert "<read_file" in fb
    assert "<content>" in fb
    assert "<block_table>" in fb
    assert "<shell_outputs>" in fb
    assert "<errors>" in fb
    
    assert isinstance(result["trace"], dict), "Expected trace to be a dict"

    shell_result = build_shell_feedback_context(run_state, max_chars=8000)
    assert shell_result["success"] is True
    shell_fb = shell_result["feedback"]
    assert "<shell_outputs>" in shell_fb
    assert "total 0" in shell_fb
    assert "<read_file" not in shell_fb

    exec_result = {
        "results": [
            {
                "url": "/shell",
                "payload": {"cmd": "pytest"},
                "output": {"output": "1 passed"},
            },
            {
                "url": "/subagent",
                "output": {
                    "subagent_result": {
                        "success": True,
                        "status": "completed",
                        "role": "explore",
                        "mode": "readonly",
                        "summary": "found issue",
                        "artifacts": [],
                    }
                },
            },
        ]
    }
    shell_context, notes = merge_execution_turn_feedback(exec_result, run_state=run_state)
    assert "pytest" in shell_context
    assert "found issue" in notes
    note = format_completed_batch_note([{"url": "/shell", "payload": {"cmd": "pytest"}}])
    assert "<completed_batch>" in note
    assert "pytest" in note

    print(fb)
    print("BUILD_FEEDBACK_CONTEXT SELF TEST PASSED")
