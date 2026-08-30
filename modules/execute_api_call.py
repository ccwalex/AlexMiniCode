from read_file import read_file
from write_file import write_file
from edit_file import edit_file
from run_shell import run_shell
from verify_write import verify_write
from shell_verifier import shell_verifier
from write_llm_memory import write_llm_memory
from refresh_after_file_change import refresh_after_file_change
from request_feedback import request_feedback
import traceback
from repair_write_step import repair_write_step
from scratchpad import execute_scratchpad
from conflict import execute_conflict
from subagent_runner import run_subagent

MODULE_METADATA = {
    "name": "execute_api_call",
    "type": "function",
    "description": "Execute one normalized Gen2 API call using file, shell, feedback, and sequential subagent modules.",
    "functions": [
        {
            "name": "execute_api_call",
            "inputs": {
                "call": "dict normalized API call with url and payload",
                "run_state": "RunState object or None for recording execution state",
                "read_cache": "dict or None mapping paths to cached content",
                "shell_instruction_prompt": "str shell permission/safety instruction prompt",
                "scratchpad": "Scratchpad object or None for in-task RAM notes",
                "mark_task_done": "bool whether /done should mark the shared RunState complete"
            },
            "outputs": "dict with success bool, url, payload, output object, error string or None, done bool, conflict bool, and request_feedback bool"
        }
    ]
}


def short_preview(text, max_chars=1000, tail=False):
    if not isinstance(text, str):
        text = str(text)

    if max_chars is None or max_chars <= 0:
        return ""

    if len(text) <= max_chars:
        return text

    if max_chars <= 12:
        return text[:max_chars]

    if tail:
        return "[TRUNCATED]\n" + text[-max_chars:]

    return text[:max_chars] + "\n[TRUNCATED]"


def _safe_call(method, *args, **kwargs):
    try:
        return method(*args, **kwargs)
    except Exception:
        return None


def _record_call(run_state, call, status="", output=None):
    if run_state is not None and hasattr(run_state, "add_call"):
        _safe_call(run_state.add_call, call, status=status, output=output)


def _record_read(run_state, path, success, content_preview="", error=""):
    if run_state is not None and hasattr(run_state, "add_read"):
        _safe_call(
            run_state.add_read,
            path,
            success,
            content_preview=content_preview,
            error=error,
        )


def _record_write(run_state, path, success, reason="", content_hash=""):
    if run_state is not None and hasattr(run_state, "add_write"):
        _safe_call(
            run_state.add_write,
            path,
            success,
            reason=reason,
            content_hash=content_hash,
        )


def _record_edit(run_state, path, success, reason="", mutation_log=None):
    if run_state is not None and hasattr(run_state, "add_edit"):
        _safe_call(
            run_state.add_edit,
            path,
            success,
            reason=reason,
            mutation_log=mutation_log or [],
        )


def _record_shell(run_state, cmd, success, output_preview="", run_id="", output=""):
    if run_state is not None and hasattr(run_state, "add_shell"):
        _safe_call(
            run_state.add_shell,
            cmd,
            success,
            output_preview=output_preview,
            run_id=run_id,
            output=output,
        )


def _record_verifier_decision(run_state, kind, target, decision):
    if run_state is None or not hasattr(run_state, "add_verifier_decision"):
        return

    if isinstance(decision, dict):
        approved = bool(decision.get("approved", False))
        reason = str(decision.get("reason", ""))
    else:
        approved = False
        reason = str(decision)

    _safe_call(
        run_state.add_verifier_decision,
        kind,
        target,
        approved,
        reason,
    )


def _record_error(run_state, stage, error, context=None):
    if run_state is not None and hasattr(run_state, "add_error"):
        _safe_call(
            run_state.add_error,
            stage,
            error,
            context=context,
        )


def _mark_completed(run_state, success=True):
    if run_state is not None and hasattr(run_state, "mark_completed"):
        _safe_call(run_state.mark_completed, success=success)


