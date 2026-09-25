"""
Discussion mode: multi-turn conversation to resolve decision.json conflicts
by revising project.md and current_plan.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

_MODULES_DIR = os.path.dirname(os.path.abspath(__file__))
if _MODULES_DIR not in sys.path:
    sys.path.insert(0, _MODULES_DIR)

from cfg import CFG
from read_file import read_file
from conflict import decision_path, DECISION_REL_PATH
from model_config import get_role_config
from cursor_model_selection import normalize_cursor_params

MODULE_METADATA = {
    "name": "discussion_mode",
    "type": "function",
    "description": "Multi-turn discussion to resolve decision.json conflicts by editing project.md and current_plan.md.",
    "functions": [
        {
            "name": "build_discussion_system_prompt",
            "inputs": {
                "decisions": "list of selected decision dicts",
                "project": "str current project.md",
                "current_plan": "str current current_plan.md",
            },
            "outputs": "str system prompt",
        },
        {
            "name": "extract_proposed_files",
            "inputs": {"text": "str assistant response"},
            "outputs": "dict with project, plan, has_project, has_plan",
        },
        {
            "name": "discussion_send_message",
            "inputs": {
                "session": "dict discussion session state",
                "message": "str user message",
                "model": "str optional",
                "effort": "str optional",
                "max_tokens": "int optional",
            },
            "outputs": "dict with updated session and assistant response",
        },
    ],
}

PROJECT_REL = "agent_memory/core/project.md"
PLAN_REL = "agent_memory/planning/current_plan.md"
SESSION_REL = os.path.join("agent_memory", "discussion", "session.json")

END_TOKEN = "[END]"
END_FINALIZATION_NUDGE = (
    "The user has sent [END]. Provide the final revised project.md "
    "and current_plan.md using <project>...</project> and "
    "<plan>...</plan> tags with full file contents."
)

DISCUSSION_SYSTEM_PROMPT = """
You are a planning discussion assistant for a coding agent project.

Your job is to help the user resolve conflicts, ambiguities, and unresolvable
errors recorded in decision.json by improving two canonical documents:

1. project.md — durable project description, goals, constraints, architecture
2. current_plan.md — the active plan the execution agent should follow next

You will receive:
- <selected_decisions>: conflict entries the user wants to resolve in this session
- <project>: the current project.md content
- <plan>: the current current_plan.md content

Discussion rules:
- Have a normal multi-turn conversation with the user.
- Ask clarifying questions when requirements conflict or are underspecified.
- Propose concrete edits to project.md and/or current_plan.md when helpful.
- When proposing concrete document changes during chat, emit draft revisions inside
  <project>...</project> and/or <plan>...</plan> tags so the user can review them
  immediately. Partial or incremental drafts are fine during discussion.
- Focus on resolving the selected decision entries by making the project/plan
  documents clearer, more consistent, and actionable for the execution agent.
- Do not invent filesystem changes outside these two documents.
- Do not discuss implementation details unrelated to resolving the selected issues.

Finalization rules (when the user sends exactly [END]):
- The user has finished discussion and wants final revised documents.
- Output the complete revised project.md inside:
  <project>
  ...full file content...
  </project>
- Output the complete revised current_plan.md inside:
  <plan>
  ...full file content...
  </plan>
- You may include a brief summary before or after the tags, but both tags must
  contain the full final file contents.
- If only one file needs changes, still output both tags; unchanged files may
  repeat the current content verbatim.
- Do not wrap tag contents in markdown code fences.

