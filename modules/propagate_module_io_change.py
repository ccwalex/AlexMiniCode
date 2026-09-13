MODULE_METADATA = {
    "name": "propagate_module_io_change",
    "type": "function",
    "description": "After a tracked module's public output format changes, grep dependents and update them with implement subagents.",
    "functions": [
        {
            "name": "propagate_module_io_change",
            "inputs": {
                "path": "str changed file path",
                "pre_content": "str or None source before the change",
                "post_content": "str or None source after the change",
            },
            "outputs": "dict summarizing cascade actions",
        },
        {
            "name": "queue_dependency_cascade",
            "inputs": {
                "run_state": "RunState with pending_dependency_cascades map",
                "path": "str changed file path",
                "pre_content": "str or None source before the change",
            },
            "outputs": "None",
        },
        {
            "name": "flush_dependency_cascades",
            "inputs": {
                "run_state": "RunState with queued dependency cascades",
                "read_cache": "dict optional post-change file cache",
                "batch_id": "str optional progress batch id",
            },
            "outputs": "list of cascade summary dicts",
        },
    ],
}

import json
import os
import subprocess

from extract_public_contract import extract_public_contract, output_format_changed
from find_module_dependents import find_module_dependents
from infer_code_type import infer_code_type
from is_tracked import is_tracked
from read_file import read_file
from refresh_after_file_change import refresh_after_file_change
from opencode_session import universal_session
from subagent_runner import run_subagent


MAX_DEPTH = 3
MAX_IMPLEMENT = 8


def _subagent_depth():
    try:
        return max(0, int(os.environ.get("AGENT_SUBAGENT_DEPTH", "0") or 0))
    except Exception:
        return 0


def _read(path):
    ok, content = read_file(path)
    return content if ok else None


def _empty_llm_stats():
    return {"review_called": 0, "implement_calls": 0, "llm_used": False}


def _merge_llm_stats(target, nested):
    if not isinstance(nested, dict):
        return
    target["review_called"] = int(target.get("review_called") or 0) + int(
        nested.get("review_called") or 0
    )
    target["implement_calls"] = int(target.get("implement_calls") or 0) + int(
        nested.get("implement_calls") or 0
    )
    target["llm_used"] = bool(target.get("llm_used") or nested.get("llm_used"))


def _role_progress_detail(role_name):
    from model_config import get_role_config

    cfg = get_role_config(role_name)
    model = cfg.get("model") or "?"
    source = cfg.get("source") or "?"
    return f"{role_name} · {source} · {model}"


def _emit_dependency_substep(batch_id, substep_id, status, action, label, detail=""):
    if not batch_id:
        return
    from job_progress import emit_dependency_substep

    emit_dependency_substep(
        batch_id,
        substep_id,
        status,
        action=action,
        label=label,
        detail=detail,
    )


def _maybe_start_parent_dependency(batch_id, path, progress_state):
    if not batch_id or not isinstance(progress_state, dict):
        return
    if progress_state.get("parent_started"):
        return
    from job_progress import emit_turn_dependency

    emit_turn_dependency(batch_id, "running", str(path or ""))
    progress_state["parent_started"] = True
    progress_state["llm_started"] = True


def _llm_output_changed(path, pre_content, post_content, batch_id=None, progress_state=None):
    substep_id = f"review:{path}"
    role_name = "subagent_review"
    detail = f"{path} · {_role_progress_detail(role_name)}"
    _maybe_start_parent_dependency(batch_id, path, progress_state)
    _emit_dependency_substep(
        batch_id,
        substep_id,
        "running",
        "dependency_review",
        "backward deps · review",
        detail,
    )
    task = (
        "Did the public output or export format of this module change?\n"
        "Answer YES or NO on the first line, then a one-sentence reason.\n\n"
        f"<path>{path}</path>\n"
        f"<before>\n{(pre_content or '')[:12000]}\n</before>\n"
        f"<after>\n{(post_content or '')[:12000]}\n</after>"
    )
    result = run_subagent(
        task,
        role="review",
        files=[path],
        timeout_seconds=1200,
    )
    summary = str((result or {}).get("summary") or "").strip()
    first = summary.splitlines()[0].strip().upper() if summary else ""
    changed = first.startswith("YES")
    status = "done" if (result or {}).get("success") else "failed"
    verdict_detail = summary.splitlines()[0][:200] if summary else str((result or {}).get("error") or "")
    _emit_dependency_substep(
        batch_id,
        substep_id,
        status,
        "dependency_review",
        "backward deps · review",
        verdict_detail,
    )
    return changed, summary, result