def _base_result(url, payload):
    return {
        "success": False,
        "url": url,
        "payload": payload,
        "output": None,
        "error": None,
        "done": False,
        "conflict": False,
        "request_feedback": False,
    }


def _record_scratchpad(run_state, action, success, content_preview="", error=""):
    if run_state is not None and hasattr(run_state, "add_scratchpad"):
        _safe_call(
            run_state.add_scratchpad,
            action,
            success,
            content_preview=content_preview,
            error=error,
        )


def execute_api_call(
    call,
    run_state=None,
    read_cache=None,
    shell_instruction_prompt="",
    scratchpad=None,
    mark_task_done=True,
):
    if read_cache is None:
        read_cache = {}

    if not isinstance(call, dict):
        return {
            "success": False,
            "url": None,
            "payload": {},
            "output": None,
            "error": "call must be a dict",
            "done": False,
            "conflict": False,
            "request_feedback": False,
        }

    url = call.get("url")
    payload = call.get("payload", {})

    if payload is None:
        payload = {}

    result = _base_result(url, payload)

    if not isinstance(url, str) or not url:
        result["error"] = "Missing url in call"
        return result

    if not isinstance(payload, dict):
        result["error"] = "payload must be a dict"
        return result

    try:
        if url == "/read":
            path = payload.get("path")

            if not path:
                result["error"] = "Missing path in /read payload"
                return result

            if path in read_cache:
                content = read_cache[path]

                _record_read(
                    run_state,
                    path,
                    True,
                    content_preview=short_preview(content),
                    error="cache hit",
                )

                result["success"] = True
                result["output"] = content
                result["request_feedback"] = True
                return result

            success, content_or_error = read_file(path)

            if success:
                read_cache[path] = content_or_error

                _record_read(
                    run_state,
                    path,
                    True,
                    content_preview=short_preview(content_or_error),
                )

                result["success"] = True
                result["output"] = content_or_error
                result["request_feedback"] = True
                return result

            _record_read(
                run_state,
                path,
                False,
                error=str(content_or_error),
            )

            result["error"] = str(content_or_error)
            return result

        if url == "/write":
            path = payload.get("path")
            content = payload.get("content")
            repair_attempt = 0
            if not path or content is None:
                result["error"] = "Missing path or content in /write payload"
                return result

            v_res = verify_write(path, content, use_llm=True)

            _record_verifier_decision(run_state, "write", path, v_res)

            if not isinstance(v_res, dict):
                result["error"] = f"verify_write returned non-dict result: {v_res}"
                return result

            if not v_res.get("approved"):
                result["error"] = v_res.get(
                    "reason",
                    "write verifier rejected content"
                )
            
                status = False
            
                while not status:
                    repair_attempt += 1
            
                    repair = repair_write_step(
                        
                            path,
                            content,
                        
                        result["error"],
                    )
            
                    if not repair.get("success"):
                        result["error"] = repair.get(
                            "reason",
                            "repair failed",
                        )
                        return result
            
                    content = repair["content"]
            
                    v_res = verify_write(
                        path,
                        content,
                        use_llm=True,
                    )
            
                    if not isinstance(v_res, dict):
                        result["error"] = (
                            f"verify_write returned non-dict result: {v_res}"
                        )
                        return result
            
                    status = bool(v_res.get("approved"))
            
                    if not status:
                        result["error"] = v_res.get(
                            "reason",
                            "write verifier rejected content",
                        )
            
                    if not status and repair_attempt >= 3:
                        return result

            verified_content = v_res.get(
                "content",
                v_res.get("cleaned_content", content),
            )

            w_success, w_msg = write_file(
                path,
                verified_content,
                verification=v_res,
            )

            _record_write(
                run_state,
                path,
                w_success,
                reason=str(w_msg),
                content_hash=str(v_res.get("content_hash", "")),
            )

            if not w_success:
                result["error"] = str(w_msg)
                return result

            read_cache[path] = verified_content

            refresh_res = refresh_after_file_change(
                path,
                run_state=run_state,
            )

            result["success"] = True
            result["output"] = {
                "write": w_msg,
                "refresh": refresh_res,
            }
            return result
        if url == "/write_llm_memory":
            issue = payload.get("issue", "")
            solution = payload.get("solution", "")
            check = payload.get("check", "")
            confidence = payload.get("confidence", "medium")
        
            if not issue or not solution:
                result["error"] = "Missing issue or solution in /write_llm_memory payload"
                return result
        
            mem_res = write_llm_memory(
                issue=issue,
                solution=solution,
                check=check,
                confidence=confidence,
            )
        
            result["success"] = True
            result["output"] = mem_res
            return result
        if url == "/edit":
            path = payload.get("path")

            edit_fns = []

            if isinstance(payload.get("edit_fns"), list):
                edit_fns.extend(payload.get("edit_fns"))

            if isinstance(payload.get("edit_fn"), str):
                edit_fns.append(payload.get("edit_fn"))

            if not path or not edit_fns:
                result["error"] = "Missing path or edit_fn/edit_fns in /edit payload"
                return result

            try:
                edit_res = edit_file(path, edit_fns)
            except Exception as e:
                tb = traceback.format_exc()
            
                _record_edit(
                    run_state,
                    path,
                    False,
                    reason=f"edit_file raised exception: {e}",
                    mutation_log=[],
                )
            
                result["error"] = f"edit_file raised exception: {e}"
                result["output"] = {
                    "success": False,
                    "path": path,
                    "reason": f"edit_file raised exception: {e}",
                    "traceback": tb,
                    "edit_fns_preview": [
                        short_preview(fn, 2000)
                        for fn in edit_fns
                    ],
                }
                return result

            if not isinstance(edit_res, dict):
                _record_edit(
                    run_state,
                    path,
                    False,
                    reason=f"edit_file returned non-dict result: {edit_res}",
                    mutation_log=[],
                )

                result["error"] = f"edit_file returned non-dict result: {edit_res}"
                return result

            edit_success = bool(edit_res.get("success"))
            edit_reason = str(
                edit_res.get(
                    "reason",
                    edit_res.get("error", "edit completed" if edit_success else "edit failed"),
                )
            )

            _record_edit(
                run_state,
                path,
                edit_success,
                reason=edit_reason,
                mutation_log=edit_res.get("mutation_log", []),
            )

            if not edit_success:
                result["error"] = edit_reason
                result["output"] = edit_res
                return result

            if "reconstructed_source" in edit_res and edit_res["reconstructed_source"] is not None:
                read_cache[path] = edit_res["reconstructed_source"]

            refresh_res = refresh_after_file_change(
                path,
                run_state=run_state,
            )

            result["success"] = True
            result["output"] = {
                "edit": edit_res,
                "refresh": refresh_res,
            }
            return result

        if url == "/shell":
            cmd = payload.get("cmd")

            if not cmd:
                result["error"] = "Missing cmd in /shell payload"
                return result

            s_res = shell_verifier(
                cmd,
                instruction_prompt=shell_instruction_prompt,
            )

            _record_verifier_decision(run_state, "shell", cmd, s_res)

            if not isinstance(s_res, dict):
                result["error"] = f"shell_verifier returned non-dict result: {s_res}"
                return result

            if not s_res.get("approved"):
                result["error"] = s_res.get("reason", "Shell command rejected")
                return result

            final_cmd = s_res.get("command") or cmd
            run_id = getattr(run_state, "run_id", "default_run_id") if run_state else "default_run_id"

            success, output = run_shell(final_cmd, run_id)

            _record_shell(
                run_state,
                final_cmd,
                success,
                output_preview=short_preview(output, 4000, tail=True),
                output=output,
                run_id=str(run_id),
            )

            if success:
                result["success"] = True
                result["output"] = output

                if s_res.get("is_inspection", s_res.get("inspection", False)):
                    result["request_feedback"] = True

                return result

            result["error"] = str(output)
            result["output"] = output
            return result

        if url == "/subagent":
            subagent_result = run_subagent(
                task=payload.get("task"),
                role=payload.get("role", "explore"),
                mode=payload.get("mode", "process"),
                files=payload.get("files", []),
                timeout_seconds=payload.get("timeout_seconds", 600),
            )
            if payload.get("mode", "process") == "process":
                # A process subagent may mutate through shell commands or stop
                # after a partial write, neither of which guarantees complete
                # artifact reporting. Conservatively discard all cached files.
                read_cache.clear()

            # A completed delegation call is execution-successful even when the
            # child reports task failure. The parent planner must receive that
            # structured result and decide how to continue; debug repair is for
            # broken API execution, not unsuccessful delegated work.
            result["success"] = True
            result["output"] = {"subagent_result": subagent_result}
            result["request_feedback"] = True
            return result

        if url == "/request_feedback":
            fb_res = request_feedback(run_state, read_cache)

            result["success"] = bool(
                isinstance(fb_res, dict) and fb_res.get("success")
            )
            result["output"] = fb_res
            result["request_feedback"] = True

            if not result["success"]:
                result["error"] = (
                    fb_res.get("error", "request_feedback failed")
                    if isinstance(fb_res, dict)
                    else "request_feedback returned non-dict result"
                )

            return result

        if url == "/scratchpad":
            sp_res = execute_scratchpad(scratchpad, payload)

            _record_scratchpad(
                run_state,
                sp_res.get("action"),
                bool(sp_res.get("success")),
                content_preview=short_preview(sp_res.get("content", ""), 1000),
                error=str(sp_res.get("error") or ""),
            )

            if not sp_res.get("success"):
                result["error"] = sp_res.get("error", "scratchpad operation failed")
                result["output"] = sp_res
                return result

            result["success"] = True
            result["output"] = sp_res
            return result

        if url == "/done":
            if mark_task_done:
                _mark_completed(run_state, success=True)

            result["success"] = True
            result["done"] = True
            result["output"] = payload.get("summary", "")
            return result

        if url == "/conflict":
            task_text = ""
            if run_state is not None:
                task_text = str(getattr(run_state, "task", "") or "")

            conflict_res = execute_conflict(task=task_text, payload=payload)

            if not conflict_res.get("success"):
                result["error"] = conflict_res.get(
                    "error",
                    "conflict operation failed",
                )
                result["output"] = conflict_res
                return result

            _mark_completed(run_state, success=False)

            result["success"] = True
            result["conflict"] = True
            result["output"] = conflict_res.get("item")
            return result

        result["error"] = f"Unknown url: {url}"
        return result

    except Exception as e:
        result["success"] = False
        result["error"] = f"execute_api_call raised exception: {str(e)}"

        _record_error(
            run_state,
            "execute_api_call",
            result["error"],
            context={
                "call": call,
            },
        )

        return result


