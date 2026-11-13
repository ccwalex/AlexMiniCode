"""
Tests for discussion-mode Cursor SDK conversation continuation.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
from unittest import mock


CODE_DIR = os.path.dirname(os.path.abspath(__file__))
MODULES_DIR = os.path.join(CODE_DIR, "modules")

if CODE_DIR not in sys.path:
    sys.path.insert(0, CODE_DIR)
if MODULES_DIR not in sys.path:
    sys.path.insert(0, MODULES_DIR)


class _FakeRun:
    def __init__(self, text: str):
        self.status = "finished"
        self.result = text

    def wait(self):
        return self


class _FakeAgent:
    _counter = 0

    def __init__(self, agent_id: str | None = None):
        _FakeAgent._counter += 1
        self.agent_id = agent_id or f"agent-{_FakeAgent._counter}"
        self.sent: list[str] = []
        self.closed = False
        self.fail_next_send = False

    def send(self, message: str):
        if self.fail_next_send:
            self.fail_next_send = False
            raise RuntimeError("send failed")
        self.sent.append(str(message))
        if _FakeAgentModule.replies:
            text = _FakeAgentModule.replies.pop(0)
        else:
            text = f"assistant-reply-{len(self.sent)}"
        return _FakeRun(text)

    def close(self):
        self.closed = True


class _FakeCursorClient:
    launches: list[str] = []

    def __init__(self, workspace="."):
        self.workspace = workspace

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    @classmethod
    def launch_bridge(cls, workspace="."):
        cls.launches.append(workspace)
        return cls(workspace)


class _FakeAgentModule:
    created: list[tuple[_FakeAgent, dict]] = []
    resumed: list[tuple[_FakeAgent, str, object]] = []
    prompt_calls: list[tuple[str, object]] = []
    handles: dict[str, _FakeAgent] = {}
    replies: list[str] = []

    @classmethod
    def reset(cls):
        cls.created = []
        cls.resumed = []
        cls.prompt_calls = []
        cls.handles = {}
        cls.replies = []
        _FakeCursorClient.launches = []
        _FakeAgent._counter = 0

    @staticmethod
    def create(**kwargs):
        agent = _FakeAgent()
        _FakeAgentModule.created.append((agent, kwargs))
        _FakeAgentModule.handles[agent.agent_id] = agent
        return agent

    @staticmethod
    def resume(agent_id, options, **kwargs):
        existing = _FakeAgentModule.handles.get(agent_id)
        if existing is not None:
            agent = existing
        else:
            agent = _FakeAgent(agent_id=agent_id)
            _FakeAgentModule.handles[agent_id] = agent
        _FakeAgentModule.resumed.append((agent, agent_id, options))
        return agent

    @staticmethod
    def prompt(prompt, options, **kwargs):
        _FakeAgentModule.prompt_calls.append((prompt, options))
        return types.SimpleNamespace(status="finished", result=f"prompt:{prompt[:40]}")


def _install_fake_cursor_sdk():
    fake = types.ModuleType("cursor_sdk")
    fake.Agent = _FakeAgentModule
    fake.AgentOptions = lambda **kwargs: types.SimpleNamespace(**kwargs)
    fake.LocalAgentOptions = lambda **kwargs: types.SimpleNamespace(**kwargs)
    fake.ModelParameterValue = lambda **kwargs: types.SimpleNamespace(**kwargs)
    fake.ModelSelection = lambda **kwargs: types.SimpleNamespace(**kwargs)
    fake.CursorClient = _FakeCursorClient
    sys.modules["cursor_sdk"] = fake
    return fake


def _setup_temp_project():
    tmp = tempfile.mkdtemp(prefix="discussion_cursor_test_")
    memory = os.path.join(tmp, "agent_memory", "discussion")
    os.makedirs(memory, exist_ok=True)
    os.makedirs(os.path.join(tmp, "agent_memory", "core"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "agent_memory", "planning"), exist_ok=True)
    with open(os.path.join(tmp, "agent_memory", "core", "project.md"), "w", encoding="utf-8") as f:
        f.write("# Project\n")
    with open(os.path.join(tmp, "agent_memory", "planning", "current_plan.md"), "w", encoding="utf-8") as f:
        f.write("# Plan\n")
    return tmp


def _import_modules():
    import cfg
    import discussion_mode as dm
    import call_llm_cursor as clc

    return cfg, dm, clc


def test_bootstrap_and_continue_delta_only():
    _FakeAgentModule.reset()
    _install_fake_cursor_sdk()
    os.environ["CURSOR_API_KEY"] = "test-key"

    tmp = _setup_temp_project()
    import cfg

    cfg.CFG.PROJECT_ROOT = tmp

    cfg_mod, dm, clc = _import_modules()
    cfg_mod.CFG.PROJECT_ROOT = tmp
    clc._AGENT_HANDLES.clear()
    clc._CURSOR_CLIENT = None
    clc._CURSOR_CLIENT_WORKSPACE = None

    role_cfg = {
        "source": "cursor",
        "model": "composer-2.5",
        "effort": "m",
        "max_tokens": 4096,
        "cursor_params": [],
    }

    with mock.patch("model_config.get_role_config", return_value=role_cfg), mock.patch(
        "discussion_mode.get_role_config",
        return_value=role_cfg,
    ), mock.patch("discussion_mode.load_decisions", return_value=[]):
        dm.reset_session({"source": "cursor", "model": "composer-2.5"})

        first = dm.discussion_send_message("hello there")
        assert first["success"] is True
        assert first["raw"]["discussion_cursor_mode"] == "bootstrap"
        session = first["session"]
        assert session["cursor_agent_id"].startswith("agent-")

        created_agent = _FakeAgentModule.created[0][0]
        bootstrap_prompt = created_agent.sent[0]
        assert "<system>" in bootstrap_prompt
        assert "hello there" in bootstrap_prompt

        second = dm.discussion_send_message("follow up question")
        assert second["success"] is True
        assert second["raw"]["discussion_cursor_mode"] == "continue"
        assert created_agent.sent[-1] == "follow up question"
        assert "hello there" not in created_agent.sent[-1]
        assert "<system>" not in created_agent.sent[-1]


def test_reset_clears_cursor_agent():
    _FakeAgentModule.reset()
    _install_fake_cursor_sdk()
    os.environ["CURSOR_API_KEY"] = "test-key"

    tmp = _setup_temp_project()
    import cfg

    cfg.CFG.PROJECT_ROOT = tmp

    cfg_mod, dm, clc = _import_modules()
    cfg_mod.CFG.PROJECT_ROOT = tmp
    clc._AGENT_HANDLES.clear()
    clc._CURSOR_CLIENT = None
    clc._CURSOR_CLIENT_WORKSPACE = None

    role_cfg = {
        "source": "cursor",
        "model": "composer-2.5",
        "effort": "m",
        "max_tokens": 4096,
        "cursor_params": [],
    }

    with mock.patch("model_config.get_role_config", return_value=role_cfg), mock.patch(
        "discussion_mode.get_role_config",
        return_value=role_cfg,
    ), mock.patch("discussion_mode.load_decisions", return_value=[]):
        dm.discussion_send_message("hello")
        session = dm.load_session()
        agent_id = session["cursor_agent_id"]
        assert agent_id
        assert agent_id in clc._AGENT_HANDLES

        dm.reset_session()
        session = dm.load_session()
        assert session["cursor_agent_id"] == ""
        assert agent_id not in clc._AGENT_HANDLES


def test_model_change_rebootstraps():
    _FakeAgentModule.reset()
    _install_fake_cursor_sdk()
    os.environ["CURSOR_API_KEY"] = "test-key"

    tmp = _setup_temp_project()
    import cfg

    cfg.CFG.PROJECT_ROOT = tmp

    cfg_mod, dm, clc = _import_modules()
    cfg_mod.CFG.PROJECT_ROOT = tmp
    clc._AGENT_HANDLES.clear()
    clc._CURSOR_CLIENT = None
    clc._CURSOR_CLIENT_WORKSPACE = None

    role_cfg = {
        "source": "cursor",
        "model": "composer-2.5",
        "effort": "m",
        "max_tokens": 4096,
        "cursor_params": [],
    }

    with mock.patch("model_config.get_role_config", return_value=role_cfg), mock.patch(
        "discussion_mode.get_role_config",
        return_value=role_cfg,
    ), mock.patch("discussion_mode.load_decisions", return_value=[]):
        dm.reset_session({"source": "cursor", "model": "composer-2.5"})
        dm.discussion_send_message("first")
        first_agent_id = dm.load_session()["cursor_agent_id"]

        dm.discussion_update_settings(model="grok-4.5")
        second = dm.discussion_send_message("after model change")
        assert second["raw"]["discussion_cursor_mode"] == "bootstrap"
        assert dm.load_session()["cursor_agent_model"] == "grok-4.5"
        assert len(_FakeAgentModule.created) == 2
        assert _FakeAgentModule.created[-1][0].agent_id != first_agent_id


def test_context_change_prepends_update():
    _FakeAgentModule.reset()
    _install_fake_cursor_sdk()
    os.environ["CURSOR_API_KEY"] = "test-key"

    tmp = _setup_temp_project()
    import cfg

    cfg.CFG.PROJECT_ROOT = tmp

    cfg_mod, dm, clc = _import_modules()
    cfg_mod.CFG.PROJECT_ROOT = tmp
    clc._AGENT_HANDLES.clear()
    clc._CURSOR_CLIENT = None
    clc._CURSOR_CLIENT_WORKSPACE = None

    role_cfg = {
        "source": "cursor",
        "model": "composer-2.5",
        "effort": "m",
        "max_tokens": 4096,
        "cursor_params": [],
    }

    decisions = [
        {
            "index": 0,
            "date": "2026-01-01",
            "task": "task",
            "conflict": "scope",
        }
    ]
    file_state = {
        dm.PROJECT_REL: "# Project\n",
        dm.PLAN_REL: "# Plan\n",
    }

    def fake_safe_read(rel: str) -> str:
        return file_state.get(rel, "")

    with mock.patch("model_config.get_role_config", return_value=role_cfg), mock.patch(
        "discussion_mode.get_role_config",
        return_value=role_cfg,
    ), mock.patch("discussion_mode.load_decisions", return_value=decisions), mock.patch.object(
        dm, "_safe_read_rel", side_effect=fake_safe_read
    ):
        dm.reset_session({"source": "cursor", "model": "composer-2.5"})
        dm.discussion_send_message("hello", selected_indices=[0])
        agent = _FakeAgentModule.created[0][0]

        file_state[dm.PROJECT_REL] = "# Updated Project\n"

        follow = dm.discussion_send_message("after context change", selected_indices=[0])
        assert follow["raw"]["discussion_cursor_mode"] == "continue"
        payload = agent.sent[-1]
        assert payload.startswith("<context_update>")
        assert "# Updated Project" in payload
        assert "after context change" in payload


def test_bootstrap_failure_falls_back_to_one_shot():
    _FakeAgentModule.reset()
    _install_fake_cursor_sdk()
    os.environ["CURSOR_API_KEY"] = "test-key"

    tmp = _setup_temp_project()
    import cfg

    cfg.CFG.PROJECT_ROOT = tmp

    cfg_mod, dm, clc = _import_modules()
    cfg_mod.CFG.PROJECT_ROOT = tmp
    clc._AGENT_HANDLES.clear()
    clc._CURSOR_CLIENT = None
    clc._CURSOR_CLIENT_WORKSPACE = None

    role_cfg = {
        "source": "cursor",
        "model": "composer-2.5",
        "effort": "m",
        "max_tokens": 4096,
        "cursor_params": [],
    }

    original_bootstrap = clc.bootstrap_cursor_conversation
    original_call_llm_role = None

    def failing_bootstrap(*args, **kwargs):
        raise RuntimeError("bootstrap failed")

    import call_llm

    original_call_llm_role = call_llm.call_llm_role

    def stub_call_llm_role(*args, **kwargs):
        return {
            "content": "fallback reply",
            "source": "cursor",
            "model": "composer-2.5",
            "fallback_used": False,
        }

    with mock.patch("model_config.get_role_config", return_value=role_cfg), mock.patch(
        "discussion_mode.get_role_config",
        return_value=role_cfg,
    ), mock.patch("discussion_mode.load_decisions", return_value=[]), mock.patch.object(
        clc, "bootstrap_cursor_conversation", side_effect=failing_bootstrap
    ), mock.patch.object(call_llm, "call_llm_role", side_effect=stub_call_llm_role):
        dm.reset_session({"source": "cursor", "model": "composer-2.5"})
        result = dm.discussion_send_message("hello fallback")
        assert result["success"] is True
        assert result["raw"]["discussion_cursor_mode"] == "one_shot_fallback"
        assert result["assistant_message"] == "fallback reply"
        assert dm.load_session()["cursor_agent_id"] == ""


def _cursor_role_cfg():
    return {
        "source": "cursor",
        "model": "composer-2.5",
        "effort": "m",
        "max_tokens": 4096,
        "cursor_params": [],
    }


def test_continue_after_dropped_handles_resumes():
    _FakeAgentModule.reset()
    _install_fake_cursor_sdk()
    os.environ["CURSOR_API_KEY"] = "test-key"

    tmp = _setup_temp_project()
    import cfg

    cfg.CFG.PROJECT_ROOT = tmp
    cfg_mod, dm, clc = _import_modules()
    cfg_mod.CFG.PROJECT_ROOT = tmp
    clc._AGENT_HANDLES.clear()
    clc._CURSOR_CLIENT = None
    clc._CURSOR_CLIENT_WORKSPACE = None

    role_cfg = _cursor_role_cfg()
    with mock.patch("model_config.get_role_config", return_value=role_cfg), mock.patch(
        "discussion_mode.get_role_config",
        return_value=role_cfg,
    ), mock.patch("discussion_mode.load_decisions", return_value=[]):
        dm.reset_session({"source": "cursor", "model": "composer-2.5"})
        first = dm.discussion_send_message("hello there")
        agent_id = first["session"]["cursor_agent_id"]
        assert _FakeCursorClient.launches
        assert os.path.abspath(_FakeCursorClient.launches[0]) == os.path.abspath(tmp)

        clc._AGENT_HANDLES.clear()
        second = dm.discussion_send_message("follow after drop")
        assert second["success"] is True
        assert second["raw"]["discussion_cursor_mode"] == "continue"
        assert second["raw"]["discussion_cursor_mode"] != "one_shot_fallback"
        assert len(_FakeAgentModule.resumed) == 1
        assert _FakeAgentModule.resumed[0][1] == agent_id
        resumed_options = _FakeAgentModule.resumed[0][2]
        assert os.path.abspath(resumed_options.local.cwd) == os.path.abspath(tmp)
        assert resumed_options.tools == []
        assert _FakeAgentModule.prompt_calls == []
        assert dm.load_session()["cursor_agent_id"] == agent_id


def test_continue_send_failure_retries_resume_without_close():
    _FakeAgentModule.reset()
    _install_fake_cursor_sdk()
    os.environ["CURSOR_API_KEY"] = "test-key"

    tmp = _setup_temp_project()
    import cfg

    cfg.CFG.PROJECT_ROOT = tmp
    cfg_mod, dm, clc = _import_modules()
    cfg_mod.CFG.PROJECT_ROOT = tmp
    clc._AGENT_HANDLES.clear()
    clc._CURSOR_CLIENT = None
    clc._CURSOR_CLIENT_WORKSPACE = None

    role_cfg = _cursor_role_cfg()
    with mock.patch("model_config.get_role_config", return_value=role_cfg), mock.patch(
        "discussion_mode.get_role_config",
        return_value=role_cfg,
    ), mock.patch("discussion_mode.load_decisions", return_value=[]):
        dm.reset_session({"source": "cursor", "model": "composer-2.5"})
        first = dm.discussion_send_message("hello")
        agent = _FakeAgentModule.created[0][0]
        agent_id = first["session"]["cursor_agent_id"]
        agent.fail_next_send = True

        second = dm.discussion_send_message("retry please")
        assert second["success"] is True
        assert second["raw"]["discussion_cursor_mode"] == "continue"
        assert agent.closed is False
        assert agent_id in clc._AGENT_HANDLES
        assert len(_FakeAgentModule.resumed) == 1
        assert _FakeAgentModule.created[0][0].agent_id == agent_id


def test_planning_tool_call_executes_review_then_reinvokes():
    _FakeAgentModule.reset()
    _install_fake_cursor_sdk()
    os.environ["CURSOR_API_KEY"] = "test-key"

    tmp = _setup_temp_project()
    import cfg

    cfg.CFG.PROJECT_ROOT = tmp
    cfg_mod, dm, clc = _import_modules()
    cfg_mod.CFG.PROJECT_ROOT = tmp
    clc._AGENT_HANDLES.clear()
    clc._CURSOR_CLIENT = None
    clc._CURSOR_CLIENT_WORKSPACE = None

    _FakeAgentModule.replies = [
        '<tool_call>{"url": "/subagent", "payload": {"task": "inspect auth", "role": "review", "files": ["a.py"]}}</tool_call>',
        "Inspection complete.\n<plan># Next steps</plan>",
    ]
    captured_specs = []

    def fake_execute(calls):
        captured_specs.append(list(calls))
        return [
            {
                "success": True,
                "status": "done",
                "role": "review",
                "summary": "auth lives in a.py",
                "artifacts": [],
            }
        ]

    role_cfg = _cursor_role_cfg()
    with mock.patch("model_config.get_role_config", return_value=role_cfg), mock.patch(
        "discussion_mode.get_role_config",
        return_value=role_cfg,
    ), mock.patch("discussion_mode.load_decisions", return_value=[]), mock.patch(
        "discussion_mode.execute_planning_review_subagents",
        side_effect=fake_execute,
    ):
        dm.reset_session({"source": "cursor", "model": "composer-2.5"})
        result = dm.discussion_send_message("look at auth")
        assert result["success"] is True
        assert result["assistant_message"].startswith("Inspection complete")
        assert result["captured"]["has_plan"] is True
        assert len(captured_specs) == 1
        assert captured_specs[0][0]["payload"]["task"] == "inspect auth"
        agent = _FakeAgentModule.created[0][0]
        assert len(agent.sent) == 2
        assert "<subagent_result>" in agent.sent[1]
        assert "auth lives in a.py" in agent.sent[1]
        assert "<tool_call>" not in result["assistant_message"]
        session_messages = dm.load_session()["messages"]
        assert session_messages[-1]["role"] == "assistant"
        assert session_messages[-1]["content"].startswith("Inspection complete")
        assert len([m for m in session_messages if m["role"] == "user"]) == 1


def test_planning_chat_turn_skips_subagent():
    _FakeAgentModule.reset()
    _install_fake_cursor_sdk()
    os.environ["CURSOR_API_KEY"] = "test-key"

    tmp = _setup_temp_project()
    import cfg

    cfg.CFG.PROJECT_ROOT = tmp
    cfg_mod, dm, clc = _import_modules()
    cfg_mod.CFG.PROJECT_ROOT = tmp
    clc._AGENT_HANDLES.clear()
    clc._CURSOR_CLIENT = None
    clc._CURSOR_CLIENT_WORKSPACE = None

    _FakeAgentModule.replies = ["Let's clarify scope.\n<plan># Clarify</plan>"]
    role_cfg = _cursor_role_cfg()
    with mock.patch("model_config.get_role_config", return_value=role_cfg), mock.patch(
        "discussion_mode.get_role_config",
        return_value=role_cfg,
    ), mock.patch("discussion_mode.load_decisions", return_value=[]), mock.patch(
        "discussion_mode.execute_planning_review_subagents"
    ) as execute:
        dm.reset_session({"source": "cursor", "model": "composer-2.5"})
        result = dm.discussion_send_message("what should we do")
        assert result["success"] is True
        assert result["captured"]["has_plan"] is True
        execute.assert_not_called()
        assert len(_FakeAgentModule.created[0][0].sent) == 1


def test_invalid_tool_call_is_fed_back_not_raised():
    _FakeAgentModule.reset()
    _install_fake_cursor_sdk()
    os.environ["CURSOR_API_KEY"] = "test-key"

    tmp = _setup_temp_project()
    import cfg

    cfg.CFG.PROJECT_ROOT = tmp
    cfg_mod, dm, clc = _import_modules()
    cfg_mod.CFG.PROJECT_ROOT = tmp
    clc._AGENT_HANDLES.clear()
    clc._CURSOR_CLIENT = None
    clc._CURSOR_CLIENT_WORKSPACE = None

    _FakeAgentModule.replies = [
        "<tool_call>not-json</tool_call>",
        "Sorry, here is a normal reply.",
    ]
    role_cfg = _cursor_role_cfg()
    with mock.patch("model_config.get_role_config", return_value=role_cfg), mock.patch(
        "discussion_mode.get_role_config",
        return_value=role_cfg,
    ), mock.patch("discussion_mode.load_decisions", return_value=[]), mock.patch(
        "discussion_mode.execute_planning_review_subagents"
    ) as execute:
        dm.reset_session({"source": "cursor", "model": "composer-2.5"})
        result = dm.discussion_send_message("inspect please")
        assert result["success"] is True
        assert result["assistant_message"] == "Sorry, here is a normal reply."
        execute.assert_not_called()
        follow = _FakeAgentModule.created[0][0].sent[1]
        assert follow.startswith("<tool_error>")
        assert "not-json" in follow or "parse" in follow.lower() or "JSON" in follow or "json" in follow.lower()


def main() -> int:
    test_bootstrap_and_continue_delta_only()
    test_reset_clears_cursor_agent()
    test_model_change_rebootstraps()
    test_context_change_prepends_update()
    test_bootstrap_failure_falls_back_to_one_shot()
    test_continue_after_dropped_handles_resumes()
    test_continue_send_failure_retries_resume_without_close()
    test_planning_tool_call_executes_review_then_reinvokes()
    test_planning_chat_turn_skips_subagent()
    test_invalid_tool_call_is_fed_back_not_raised()
    print("DISCUSSION CURSOR CONTINUATION TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
