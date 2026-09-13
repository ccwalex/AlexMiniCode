"""Endpoint allowlists for subagent worker types."""

from __future__ import annotations

MODULE_METADATA = {
    "name": "subagent_capabilities",
    "type": "function",
    "description": "Shared endpoint allowlists for review and implement subagent workers.",
    "functions": [
        {
            "name": "allowed_endpoints",
            "inputs": {"role": "str review or implement"},
            "outputs": "set of allowed endpoint url strings",
        },
        {
            "name": "is_allowed",
            "inputs": {"role": "str", "url": "str"},
            "outputs": "bool whether the endpoint is allowed for the role",
        },
        {
            "name": "normalize_subagent_role",
            "inputs": {"role": "str optional role or alias"},
            "outputs": "normalized review or implement",
        },
    ],
}

REVIEW_ENDPOINTS = {
    "/read",
    "/shell",
    "/scratchpad",
    "/drop_cache",
    "/request_feedback",
    "/done",
}

IMPLEMENT_ENDPOINTS = REVIEW_ENDPOINTS | {"/write", "/edit"}

BLOCKED_FOR_ALL = {"/subagent", "/write_llm_memory", "/conflict"}

SUBAGENT_ROLES = ("review", "implement")

ROLE_ALIASES = {
    "explore": "review",
}


def normalize_subagent_role(role) -> str:
    value = str(role or "review").strip().lower()
    value = ROLE_ALIASES.get(value, value)
    if value not in SUBAGENT_ROLES:
        raise ValueError(f"unsupported subagent role: {role}")
    return value


def allowed_endpoints(role: str) -> set[str]:
    normalized = normalize_subagent_role(role)
    if normalized == "implement":
        return set(IMPLEMENT_ENDPOINTS)
    return set(REVIEW_ENDPOINTS)


def is_allowed(role: str, url: str) -> bool:
    endpoint = str(url or "").strip()
    if endpoint in BLOCKED_FOR_ALL:
        return False
    return endpoint in allowed_endpoints(role)


if __name__ == "__main__":
    assert allowed_endpoints("review") == REVIEW_ENDPOINTS
    assert allowed_endpoints("implement") == IMPLEMENT_ENDPOINTS
    assert normalize_subagent_role("explore") == "review"
    assert is_allowed("review", "/read")
    assert not is_allowed("review", "/write")
    assert is_allowed("implement", "/edit")
    assert not is_allowed("implement", "/subagent")
    print("SUBAGENT_CAPABILITIES SELF TEST PASSED")