Keep responses concise and practical unless the user asks for detail.
""".strip()


def _project_root() -> str:
    return CFG.PROJECT_ROOT


def _abs(rel: str) -> str:
    return os.path.join(_project_root(), rel.replace("\\", "/"))


def session_path() -> str:
    return _abs(SESSION_REL)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_read_rel(rel: str) -> str:
    success, content = read_file(rel.replace("\\", "/"))
    return content if success else ""


def load_decisions() -> list[dict]:
    path = decision_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = []
    if not isinstance(data, list):
        data = []
    out = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "index": i,
                "date": str(item.get("date") or ""),
                "task": str(item.get("task") or ""),
                "conflict": str(item.get("conflict") or ""),
            }
        )
    return out


def remove_decisions_by_entries(entries: list[dict]) -> dict:
    """Remove decision entries matching date+task+conflict tuples."""
    path = decision_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = []
    if not isinstance(data, list):
        data = []

    remove_keys = set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        key = (
            str(entry.get("date") or ""),
            str(entry.get("task") or ""),
            str(entry.get("conflict") or ""),
        )
        remove_keys.add(key)

    kept = []
    removed = []
    for item in data:
        if not isinstance(item, dict):
            kept.append(item)
            continue
        key = (
            str(item.get("date") or ""),
            str(item.get("task") or ""),
            str(item.get("conflict") or ""),
        )
        if key in remove_keys:
            removed.append(item)
        else:
            kept.append(item)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(kept, f, indent=2, ensure_ascii=False)

    return {
        "success": True,
        "removed_count": len(removed),
        "remaining_count": len(kept),
        "removed": removed,
        "path": DECISION_REL_PATH,
    }


def load_session() -> dict:
    path = session_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("messages", [])
    data.setdefault("selected_indices", [])
    data.setdefault("selected_entries", [])
    data.setdefault("phase", "chat")
    data.setdefault("pending_project", "")
    data.setdefault("pending_plan", "")
    data.setdefault("has_pending_proposals", False)
    data.setdefault("end_triggered", False)
    defaults = discussion_defaults()
    data.setdefault("model", defaults["model"])
    data.setdefault("effort", defaults["effort"])
    data.setdefault("max_tokens", defaults["max_tokens"])
    data.setdefault("cursor_agent_id", "")
    data.setdefault("cursor_agent_model", "")
    data.setdefault("cursor_agent_params", [])
    data.setdefault("cursor_context_hash", "")
    data.setdefault("updated_at", _now_iso())
    return data


def save_session(session: dict) -> dict:
    session = dict(session or {})
    session["updated_at"] = _now_iso()
    path = session_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2, ensure_ascii=False)
    return session


def clear_cursor_conversation_session(session: dict | None = None) -> dict:
    session = dict(session or load_session())
    agent_id = str(session.get("cursor_agent_id") or "").strip()
    if agent_id:
        from call_llm_cursor import close_cursor_conversation

        close_cursor_conversation(agent_id)
    session["cursor_agent_id"] = ""
    session["cursor_agent_model"] = ""
    session["cursor_agent_params"] = []
    session["cursor_context_hash"] = ""
    return session


def reset_session(defaults: dict | None = None) -> dict:
    settings = discussion_defaults(defaults)
    existing = load_session()
    clear_cursor_conversation_session(existing)
    session = {
        "messages": [],
        "selected_indices": [],
        "selected_entries": [],
        "phase": "chat",
        "pending_project": "",
        "pending_plan": "",
        "has_pending_proposals": False,
        "end_triggered": False,
        "model": settings["model"],
        "effort": settings["effort"],
        "max_tokens": settings["max_tokens"],
        "cursor_agent_id": "",
        "cursor_agent_model": "",
        "cursor_agent_params": [],
        "cursor_context_hash": "",
        "updated_at": _now_iso(),
    }
    return save_session(session)


def discussion_defaults(overrides: dict | None = None) -> dict:
    cfg = get_role_config("discussion")
    base = {
        "source": cfg["source"],
        "model": cfg["model"],
        "effort": cfg["effort"],
        "max_tokens": int(cfg["max_tokens"]),
    }
    if isinstance(overrides, dict):
        for key in ("source", "model", "effort", "max_tokens"):
            if overrides.get(key) is not None:
                base[key] = overrides[key]
    return base


def normalize_discussion_settings(
    model=None,
    effort=None,
    max_tokens=None,
    source=None,
    defaults: dict | None = None,
) -> dict:
    base = discussion_defaults(defaults)
    return {
        "source": str(source if source is not None else base["source"]).strip() or base["source"],
        "model": str(model if model is not None else base["model"]).strip() or base["model"],
        "effort": str(effort if effort is not None else base["effort"]).strip() or base["effort"],
        "max_tokens": int(max_tokens if max_tokens is not None else base["max_tokens"]),
    }


def discussion_update_settings(
    model=None,
    effort=None,
    max_tokens=None,
    source=None,
    defaults: dict | None = None,
    session: dict | None = None,
) -> dict:
    session = session if isinstance(session, dict) else load_session()
    prior_settings = normalize_discussion_settings(
        model=session.get("model"),
        effort=session.get("effort"),
        max_tokens=session.get("max_tokens"),
        source=session.get("source"),
        defaults=defaults,
    )
    settings = normalize_discussion_settings(
        model=model,
        effort=effort,
        max_tokens=max_tokens,
        source=source,
        defaults=defaults,
    )
    if (
        settings["source"] != prior_settings["source"]
        or settings["model"] != prior_settings["model"]
    ):
        clear_cursor_conversation_session(session)
    session.update(settings)
    save_session(session)
    return {"success": True, "settings": settings, "session": session, "note": "Discussion LLM settings come from global Model Config."}


def build_discussion_context_body(
    decisions: list[dict],
    project: str,
    current_plan: str,
) -> str:
    decisions_json = json.dumps(
        {"decisions": decisions or []},
        ensure_ascii=False,
        indent=2,
    )
    return (
        "<selected_decisions>\n"
        + decisions_json
        + "\n</selected_decisions>\n\n<project>\n"
        + str(project or "")
        + "\n</project>\n\n<plan>\n"
        + str(current_plan or "")
        + "\n</plan>"
    )


def build_discussion_system_prompt(
    decisions: list[dict],
    project: str,
    current_plan: str,
) -> str:
    return (
        DISCUSSION_SYSTEM_PROMPT
        + "\n\n"
        + build_discussion_context_body(decisions, project, current_plan)
    )


def build_discussion_context_update(
    decisions: list[dict],
    project: str,
    current_plan: str,
) -> str:
    return (
        "<context_update>\n"
        + build_discussion_context_body(decisions, project, current_plan)
        + "\n</context_update>"
    )


def compute_discussion_context_hash(
    decisions: list[dict],
    project: str,
    current_plan: str,
) -> str:
    payload = json.dumps(
        {
            "decisions": decisions or [],
            "project": project or "",
            "plan": current_plan or "",
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def extract_proposed_files(text: str) -> dict:
    text = str(text or "")

    def _extract(tag: str) -> tuple[str, bool]:
        pattern = rf"<{tag}\s*>([\s\S]*?)</{tag}\s*>"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            return "", False
        return match.group(1).strip("\n"), True

    project, has_project = _extract("project")
    plan, has_plan = _extract("plan")

    return {
        "project": project,
        "plan": plan,
        "has_project": has_project,
        "has_plan": has_plan,
        "has_any": has_project or has_plan,
        "has_both": has_project and has_plan,
    }


def _normalize_effort(effort):
    if effort is None:
        effort = getattr(CFG, "DEFAULT_EFFORT", "m")
    mapping = {"l": "low", "m": "medium", "h": "high"}
    if effort in mapping:
        return mapping[effort]
    if effort in ("low", "medium", "high"):
        return effort
    return "medium"


def _response_text(raw) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for key in ("content", "text", "output", "response", "message"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value
        choices = raw.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if isinstance(choice, dict):
                message = choice.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str):
                        return content
    return str(raw)


def _entries_for_indices(indices: list[int], all_decisions: list[dict]) -> list[dict]:
    idx_set = {int(i) for i in indices if isinstance(i, int) or str(i).isdigit()}
    out = []
    for item in all_decisions:
        if item.get("index") in idx_set:
            out.append(
                {
                    "index": item.get("index"),
                    "date": item.get("date", ""),
                    "task": item.get("task", ""),
                    "conflict": item.get("conflict", ""),
                }
            )
    return out


def _persist_cursor_session_fields(
    session: dict,
    *,
    agent_id: str,
    model: str,
    cursor_params,
    context_hash: str,
) -> None:
    session["cursor_agent_id"] = str(agent_id or "").strip()
    session["cursor_agent_model"] = str(model or "").strip()
    session["cursor_agent_params"] = normalize_cursor_params(cursor_params)
    session["cursor_context_hash"] = str(context_hash or "").strip()


def _build_discussion_llm_messages(session: dict, system_prompt: str, is_end: bool) -> list[dict]:
    llm_messages = [{"role": "system", "content": system_prompt}]
    for item in session.get("messages", []):
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in ("user", "assistant") and isinstance(content, str):
            llm_messages.append({"role": role, "content": content})
    if is_end:
        llm_messages.append({"role": "user", "content": END_FINALIZATION_NUDGE})
    return llm_messages


def _build_discussion_send_message(
    message: str,
    is_end: bool,
    *,
    context_changed: bool,
    decisions: list[dict],
    project: str,
    plan: str,
) -> str:
    send_message = str(message or "")
    if is_end:
        send_message = f"{send_message}\n\n{END_FINALIZATION_NUDGE}"
    if context_changed:
        send_message = (
            build_discussion_context_update(decisions, project, plan)
            + "\n\n"
            + send_message
        )
    return send_message


def _can_continue_cursor_session(session: dict, model: str, cursor_params) -> bool:
    agent_id = str(session.get("cursor_agent_id") or "").strip()
    stored_model = str(session.get("cursor_agent_model") or "").strip()
    stored_params = normalize_cursor_params(session.get("cursor_agent_params"))
    current_params = normalize_cursor_params(cursor_params)
    return bool(agent_id and stored_model == str(model or "").strip() and stored_params == current_params)


def _call_discussion_llm(
    *,
    session: dict,
    settings: dict,
    llm_messages: list[dict],
    send_message: str,
    context_hash: str,
    thinking: str,
    max_tokens: int,
    can_continue: bool,
) -> dict:
    from call_llm import call_llm_role
    from call_llm_cursor import (
        bootstrap_cursor_conversation,
        continue_cursor_conversation,
    )
    from opencode_session import session_for

    source = str(settings.get("source") or "opencode").strip().lower()
    model = settings["model"]
    cursor_params = normalize_cursor_params(get_role_config("discussion").get("cursor_params"))
    timeout = CFG.get_timeout("discussion_call", 240)

    if source != "cursor":
        return call_llm_role(
            role="discussion",
            messages=llm_messages,
            max_tokens=int(max_tokens),
            thinking=thinking,
            model=model,
            timeout=timeout,
            session_id=session_for("discussion"),
        )

    if can_continue:
        agent_id = str(session.get("cursor_agent_id") or "").strip()
        try:
            result = continue_cursor_conversation(
                agent_id,
                send_message,
                model=model,
                thinking=thinking,
                max_tokens=int(max_tokens),
                cursor_params=cursor_params,
            )
            _persist_cursor_session_fields(
                session,
                agent_id=result.get("agent_id") or agent_id,
                model=model,
                cursor_params=cursor_params,
                context_hash=context_hash,
            )
            result["discussion_cursor_mode"] = "continue"
            return result
        except Exception as exc:
            print(f"[Discussion Cursor] continuation failed: {exc}; re-bootstrapping")
            clear_cursor_conversation_session(session)

    try:
        result = bootstrap_cursor_conversation(
            llm_messages,
            model=model,
            thinking=thinking,
            max_tokens=int(max_tokens),
            cursor_params=cursor_params,
        )
        _persist_cursor_session_fields(
            session,
            agent_id=result.get("agent_id"),
            model=model,
            cursor_params=cursor_params,
            context_hash=context_hash,
        )
        result["discussion_cursor_mode"] = "bootstrap"
        return result
    except Exception as bootstrap_exc:
        print(
            f"[Discussion Cursor] bootstrap failed: {bootstrap_exc}; "
            "falling back to one-shot call_llm_role"
        )
        clear_cursor_conversation_session(session)
        result = call_llm_role(
            role="discussion",
            messages=llm_messages,
            max_tokens=int(max_tokens),
            thinking=thinking,
            model=model,
            timeout=timeout,
            session_id=session_for("discussion"),
        )
        if isinstance(result, dict):
            result = dict(result)
            result["discussion_cursor_mode"] = "one_shot_fallback"
        return result


def discussion_context(defaults: dict | None = None) -> dict:
    session = load_session()
    resolved_defaults = discussion_defaults(defaults)
    settings = normalize_discussion_settings(
        model=session.get("model"),
        effort=session.get("effort"),
        max_tokens=session.get("max_tokens"),
        defaults=resolved_defaults,
    )
    return {
        "decisions": load_decisions(),
        "project": _safe_read_rel(PROJECT_REL),
        "plan": _safe_read_rel(PLAN_REL),
        "project_path": PROJECT_REL,
        "plan_path": PLAN_REL,
        "defaults": resolved_defaults,
        "settings": settings,
        "session": session,
    }


def discussion_send_message(
    message: str,
    selected_indices: list | None = None,
    model=None,
    effort=None,
    max_tokens=None,
    defaults: dict | None = None,
    session: dict | None = None,
) -> dict:
    message = str(message or "")
    if not message.strip():
        return {"success": False, "error": "message required"}

    session = session if isinstance(session, dict) else load_session()
    settings = normalize_discussion_settings(
        model=session.get("model"),
        effort=session.get("effort"),
        max_tokens=session.get("max_tokens"),
        source=session.get("source"),
        defaults=defaults,
    )
    model = settings["model"]
    effort = settings["effort"]
    max_tokens = settings["max_tokens"]
    session.update(settings)

    all_decisions = load_decisions()

    if selected_indices is not None:
        session["selected_indices"] = [
            int(i) for i in selected_indices if str(i).strip() != ""
        ]
        session["selected_entries"] = _entries_for_indices(
            session["selected_indices"],
            all_decisions,
        )

    selected_entries = session.get("selected_entries") or []
    project = _safe_read_rel(PROJECT_REL)
    plan = _safe_read_rel(PLAN_REL)

    is_end = message.strip() == END_TOKEN
    if is_end:
        session["end_triggered"] = True

    session.setdefault("messages", [])
    session["messages"].append({"role": "user", "content": message, "at": _now_iso()})

    system_prompt = build_discussion_system_prompt(
        decisions=selected_entries,
        project=project,
        current_plan=plan,
    )

    llm_messages = _build_discussion_llm_messages(session, system_prompt, is_end)

    thinking = _normalize_effort(effort)
    context_hash = compute_discussion_context_hash(selected_entries, project, plan)
    cursor_params = normalize_cursor_params(get_role_config("discussion").get("cursor_params"))
    can_continue = (
        settings["source"] == "cursor"
        and _can_continue_cursor_session(session, model, cursor_params)
    )
    context_changed = can_continue and str(session.get("cursor_context_hash") or "") != context_hash
    send_message = _build_discussion_send_message(
        message,
        is_end,
        context_changed=context_changed,
        decisions=selected_entries,
        project=project,
        plan=plan,
    )

    try:
        raw = _call_discussion_llm(
            session=session,
            settings=settings,
            llm_messages=llm_messages,
            send_message=send_message,
            context_hash=context_hash,
            thinking=thinking,
            max_tokens=int(max_tokens),
            can_continue=can_continue,
        )
    except Exception as e:
        session["messages"].pop()
        save_session(session)
        return {"success": False, "error": str(e), "session": session}

    assistant_text = _response_text(raw)
    session["messages"].append(
        {"role": "assistant", "content": assistant_text, "at": _now_iso()}
    )

    captured = extract_proposed_files(assistant_text)
    if captured.get("has_project"):
        session["pending_project"] = captured["project"]
    if captured.get("has_plan"):
        session["pending_plan"] = captured["plan"]
    session["has_pending_proposals"] = bool(
        session.get("pending_project") or session.get("pending_plan")
    )

    save_session(session)

    return {
        "success": True,
        "assistant_message": assistant_text,
        "captured": captured,
        "is_end": is_end,
        "settings": settings,
        "session": session,
        "raw": raw,
    }


def save_planning_files(project: str, plan: str) -> dict:
    project = str(project or "")
    plan = str(plan or "")

    project_abs = _abs(PROJECT_REL)
    plan_abs = _abs(PLAN_REL)

    os.makedirs(os.path.dirname(project_abs), exist_ok=True)
    os.makedirs(os.path.dirname(plan_abs), exist_ok=True)

    with open(project_abs, "w", encoding="utf-8") as f:
        f.write(project)
    with open(plan_abs, "w", encoding="utf-8") as f:
        f.write(plan)

    session = load_session()
    session["pending_project"] = project
    session["pending_plan"] = plan
    session["has_pending_proposals"] = bool(project or plan)
    save_session(session)

    return {
        "success": True,
        "project_path": PROJECT_REL,
        "plan_path": PLAN_REL,
        "session": session,
    }


def discussion_save_files(project: str, plan: str) -> dict:
    result = save_planning_files(project, plan)
    session = result.get("session") or load_session()
    session["phase"] = "resolve"
    save_session(session)
    result["session"] = session
    return result


def discussion_discard_files() -> dict:
    session = load_session()
    session["pending_project"] = ""
    session["pending_plan"] = ""
    session["has_pending_proposals"] = False
    session["phase"] = "resolve"
    save_session(session)
    return {"success": True, "session": session}


def discussion_resolve_entries(resolved_entries: list[dict]) -> dict:
    result = remove_decisions_by_entries(resolved_entries)
    session = load_session()
    session["phase"] = "chat"
    session["end_triggered"] = False
    session["pending_project"] = ""
    session["pending_plan"] = ""
    session["has_pending_proposals"] = False
    save_session(session)
    result["session"] = session
    return result


if __name__ == "__main__":
    sample = """
