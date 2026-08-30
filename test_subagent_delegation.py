import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
MODULES = ROOT / "modules"
for path in (str(ROOT), str(MODULES)):
    if path not in sys.path:
        sys.path.insert(0, path)

import modules.build_prompt_v2 as prompt_module
import modules.execute_api_call as api_module
import modules.run_task_v2 as task_module
import modules.subagent_runner as runner
from modules.model_config import get_role_config, role_override_scope
from modules.parse_api_plan import parse_api_plan
from modules.run_task_v2 import _subagent_feedback_from_execution_result


class SubagentDelegationTests(unittest.TestCase):
    def test_parser_accepts_normalized_final_subagent(self):
        parsed = parse_api_plan(
            [
                {
                    "url": "/subagent",
                    "payload": {
                        "task": "Inspect the parser",
                        "role": "review",
                        "mode": "readonly",
                        "files": ["agent/modules/parse_api_plan.py"],
                    },
                }
            ]
        )
        self.assertTrue(parsed["success"], parsed)
        payload = parsed["calls"][0]["payload"]
        self.assertEqual(payload["timeout_seconds"], 600)
        self.assertEqual(payload["files"], ["agent/modules/parse_api_plan.py"])

    def test_parser_rejects_nonfinal_or_mutating_readonly_subagent(self):
        nonfinal = parse_api_plan(
            [
                {"url": "/subagent", "payload": {"task": "inspect"}},
                {"url": "/done", "payload": {}},
            ]
        )
        self.assertFalse(nonfinal["success"])
        self.assertIn("final call", nonfinal["error"])

        mutating = parse_api_plan(
            [
                {
                    "url": "/subagent",
                    "payload": {
                        "task": "change a file",
                        "role": "implement",
                        "mode": "readonly",
                    },
                }
            ]
        )
        self.assertFalse(mutating["success"])
        self.assertIn("requires process mode", mutating["error"])

    def test_api_surfaces_child_failure_without_entering_debug_failure(self):
        child = {
            "success": False,
            "status": "failed",
            "role": "review",
            "mode": "process",
            "summary": "Could not complete review",
            "artifacts": ["agent/changed.py"],
            "error": "child task failed",
        }
        with patch.object(api_module, "run_subagent", return_value=child):
            read_cache = {"agent/changed.py": "stale", "agent/other.py": "keep"}
            result = api_module.execute_api_call(
                {
                    "url": "/subagent",
                    "payload": {"task": "Review this", "role": "review"},
                },
                read_cache=read_cache,
            )
        self.assertTrue(result["success"])
        self.assertTrue(result["request_feedback"])
        self.assertEqual(result["output"]["subagent_result"], child)
        self.assertNotIn("agent/changed.py", read_cache)
        self.assertNotIn("agent/other.py", read_cache)

    def test_parent_feedback_is_summary_only_and_bounded(self):
        execution = {
            "results": [
                {
                    "url": "/subagent",
                    "output": {
                        "subagent_result": {
                            "success": True,
                            "status": "completed",
                            "role": "explore",
                            "mode": "process",
                            "summary": "x" * 10000,
                            "artifacts": ["agent/a.py"],
                            "run_id": "subagent_1",
                            "run_state": {"secret": "must not cross"},
                            "read_cache": {"agent/a.py": "must not cross"},
                        }
                    },
                }
            ]
        }
        feedback = _subagent_feedback_from_execution_result(execution, max_chars=1200)
        self.assertIn("<subagent_result>", feedback)
        self.assertIn("[TRUNCATED]", feedback)
        self.assertNotIn("run_state", feedback)
        self.assertNotIn("read_cache", feedback)
        self.assertLessEqual(len(feedback), 1240)

    def test_readonly_mode_is_one_llm_call_with_no_worker(self):
        response = {"content": "Review result"}
        with patch.object(runner, "call_llm_role", return_value=response) as llm_call, patch.object(
            runner.subprocess, "Popen"
        ) as popen:
            result = runner.run_subagent(
                "Review behavior",
                role="review",
                mode="readonly",
                files=[],
            )
        self.assertTrue(result["success"], result)
        self.assertEqual(result["summary"], "Review result")
        llm_call.assert_called_once()
        popen.assert_not_called()

    def test_process_mode_launches_isolated_worker_and_filters_result(self):
        class FakeProcess:
            pid = 12345

            def __init__(self, command):
                self.command = command

            def wait(self, timeout=None):
                result_path = Path(self.command[self.command.index("--result") + 1])
                result_path.write_text(
                    json.dumps(
                        {
                            "success": True,
                            "status": "done",
                            "summary": "Implemented safely",
                            "read_cache": {"secret.py": "hidden"},
                            "run_state": {
                                "writes": [{"path": "agent/new.py", "success": True}],
                                "edits": [{"path": "agent/old.py", "success": True}],
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                return 0

        captured = {}

        def fake_popen(command, **kwargs):
            captured.update(kwargs)
            return FakeProcess(command)

        with patch.object(runner.subprocess, "Popen", side_effect=fake_popen):
            result = runner.run_subagent(
                "Implement one change",
                role="implement",
                mode="process",
                files=[],
            )
        self.assertTrue(result["success"], result)
        self.assertEqual(result["summary"], "Implemented safely")
        self.assertEqual(result["artifacts"], ["agent/new.py", "agent/old.py"])
        self.assertNotIn("run_state", result)
        self.assertNotIn("read_cache", result)
        self.assertEqual(captured["env"]["AGENT_SUBAGENT_DEPTH"], "1")
        self.assertTrue(captured["start_new_session"])

    def test_process_timeout_terminates_child_process_group(self):
        class HangingProcess:
            pid = 4321

            def __init__(self):
                self.waits = 0

            def wait(self, timeout=None):
                self.waits += 1
                if self.waits == 1:
                    raise runner.subprocess.TimeoutExpired("worker", timeout)
                return -15

        signals = []
        with patch.object(runner.subprocess, "Popen", return_value=HangingProcess()), patch.object(
            runner.os, "killpg", side_effect=lambda pid, sig: signals.append((pid, sig))
        ):
            result = runner.run_subagent(
                "Slow task",
                role="explore",
                mode="process",
                timeout_seconds=1,
            )
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "timed_out")
        self.assertEqual(signals, [(4321, runner.signal.SIGTERM)])

    def test_successful_process_requires_nonempty_done_summary(self):
        class EmptySummaryProcess:
            pid = 5432

            def __init__(self, command):
                self.command = command

            def wait(self, timeout=None):
                result_path = Path(self.command[self.command.index("--result") + 1])
                result_path.write_text(
                    json.dumps({"success": True, "status": "done", "summary": ""}),
                    encoding="utf-8",
                )
                return 0

        with patch.object(
            runner.subprocess,
            "Popen",
            side_effect=lambda command, **kwargs: EmptySummaryProcess(command),
        ):
            result = runner.run_subagent(
                "Task missing summary",
                role="implement",
                mode="process",
            )
        self.assertFalse(result["success"])
        self.assertIn("empty /done summary", result["error"])

    def test_nested_process_delegation_is_rejected_before_spawn(self):
        with patch.dict(os.environ, {"AGENT_SUBAGENT_DEPTH": "1"}), patch.object(
            runner.subprocess, "Popen"
        ) as popen:
            result = runner.run_subagent("Nested task", mode="process")
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "rejected")
        popen.assert_not_called()

        with patch.dict(os.environ, {"AGENT_SUBAGENT_DEPTH": "1"}), patch.object(
            runner, "call_llm_role"
        ) as llm_call:
            readonly = runner.run_subagent("Nested review", mode="readonly")
        self.assertFalse(readonly["success"])
        self.assertEqual(readonly["status"], "rejected")
        llm_call.assert_not_called()

    @unittest.skipUnless(hasattr(runner.signal, "setitimer"), "Unix timer required")
    def test_readonly_deadline_interrupts_hung_call(self):
        with self.assertRaises(TimeoutError):
            runner._call_with_timeout(lambda: time.sleep(1), 0.01)

    def test_subagent_roles_exist_and_child_prompt_hides_delegation(self):
        for role in ("subagent_explore", "subagent_review", "subagent_implement"):
            self.assertTrue(get_role_config(role)["model"])

        with patch.dict(os.environ, {"AGENT_SUBAGENT_DEPTH": "0"}):
            parent_prompt, _ = prompt_module.build_prompt_v2("task")
        with patch.dict(os.environ, {"AGENT_SUBAGENT_DEPTH": "1"}):
            child_prompt, _ = prompt_module.build_prompt_v2("task")
        self.assertIn("5. /subagent", parent_prompt)
        self.assertNotIn("5. /subagent", child_prompt)

    def test_per_job_role_overrides_apply_to_subagent_roles(self):
        with role_override_scope(
            {
                "subagent_explore": {
                    "source": "cursor",
                    "model": "job-explore",
                    "effort": "h",
                    "max_tokens": 1111,
                }
            }
        ):
            cfg = get_role_config("subagent_explore")
            planner = get_role_config("main_planner")
        self.assertEqual(cfg["model"], "job-explore")
        self.assertEqual(cfg["source"], "cursor")
        self.assertEqual(cfg["effort"], "h")
        self.assertEqual(cfg["max_tokens"], 1111)
        self.assertNotEqual(planner.get("model"), "job-explore")
        self.assertNotEqual(get_role_config("subagent_explore")["model"], "job-explore")

    def test_main_loop_replans_with_only_subagent_summary(self):
        prompts = []
        executions = {"count": 0}

        def fake_build(task, context="", scratchpad_content="", iteration=None, **kwargs):
            prompts.append(kwargs.get("execution_notes", ""))
            return "system", "user"

        def fake_execute(calls, run_state=None, read_cache=None, **kwargs):
            executions["count"] += 1
            if executions["count"] == 1:
                return {
                    "success": True,
                    "status": "request_feedback",
                    "run_state": run_state,
                    "read_cache": read_cache,
                    "results": [
                        {
                            "url": "/subagent",
                            "output": {
                                "subagent_result": {
                                    "success": True,
                                    "status": "completed",
                                    "role": "explore",
                                    "mode": "readonly",
                                    "summary": "Found the relevant call site",
                                    "artifacts": [],
                                    "run_state": {"hidden": True},
                                    "read_cache": {"hidden.py": "secret"},
                                }
                            },
                        }
                    ],
                    "done": False,
                    "error": None,
                }
            return {
                "success": True,
                "status": "done",
                "run_state": run_state,
                "read_cache": read_cache,
                "results": [{"url": "/done", "output": "finished"}],
                "done": True,
                "error": None,
            }

        with patch.object(
            task_module,
            "rewrite_task",
            return_value={"success": True, "rewritten_task": "task"},
        ), patch.object(task_module, "build_prompt_v2", side_effect=fake_build), patch.object(
            task_module, "_call_planner_llm", return_value=[]
        ), patch.object(
            task_module,
            "parse_api_plan",
            return_value={"success": True, "calls": [{"url": "/done", "payload": {}}]},
        ), patch.object(
            task_module, "execute_api_plan", side_effect=fake_execute
        ), patch.object(
            task_module, "append_run"
        ):
            result = task_module.run_task_v2(
                "task",
                max_iterations=3,
                max_feedback_loops=1,
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["summary"], "finished")
        self.assertEqual(len(prompts), 2)
        self.assertIn("Found the relevant call site", prompts[1])
        self.assertNotIn("read_cache", prompts[1])
        self.assertNotIn("run_state", prompts[1])


if __name__ == "__main__":
    unittest.main()
