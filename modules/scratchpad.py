"""
In-task RAM scratchpad for preserving planner context within a single run.

Separate scratchpad instances should be used for the main planner loop and the
debug planner loop.
"""

from __future__ import annotations

import time

MODULE_METADATA = {
    "name": "scratchpad",
    "type": "class",
    "description": "In-memory scratchpad store for intra-task context preservation via /scratchpad API calls.",
    "functions": [
        {
            "name": "Scratchpad",
            "inputs": {"name": "str scratchpad label, e.g. main or debug"},
            "outputs": "Scratchpad object stored in RAM for the current task run",
        },
        {
            "name": "execute_scratchpad",
            "inputs": {
                "scratchpad": "Scratchpad object or None",
                "payload": "dict with action and optional content",
            },
            "outputs": "dict with success, action, content, length, and error",
        },
        {
            "name": "get_scratchpad_endpoint_doc",
            "inputs": {"loop": "str main or debug"},
            "outputs": "str endpoint documentation block for planner prompts",
        },
    ],
}


class Scratchpad:
    def __init__(self, name: str = "main"):
        self.name = str(name or "main")
        self._content = ""
        self._updated_at = None
        self._ops = []

    def read(self) -> str:
        return self._content

    def set(self, content: str) -> str:
        self._content = str(content)
        self._touch("set")
        return self._content

    def append(self, content: str) -> str:
        self._content += str(content)
        self._touch("append")
        return self._content

    def clear(self) -> str:
        self._content = ""
        self._touch("clear")
        return self._content

    def _touch(self, action: str):
        self._updated_at = time.time()
        self._ops.append(
            {
                "timestamp": self._updated_at,
                "action": action,
                "length": len(self._content),
            }
        )

    def ops(self):
        return list(self._ops)

    def to_dict(self):
        return {
            "name": self.name,
            "content": self._content,
            "length": len(self._content),
            "updated_at": self._updated_at,
            "ops": self.ops(),
        }


def execute_scratchpad(scratchpad, payload):
    if scratchpad is None:
        return {
            "success": False,
            "action": None,
            "content": "",
            "length": 0,
            "error": "scratchpad unavailable",
        }

    if not isinstance(payload, dict):
        return {
            "success": False,
            "action": None,
            "content": scratchpad.read(),
            "length": len(scratchpad.read()),
            "error": "scratchpad payload must be a dict",
        }

    action = str(payload.get("action") or "read").strip().lower()

    if action == "read":
        content = scratchpad.read()
        return {
            "success": True,
            "action": action,
            "content": content,
            "length": len(content),
            "error": None,
        }

    if action == "set":
        if "content" not in payload:
            return {
                "success": False,
                "action": action,
                "content": scratchpad.read(),
                "length": len(scratchpad.read()),
                "error": "scratchpad set requires content",
            }
        content = scratchpad.set(payload.get("content", ""))
        return {
            "success": True,
            "action": action,
            "content": content,
            "length": len(content),
            "error": None,
        }

    if action == "append":
        if "content" not in payload:
            return {
                "success": False,
                "action": action,
                "content": scratchpad.read(),
                "length": len(scratchpad.read()),
                "error": "scratchpad append requires content",
            }
        content = scratchpad.append(payload.get("content", ""))
        return {
            "success": True,
            "action": action,
            "content": content,
            "length": len(content),
            "error": None,
        }

    if action == "clear":
        content = scratchpad.clear()
        return {
            "success": True,
            "action": action,
            "content": content,
            "length": len(content),
            "error": None,
        }

    return {
        "success": False,
        "action": action,
        "content": scratchpad.read(),
        "length": len(scratchpad.read()),
        "error": f"unknown scratchpad action: {action}",
    }


def render_scratchpad_block(content: str, loop: str = "main", iteration=None) -> str:
    """
    Inject scratchpad into planner user prompt.

    Skip injection when:
    - content is empty, or
    - this is the first loop iteration (iteration <= 1).
    """
    if iteration is not None:
        try:
            if int(iteration) <= 1:
                return ""
        except Exception:
            pass

    text = str(content or "")
    if not text.strip():
        return ""

    label = str(loop or "main")
    return (
        f'<scratchpad loop="{label}">\n'
        f"{text}\n"
        f"</scratchpad>"
    )


def get_scratchpad_endpoint_doc(loop: str = "main") -> str:
    loop = str(loop or "main").strip().lower()
    is_debug = loop == "debug"

    if is_debug:
        description = """
The debug scratchpad is separate from the main scratchpad.

Use it during debug repair to preserve:
- failure hypotheses
- attempted fixes
- validation notes
- commands/results worth remembering across debug iterations

The debug scratchpad exists only for the current execute_debug_v2 session.
""".strip()
    else:
        description = """
The scratchpad is task-local RAM storage for notes the planner wants to keep
across turns within the same main run_task_v2 loop.

Use it to preserve:
- intermediate conclusions
- partial plans
- file/path findings
- constraints discovered during the task

The scratchpad is NOT persisted to disk and is cleared when the task ends.
""".strip()

    return f"""
8. /scratchpad

{description}

Payload:
{{
  "action": "read|set|append|clear",
  "content": "text required for set/append"
}}

Rules:
- Use read to inspect current scratchpad content.
- Use set to replace the entire scratchpad.
- Use append to add text to the end of the scratchpad.
- Use clear to empty the scratchpad.
- Scratchpad content is injected back into later planner turns for this loop only.
- Scratchpad is omitted from the prompt when empty or on the first loop turn.
- This loop uses the {"debug" if is_debug else "main"} scratchpad instance.
""".strip()


if __name__ == "__main__":
    pad = Scratchpad("main")
    assert execute_scratchpad(pad, {"action": "set", "content": "note 1"})["success"]
    assert pad.read() == "note 1"
    assert execute_scratchpad(pad, {"action": "append", "content": "\nnote 2"})["success"]
    assert "note 2" in pad.read()
    assert execute_scratchpad(pad, {"action": "clear"})["success"]
    assert pad.read() == ""
    assert render_scratchpad_block("", loop="main") == ""
    assert render_scratchpad_block("note", loop="main", iteration=1) == ""
    assert '<scratchpad loop="main">' in render_scratchpad_block("note", loop="main", iteration=2)
    assert "task-local RAM" in get_scratchpad_endpoint_doc("main")
    assert "debug scratchpad" in get_scratchpad_endpoint_doc("debug").lower()
    print("SCRATCHPAD SELF TEST PASSED")
