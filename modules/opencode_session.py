"""
OpenCode session ID helpers for cache-friendly routing.

Session taxonomy:
- session_for(): job-scoped IDs for growing conversations (planner, debug,
  discussion, planner-delegated subagents). Prefixed with {job_id}:...
- universal_session(): stable cross-job IDs for auxiliary one-shot calls
  (verifiers, meta writer, context rewriter, dependency IO review). Prefixed
  with gen2-agent:... so multiple agent instances share OpenCode prompt cache.
"""

from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

MODULE_METADATA = {
    "name": "opencode_session",
    "type": "function",
    "description": "Manage stable x-opencode-session identifiers per conversation scope.",
}

_session_ctx: ContextVar[str | None] = ContextVar("opencode_session_id", default=None)
_job_id_ctx: ContextVar[str | None] = ContextVar("opencode_job_id", default=None)
_SESSION_ID_RE = re.compile(r"[^a-zA-Z0-9:_\-]+")
UNIVERSAL_PREFIX = "gen2-agent"


def sanitize_session_id(value) -> str:
    text = str(value or "").strip()
    if not text:
        text = uuid.uuid4().hex
    text = _SESSION_ID_RE.sub("-", text)
    return text[:128]


def build_session_id(job_id, scope, *parts) -> str:
    bits = [str(job_id or "").strip(), str(scope or "").strip()]
    bits.extend(str(part or "").strip() for part in parts if str(part or "").strip())
    return sanitize_session_id(":".join(bit for bit in bits if bit))


def get_opencode_session() -> str:
    current = _session_ctx.get()
    if current:
        return sanitize_session_id(current)
    return sanitize_session_id(uuid.uuid4().hex)


@contextmanager
def opencode_session_scope(session_id):
    token = _session_ctx.set(sanitize_session_id(session_id))
    try:
        yield sanitize_session_id(session_id)
    finally:
        _session_ctx.reset(token)


@contextmanager
def opencode_job_scope(job_id):
    token = _job_id_ctx.set(str(job_id or "").strip() or None)
    try:
        yield get_job_id()
    finally:
        _job_id_ctx.reset(token)


def get_job_id() -> str | None:
    value = _job_id_ctx.get()
    if value:
        return str(value).strip() or None
    return None


def session_for(scope, *parts) -> str:
    job_id = get_job_id()
    if job_id:
        return build_session_id(job_id, scope, *parts)
    return build_session_id("direct", scope, *parts)


def universal_session(scope, *parts) -> str:
    """Stable session ID for auxiliary roles; shared across jobs and agents."""
    bits = [UNIVERSAL_PREFIX, str(scope or "").strip()]
    bits.extend(str(part or "").strip() for part in parts if str(part or "").strip())
    return sanitize_session_id(":".join(bit for bit in bits if bit))


if __name__ == "__main__":
    long_id = "a" * 200
    assert len(sanitize_session_id(long_id)) == 128
    assert sanitize_session_id("job-1:planner") == "job-1:planner"
    assert build_session_id("job1", "subagent", "explore", "2") == "job1:subagent:explore:2"

    with opencode_session_scope("job-1:planner"):
        assert get_opencode_session() == "job-1:planner"
    assert get_opencode_session() != "job-1:planner"

    assert universal_session("verifier", "write") == "gen2-agent:verifier:write"
    assert universal_session("meta_writer") == "gen2-agent:meta_writer"
    with opencode_job_scope("job-42"):
        assert universal_session("meta_writer") == "gen2-agent:meta_writer"
        assert session_for("planner") == "job-42:planner"

    print("OPENCODE_SESSION SELF TEST PASSED")