def _update_dependent(
    changed_path,
    dependent,
    before_contract,
    after_contract,
    verdict,
    batch_id=None,
    progress_state=None,
):
    substep_id = f"implement:{dependent}"
    role_name = "subagent_implement"
    detail = f"{dependent} ← {changed_path} · {_role_progress_detail(role_name)}"
    _maybe_start_parent_dependency(batch_id, changed_path, progress_state)
    _emit_dependency_substep(
        batch_id,
        substep_id,
        "running",
        "dependency_implement",
        "backward deps · implement",
        detail,
    )
    contract_text = json.dumps(
        {
            "changed_path": changed_path,
            "before_outputs": (before_contract or {}).get("outputs"),
            "after_outputs": (after_contract or {}).get("outputs"),
            "verdict": verdict,
        },
        ensure_ascii=False,
        indent=2,
    )
    task = (
        f"Update {dependent} so it matches the new public output/export format of {changed_path}.\n"
        "Change only call sites and types that depend on the old output format.\n"
        f"<io_change>\n{contract_text}\n</io_change>"
    )
    result = run_subagent(
        task,
        role="implement",
        mode="process",
        files=[dependent, changed_path],
        timeout_seconds=1200,
    )
    success = bool((result or {}).get("success"))
    status = "done" if success else "failed"
    result_detail = str((result or {}).get("summary") or (result or {}).get("error") or "")[:200]
    _emit_dependency_substep(
        batch_id,
        substep_id,
        status,
        "dependency_implement",
        "backward deps · implement",
        result_detail,
    )
    return result


def propagate_module_io_change(
    path,
    pre_content=None,
    post_content=None,
    *,
    depth=0,
    visited=None,
    implement_count=0,
    run_state=None,
    batch_id=None,
    progress_state=None,
):
    llm_stats = _empty_llm_stats()
    summary = {
        "path": path,
        "tracked": False,
        "changed": False,
        "grepped": False,
        "dependents": [],
        "updates": [],
        "reason": "",
        "implement_count": implement_count,
        "review_called": 0,
        "implement_calls": 0,
        "llm_used": False,
    }
    if _subagent_depth() >= 1:
        summary["reason"] = "skipped inside nested subagent"
        return summary
    if not path or not isinstance(path, str):
        summary["reason"] = "invalid path"
        return summary
    if not is_tracked(path):
        summary["reason"] = "untracked"
        return summary
    summary["tracked"] = True

    visited = set(visited or [])
    if path in visited:
        summary["reason"] = "already visited"
        return summary
    if depth > MAX_DEPTH:
        summary["reason"] = "max cascade depth"
        return summary
    visited.add(path)

    if post_content is None:
        post_content = _read(path)
    code_type = infer_code_type(path, post_content or pre_content or "")
    before_contract = extract_public_contract(path, pre_content or "", code_type)
    after_contract = extract_public_contract(path, post_content or "", code_type)
    changed, reason, needs_llm = output_format_changed(before_contract, after_contract)
    verdict = reason
    if needs_llm:
        changed, verdict, _llm = _llm_output_changed(
            path,
            pre_content,
            post_content,
            batch_id=batch_id,
            progress_state=progress_state,
        )
        llm_stats["review_called"] += 1
        llm_stats["llm_used"] = True
        reason = "llm review: " + str(verdict)[:300]
    summary["changed"] = bool(changed)
    summary["reason"] = reason
    summary["review_called"] = llm_stats["review_called"]
    summary["implement_calls"] = llm_stats["implement_calls"]
    summary["llm_used"] = llm_stats["llm_used"]
    if not changed:
        return summary

    dependents = find_module_dependents(path)
    summary["grepped"] = True
    summary["dependents"] = list(dependents)
    for dependent in dependents:
        if dependent in visited:
            continue
        if implement_count >= MAX_IMPLEMENT:
            summary["updates"].append({"path": dependent, "skipped": "max implement calls"})
            break
        pre_dep = _read(dependent)
        child = _update_dependent(
            path,
            dependent,
            before_contract,
            after_contract,
            verdict,
            batch_id=batch_id,
            progress_state=progress_state,
        )
        implement_count += 1
        llm_stats["implement_calls"] += 1
        llm_stats["llm_used"] = True
        summary["implement_count"] = implement_count
        summary["implement_calls"] = llm_stats["implement_calls"]
        summary["llm_used"] = llm_stats["llm_used"]
        update = {
            "path": dependent,
            "success": bool((child or {}).get("success")),
            "summary": str((child or {}).get("summary") or "")[:500],
            "error": str((child or {}).get("error") or "")[:300],
        }
        summary["updates"].append(update)
        if not update["success"]:
            continue
        refresh_after_file_change(dependent, run_state=run_state)
        nested = propagate_module_io_change(
            dependent,
            pre_content=pre_dep,
            post_content=_read(dependent),
            depth=depth + 1,
            visited=visited,
            implement_count=implement_count,
            run_state=run_state,
            batch_id=batch_id,
            progress_state=progress_state,
        )
        implement_count = int(nested.get("implement_count") or implement_count)
        summary["implement_count"] = implement_count
        _merge_llm_stats(llm_stats, nested)
        summary["review_called"] = llm_stats["review_called"]
        summary["implement_calls"] = llm_stats["implement_calls"]
        summary["llm_used"] = llm_stats["llm_used"]
        summary["updates"].extend(nested.get("updates") or [])
        if nested.get("dependents"):
            summary["dependents"].extend(
                item for item in nested["dependents"] if item not in summary["dependents"]
            )
    return summary