if __name__ == "__main__":
    import sys

    sys.modules[__name__].read_file = lambda p: (True, "file content")
    sys.modules[__name__].verify_write = lambda p, c, use_llm=True: {
        "approved": True,
        "content": c,
        "content_hash": "hash",
        "reason": "ok",
    }
    sys.modules[__name__].write_file = lambda p, c, verification=None: (True, "write success")
    sys.modules[__name__].edit_file = lambda p, f: {
        "success": True,
        "reconstructed_source": "new edit content",
        "mutation_log": [{"op": "mock"}],
        "reason": "edit success",
    }
    sys.modules[__name__].shell_verifier = lambda c, instruction_prompt="": {
        "approved": True,
        "command": c,
        "is_inspection": False,
        "reason": "ok",
    }
    sys.modules[__name__].run_shell = lambda c, r: (True, "shell output")
    sys.modules[__name__].refresh_after_file_change = lambda p, run_state=None: {"success": True}
    sys.modules[__name__].request_feedback = lambda r, c: {
        "success": True,
        "feedback": "<feedback>ok</feedback>",
        "trace": {},
        "error": None,
    }

    class FakeRunState:
        def __init__(self):
            self.run_id = "test_run"
            self.completed = False
            self.calls = []
            self.reads = []
            self.writes = []
            self.edits = []
            self.shells = []
            self.verifier_decisions = []
            self.errors = []

        def add_call(self, call, status="", output=None):
            self.calls.append({"call": call, "status": status, "output": output})

        def add_read(self, path, success, content_preview="", error=""):
            self.reads.append({
                "path": path,
                "success": success,
                "content_preview": content_preview,
                "error": error,
            })

        def add_write(self, path, success, reason="", content_hash=""):
            self.writes.append({
                "path": path,
                "success": success,
                "reason": reason,
                "content_hash": content_hash,
            })

        def add_edit(self, path, success, reason="", mutation_log=None):
            self.edits.append({
                "path": path,
                "success": success,
                "reason": reason,
                "mutation_log": mutation_log or [],
            })

        def add_shell(self, cmd, success, output_preview="", run_id=""):
            self.shells.append({
                "cmd": cmd,
                "success": success,
                "output_preview": output_preview,
                "run_id": run_id,
            })

        def add_verifier_decision(self, kind, target, approved, reason=""):
            self.verifier_decisions.append({
                "kind": kind,
                "target": target,
                "approved": approved,
                "reason": reason,
            })

        def add_error(self, stage, error, context=None):
            self.errors.append({
                "stage": stage,
                "error": error,
                "context": context,
            })

        def mark_completed(self, success=False):
            self.completed = success

    rs = FakeRunState()
    rc = {}

    res = execute_api_call(
        {"url": "/read", "payload": {"path": "test.py"}},
        rs,
        rc,
    )
    assert res["success"], res
    assert rc["test.py"] == "file content"

    res2 = execute_api_call(
        {"url": "/read", "payload": {"path": "test.py"}},
        rs,
        rc,
    )
    assert res2["success"], res2

    res = execute_api_call(
        {"url": "/write", "payload": {"path": "w.py", "content": "c"}},
        rs,
        rc,
    )
    assert res["success"], res
    assert rc["w.py"] == "c"
    assert rs.verifier_decisions[-1]["kind"] == "write"

    res = execute_api_call(
        {"url": "/edit", "payload": {"path": "e.py", "edit_fn": "def edit(code):\n    return code"}},
        rs,
        rc,
    )
    assert res["success"], res
    assert rc["e.py"] == "new edit content"

    res = execute_api_call(
        {"url": "/shell", "payload": {"cmd": "ls"}},
        rs,
        rc,
    )
    assert res["success"], res
    assert rs.verifier_decisions[-1]["kind"] == "shell"

    res = execute_api_call(
        {"url": "/request_feedback", "payload": {}},
        rs,
        rc,
    )
    assert res["success"], res
    assert res["request_feedback"], res

    pad = __import__("scratchpad").Scratchpad("test")
    res = execute_api_call(
        {"url": "/scratchpad", "payload": {"action": "set", "content": "note"}},
        rs,
        rc,
        scratchpad=pad,
    )
    assert res["success"], res
    assert pad.read() == "note"

    res = execute_api_call(
        {"url": "/done", "payload": {}},
        rs,
        rc,
    )
    assert res["done"], res
    assert rs.completed

    sys.modules[__name__].verify_write = lambda p, c, use_llm=True: {
        "approved": False,
        "reason": "rejected",
    }
    sys.modules[__name__].repair_write_step = lambda p, c, reason: {
        "success": False,
        "reason": reason,
    }
    res = execute_api_call(
        {"url": "/write", "payload": {"path": "w2.py", "content": "c"}},
        rs,
        rc,
    )
    assert not res["success"], res
    assert "rejected" in res["error"], res

    print("EXECUTE_API_CALL SELF TEST PASSED")