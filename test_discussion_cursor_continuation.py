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

    def send(self, message: str):
        self.sent.append(str(message))
        return _FakeRun(f"assistant-reply-{len(self.sent)}")

    def close(self):
        self.closed = True


class _FakeAgentModule:
    created: list[tuple[_FakeAgent, dict]] = []
    resumed: list[tuple[_FakeAgent, str, object]] = []
    prompt_calls: list[tuple[str, object]] = []
    handles: dict[str, _FakeAgent] = {}

    @classmethod
    def reset(cls):
        cls.created = []
        cls.resumed = []
        cls.prompt_calls = []
        cls.handles = {}
        _FakeAgent._counter = 0

    @staticmethod
    def create(**kwargs):
        agent = _FakeAgent()
        _FakeAgentModule.created.append((agent, kwargs))
        _FakeAgentModule.handles[agent.agent_id] = agent
        return agent

    @staticmethod
    def resume(agent_id, options):
        existing = _FakeAgentModule.handles.get(agent_id)
        if existing is not None:
            agent = existing
        else:
            agent = _FakeAgent(agent_id=agent_id)
            _FakeAgentModule.handles[agent_id] = agent
        _FakeAgentModule.resumed.append((agent, agent_id, options))
        return agent

    @staticmethod
    def prompt(prompt, options):
        _FakeAgentModule.prompt_calls.append((prompt, options))
        return types.SimpleNamespace(status="finished", result=f"prompt:{prompt[:40]}")


def _install_fake_cursor_sdk():
    fake = types.ModuleType("cursor_sdk")
    fake.Agent = _FakeAgentModule
    fake.AgentOptions = lambda **kwargs: types.SimpleNamespace(**kwargs)
    fake.LocalAgentOptions = lambda **kwargs: types.SimpleNamespace(**kwargs)
    fake.ModelParameterValue = lambda **kwargs: types.SimpleNamespace(**kwargs)
    fake.ModelSelection = lambda **kwargs: types.SimpleNamespace(**kwargs)
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


def main() -> int:
    test_bootstrap_and_continue_delta_only()
    test_reset_clears_cursor_agent()
    test_model_change_rebootstraps()
    test_context_change_prepends_update()
    test_bootstrap_failure_falls_back_to_one_shot()
    print("DISCUSSION CURSOR CONTINUATION TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
