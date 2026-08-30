MODULE_METADATA = {
    "name": "parse_api_plan",
    "type": "function",
    "description": "Parse and validate Gen2 planner output into a normalized list of API calls without executing them.",
    "functions": [
        {
            "name": "parse_api_plan",
            "inputs": {
                "raw": "dict, list, or str planner output containing API-call list"
            },
            "outputs": "dict with success bool, normalized calls list, and error string or None"
        }
    ]
}

import ast
import json


ALLOWED_URLS = {
    "/read",
    "/write",
    "/edit",
    "/shell",
    "/subagent",
    "/request_feedback",
    "/scratchpad",
    "/write_llm_memory",
    "/done",
    "/conflict",
}


def strip_code_fences(text):
    text = str(text).strip()

    if text.startswith("```"):
        lines = text.splitlines()

        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    return text


def extract_json_candidate(text):
    """
    Best effort extraction of JSON object/list from a string.

    Prefer:
    1. Entire string as JSON.
    2. First JSON list.
    3. First JSON object with "calls".
    """

    text = strip_code_fences(text)

    if not text:
        return text

    # If the whole string already looks like JSON, use it directly.
    if text[0] in "[{":
        return text

    # Prefer first top-level-looking list.
    list_start = text.find("[")
    if list_start != -1:
        list_candidate = _extract_balanced(text, list_start, "[", "]")
        if list_candidate:
            return list_candidate

    # Fallback to first object.
    obj_start = text.find("{")
    if obj_start != -1:
        obj_candidate = _extract_balanced(text, obj_start, "{", "}")
        if obj_candidate:
            return obj_candidate

    return text


def _extract_balanced(text, start_index, open_char, close_char):
    depth = 0
    in_string = False
    escape = False

    for i in range(start_index, len(text)):
        ch = text[i]

        if escape:
            escape = False
            continue

        if ch == "\\":
            escape = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == open_char:
            depth += 1

        elif ch == close_char:
            depth -= 1

            if depth == 0:
                return text[start_index:i + 1]

    return ""


def parse_text_candidate(text):
    candidate = extract_json_candidate(text)

    try:
        return json.loads(candidate)
    except Exception:
        pass

    try:
        return ast.literal_eval(candidate)
    except Exception as e:
        raise ValueError(f"could not parse planner output as JSON/list: {e}")


def unwrap_raw_response(raw):
    """
    Extract likely planner content from common relay/model wrappers.
    """

    if isinstance(raw, list):
        return raw

    if isinstance(raw, str):
        return parse_text_candidate(raw)

    if not isinstance(raw, dict):
        raise ValueError(f"raw planner output must be dict, list, or str, got {type(raw).__name__}")

    if "calls" in raw:
        return raw

    # Already one call object.
    if "url" in raw and "payload" in raw:
        return [raw]

    # Common direct text wrappers.
    for key in ["content", "text", "output", "response"]:
        if key in raw and isinstance(raw[key], str):
            return parse_text_candidate(raw[key])

    # Common chat wrapper.
    message = raw.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return parse_text_candidate(content)

    # OpenAI-like wrapper.
    choices = raw.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]

        if isinstance(first, dict):
            msg = first.get("message")
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                return parse_text_candidate(msg["content"])

            if isinstance(first.get("text"), str):
                return parse_text_candidate(first["text"])

    # Gemini/relay-ish possible fields.
    candidates = raw.get("candidates")
    if isinstance(candidates, list) and candidates:
        first = candidates[0]
        if isinstance(first, dict):
            content = first.get("content")

            if isinstance(content, str):
                return parse_text_candidate(content)

            if isinstance(content, dict):
                parts = content.get("parts")
                if isinstance(parts, list):
                    joined = ""
                    for part in parts:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            joined += part["text"]
                    if joined:
                        return parse_text_candidate(joined)

    raise ValueError("could not find API-call list in planner output wrapper")


def normalize_candidate(candidate):
    if isinstance(candidate, dict):
        if "calls" not in candidate:
            raise ValueError("planner output dict missing 'calls' key")
        candidate = candidate["calls"]

    if not isinstance(candidate, list):
        raise ValueError(f"planner output must normalize to a list, got {type(candidate).__name__}")

    if not candidate:
        raise ValueError("planner output must contain at least one API call")

    calls = []

    for i, call in enumerate(candidate):
        calls.append(validate_call(call, i))

    for i, call in enumerate(calls[:-1]):
        if call.get("url") == "/subagent":
            raise ValueError(f"call {i} /subagent must be the final call in a planner turn")

    return calls