def snapshot_pre_content(path, read_cache=None):
    if isinstance(read_cache, dict) and path in read_cache:
        return read_cache.get(path)
    content = _read(path)
    if content is not None:
        return content
    try:
        proc = subprocess.run(
            ["git", "show", f"HEAD:{path}"],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return proc.stdout
    except Exception:
        pass
    return None


def dependency_cascade_detail(path, cascade=None):
    if not isinstance(cascade, dict):
        return str(path or "")
    if not cascade.get("changed"):
        reason = str(cascade.get("reason") or "no I/O change").strip()
        return f"{path} ({reason})"
    dependents = cascade.get("dependents") or []
    updates = cascade.get("updates") or []
    ok = sum(1 for item in updates if isinstance(item, dict) and item.get("success"))
    reviews = int(cascade.get("review_called") or 0)
    implements = int(cascade.get("implement_calls") or 0)
    if reviews or implements:
        return f"{path} · {reviews} review · {implements} implement · {ok} updated"
    return f"{path} · {len(dependents)} dependents · {ok} updated"


def _pending_dependency_map(run_state):
    if run_state is None:
        return None
    pending = getattr(run_state, "pending_dependency_cascades", None)
    if not isinstance(pending, dict):
        pending = {}
        run_state.pending_dependency_cascades = pending
    return pending


def queue_dependency_cascade(run_state, path, pre_content=None):
    path = str(path or "").strip()
    if not path:
        return
    pending = _pending_dependency_map(run_state)
    if pending is None:
        return
    if path not in pending:
        pending[path] = {"path": path, "pre_content": pre_content}


def flush_dependency_cascades(run_state, read_cache=None, batch_id=None):
    pending = _pending_dependency_map(run_state)
    if not pending:
        return []

    items = list(pending.values())
    pending.clear()

    cascades = []
    for item in items:
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        pre_content = item.get("pre_content")
        post_content = read_cache.get(path) if isinstance(read_cache, dict) else None
        if post_content is None:
            post_content = _read(path)

        progress_state = {"parent_started": False, "llm_started": False}
        try:
            cascade = propagate_module_io_change(
                path,
                pre_content=pre_content,
                post_content=post_content,
                run_state=run_state,
                batch_id=batch_id,
                progress_state=progress_state,
            )
        except Exception as exc:
            if batch_id and progress_state.get("llm_started"):
                from job_progress import emit_turn_dependency, log_step

                emit_turn_dependency(batch_id, "failed", f"{path}: {exc}")
                log_step(f"[Gen2 Turn] backward deps {path} (failed)")
            raise

        if batch_id and cascade.get("llm_used"):
            from job_progress import emit_turn_dependency, log_step

            if not progress_state.get("parent_started"):
                emit_turn_dependency(batch_id, "running", dependency_cascade_detail(path))
            emit_turn_dependency(
                batch_id,
                "done",
                dependency_cascade_detail(path, cascade),
            )
            log_step(
                f"[Gen2 Turn] backward deps {path} (done) "
                f"{dependency_cascade_detail(path, cascade)}"
            )
        cascades.append(cascade)
    return cascades