Here is the summary.

<project>
# Demo Project
Updated goals.
</project>

<plan>
# Demo Plan
Step 1: fix ambiguity
</plan>
"""
    captured = extract_proposed_files(sample)
    assert captured["has_both"], captured
    assert "Demo Project" in captured["project"]
    assert "Demo Plan" in captured["plan"]

    prompt = build_discussion_system_prompt(
        decisions=[{"index": 0, "conflict": "ambiguous scope"}],
        project="# Old project",
        current_plan="# Old plan",
    )
    assert "<selected_decisions>" in prompt
    assert "<project>" in prompt
    assert "ambiguous scope" in prompt

    settings = normalize_discussion_settings(model="pro", effort="h", max_tokens=8192)
    assert settings["model"] == "pro"
    assert settings["effort"] == "h"
    assert settings["max_tokens"] == 8192

    ctx_hash_a = compute_discussion_context_hash(
        [{"index": 0, "conflict": "a"}],
        "# project",
        "# plan",
    )
    ctx_hash_b = compute_discussion_context_hash(
        [{"index": 0, "conflict": "b"}],
        "# project",
        "# plan",
    )
    assert ctx_hash_a != ctx_hash_b

    update = build_discussion_context_update(
        decisions=[{"index": 0, "conflict": "ambiguous scope"}],
        project="# Old project",
        current_plan="# Old plan",
    )
    assert update.startswith("<context_update>")
    assert "<selected_decisions>" in update

    session = reset_session()
    assert session.get("phase") == "chat"
    assert session.get("has_pending_proposals") is False
    assert session.get("cursor_agent_id") == ""

    print("DISCUSSION_MODE SELF TEST PASSED")