def validate_call(call, index):
    if not isinstance(call, dict):
        raise ValueError(f"call {index} must be a dict")

    url = call.get("url")
    payload = call.get("payload")

    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"call {index} missing non-empty string url")

    url = url.strip()

    if url not in ALLOWED_URLS:
        raise ValueError(f"call {index} has unknown url: {url}")

    if payload is None:
        payload = {}

    if not isinstance(payload, dict):
        raise ValueError(f"call {index} payload must be a dict")

    payload = dict(payload)

    if url == "/read":
        _require_str(payload, "path", index, url)

    elif url == "/write":
        _require_str(payload, "path", index, url)
        _require_str(payload, "content", index, url)

    elif url == "/edit":
        _require_str(payload, "path", index, url)
        edit_fns = payload.get("edit_fns")
        edit_fn = payload.get("edit_fn")

        if isinstance(edit_fns, list):
            if not edit_fns or not all(isinstance(fn, str) and fn.strip() for fn in edit_fns):
                raise ValueError(f"call {index} /edit payload.edit_fns must be a non-empty list of strings")
            for fn in edit_fns:
                if "def edit(" not in fn:
                    raise ValueError(f"call {index} /edit payload.edit_fns entries must define def edit(code):")
        else:
            _require_str(payload, "edit_fn", index, url)
            if "def edit(" not in edit_fn:
                raise ValueError(f"call {index} /edit payload.edit_fn must define def edit(code):")

    elif url == "/shell":
        _require_str(payload, "cmd", index, url)

    elif url == "/subagent":
        _require_str(payload, "task", index, url)
        role = str(payload.get("role") or "explore").strip().lower()
        if role not in {"explore", "review", "implement"}:
            raise ValueError(
                f"call {index} /subagent payload.role must be explore, review, or implement"
            )
        mode = str(payload.get("mode") or "process").strip().lower()
        if mode not in {"process", "readonly"}:
            raise ValueError(
                f"call {index} /subagent payload.mode must be process or readonly"
            )
        if mode == "readonly" and role == "implement":
            raise ValueError(
                f"call {index} /subagent implement role requires process mode"
            )
        files = payload.get("files", [])
        if files is None:
            files = []
        if not isinstance(files, list) or not all(
            isinstance(path, str) and path.strip() for path in files
        ):
            raise ValueError(
                f"call {index} /subagent payload.files must be a list of non-empty strings"
            )
        timeout_seconds = payload.get("timeout_seconds", 600)
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool):
            raise ValueError(
                f"call {index} /subagent payload.timeout_seconds must be numeric"
            )
        payload.update(
            {
                "role": role,
                "mode": mode,
                "files": files,
                "timeout_seconds": max(1, min(int(timeout_seconds), 3600)),
            }
        )

    elif url == "/request_feedback":
        # Empty payload is fine.
        pass

    elif url == "/scratchpad":
        action = str(payload.get("action") or "read").strip().lower()
        allowed = {"read", "set", "append", "clear"}
        if action not in allowed:
            raise ValueError(
                f"call {index} /scratchpad payload.action must be one of {sorted(allowed)}"
            )
        payload["action"] = action
        if action in {"set", "append"}:
            if "content" not in payload:
                raise ValueError(
                    f"call {index} /scratchpad payload.content is required for action {action}"
                )
            if payload["content"] is None:
                payload["content"] = ""
            if not isinstance(payload["content"], str):
                raise ValueError(
                    f"call {index} /scratchpad payload.content must be a string"
                )

    elif url == "/write_llm_memory":
        _require_str(payload, "issue", index, url)
        _require_str(payload, "solution", index, url)
        for key in ("check", "confidence"):
            value = payload.get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"call {index} {url} payload.{key} must be a string if present")

    elif url == "/done":
        summary = payload.get("summary", "")
        if summary is None:
            summary = ""
        if not isinstance(summary, str):
            raise ValueError(f"call {index} /done payload.summary must be a string if present")
        payload["summary"] = summary

    elif url == "/conflict":
        _require_str(payload, "conflict", index, url)

    return {
        "url": url,
        "payload": payload,
    }


def _require_str(payload, key, index, url):
    value = payload.get(key)

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"call {index} {url} payload.{key} must be a non-empty string")


def parse_api_plan(raw):
    try:
        candidate = unwrap_raw_response(raw)
        calls = normalize_candidate(candidate)

        return {
            "success": True,
            "calls": calls,
            "error": None,
        }

    except Exception as e:
        return {
            "success": False,
            "calls": [],
            "error": str(e),
        }


if __name__ == "__main__":
    direct_list = [
        {
            "url": "/read",
            "payload": {
                "path": "a.py"
            }
        }
    ]

    result = parse_api_plan(direct_list)
    assert result["success"], result
    assert result["calls"][0]["url"] == "/read"

    result = parse_api_plan({
        "calls": [
            {
                "url": "/done",
                "payload": {
                    "summary": "ok"
                }
            }
        ]
    })
    assert result["success"], result

    result = parse_api_plan('[{"url": "/request_feedback", "payload": {}}]')
    assert result["success"], result

    result = parse_api_plan({
        "calls": [
            {
                "url": "/conflict",
                "payload": {
                    "conflict": "ambiguous requirement"
                }
            }
        ]
    })
    assert result["success"], result
    assert result["calls"][0]["payload"]["conflict"] == "ambiguous requirement"

    result = parse_api_plan({
        "calls": [
            {
                "url": "/conflict",
                "payload": {
                    "conflict": ""
                }
            }
        ]
    })
    assert not result["success"], result

    fenced = """```json
[
  {
    "url": "/shell",
    "payload": {
      "cmd": "ls code"
    }
  }
]
```"""
    result = parse_api_plan(fenced)
    assert result["success"], result

    wrapped = {
        "message": {
            "content": '[{"url": "/request_feedback", "payload": {}}]'
        }
    }
    result = parse_api_plan(wrapped)
    assert result["success"], result

    result = parse_api_plan([
        {
            "url": "/bad",
            "payload": {}
        }
    ])
    assert not result["success"], result

    result = parse_api_plan([
        {
            "url": "/write",
            "payload": {
                "path": "a.py"
            }
        }
    ])
    assert not result["success"], result

    result = parse_api_plan([
        {
            "url": "/edit",
            "payload": {
                "path": "a.py"
            }
        }
    ])
    assert not result["success"], result

    result = parse_api_plan([
        {
            "url": "/edit",
            "payload": {
                "path": "a.py",
                "edit_fn": "print('bad')"
            }
        }
    ])
    assert not result["success"], result

    result = parse_api_plan([
        {
            "url": "/done",
            "payload": {}
        }
    ])
    assert result["success"], result
    assert result["calls"][0]["payload"]["summary"] == ""

    print("PARSE_API_PLAN SELF TEST PASSED")