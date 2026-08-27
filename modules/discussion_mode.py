"""
Discussion mode: multi-turn conversation to resolve decision.json conflicts
by revising project.md and current_plan.md.
"""

from __future__ import annotations

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
- During ordinary discussion, do NOT emit full replacement file bodies unless the
  user explicitly asks for a draft before finalization.
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
    data.setdefault("end_triggered", False)
    defaults = discussion_defaults()
    data.setdefault("model", defaults["model"])
    data.setdefault("effort", defaults["effort"])
    data.setdefault("max_tokens", defaults["max_tokens"])
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


def reset_session(defaults: dict | None = None) -> dict:
    settings = discussion_defaults(defaults)
    session = {
        "messages": [],
        "selected_indices": [],
        "selected_entries": [],
        "phase": "chat",
        "pending_project": "",
        "pending_plan": "",
        "end_triggered": False,
        "model": settings["model"],
        "effort": settings["effort"],
        "max_tokens": settings["max_tokens"],
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
    settings = normalize_discussion_settings(
        model=model,
        effort=effort,
        max_tokens=max_tokens,
        source=source,
        defaults=defaults,
    )
    session.update(settings)
    save_session(session)
    return {"success": True, "settings": settings, "session": session, "note": "Discussion LLM settings come from global Model Config."}


def build_discussion_system_prompt(
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
        DISCUSSION_SYSTEM_PROMPT
        + "\n\n<selected_decisions>\n"
        + decisions_json
        + "\n</selected_decisions>\n\n<project>\n"
        + str(project or "")
        + "\n</project>\n\n<plan>\n"
        + str(current_plan or "")
        + "\n</plan>"
    )


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
    settings = normalize_discussion_settings(defaults=defaults)
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

    llm_messages = [{"role": "system", "content": system_prompt}]
    for item in session.get("messages", []):
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in ("user", "assistant") and isinstance(content, str):
            llm_messages.append({"role": role, "content": content})

    if is_end:
        llm_messages.append(
            {
                "role": "user",
                "content": (
                    "The user has sent [END]. Provide the final revised project.md "
                    "and current_plan.md using <project>...</project> and "
                    "<plan>...</plan> tags with full file contents."
                ),
            }
        )

    thinking = _normalize_effort(effort)

    from call_llm import call_llm_role

    try:
        raw = call_llm_role(
            role="discussion",
            messages=llm_messages,
            max_tokens=int(max_tokens),
            thinking=thinking,
            model=model,
            timeout=CFG.get_timeout("discussion_call", 240),
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

    if is_end and captured.get("has_any"):
        session["phase"] = "review"
    elif is_end:
        session["phase"] = "review"

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


def discussion_save_files(project: str, plan: str) -> dict:
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
    session["phase"] = "resolve"
    session["pending_project"] = project
    session["pending_plan"] = plan
    save_session(session)

    return {
        "success": True,
        "project_path": PROJECT_REL,
        "plan_path": PLAN_REL,
        "session": session,
    }


def discussion_discard_files() -> dict:
    session = load_session()
    session["pending_project"] = ""
    session["pending_plan"] = ""
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

    print("DISCUSSION_MODE SELF TEST PASSED")
