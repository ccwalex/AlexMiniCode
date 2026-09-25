import json
import os
import sys
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
from modules.build_feedback_context import subagent_feedback_from_execution_result


class SubagentDelegationTests(unittest.TestCase):
    def test_parser_accepts_normalized_final_subagent(self):
        parsed = parse_api_plan(
            [
                {
                    "url": "/subagent",
                    "payload": {
                        "task": "Inspect the parser",
                        "role": "review",
                        "files": ["agent/modules/parse_api_plan.py"],
                    },
                }
            ]
        )
        self.assertTrue(parsed["success"], parsed)
        payload = parsed["calls"][0]["payload"]
        self.assertEqual(payload["timeout_seconds"], 1200)
        self.assertEqual(payload["role"], "review")
        self.assertEqual(payload["files"], ["agent/modules/parse_api_plan.py"])

    def test_parser_aliases_explore_to_review(self):
        parsed = parse_api_plan(
            [{"url": "/subagent", "payload": {"task": "inspect", "role": "explore"}}]
        )
        self.assertTrue(parsed["success"], parsed)
        self.assertEqual(parsed["calls"][0]["payload"]["role"], "review")

    def test_parser_rejects_nonfinal_or_legacy_mode(self):
        nonfinal = parse_api_plan(
            [
                {"url": "/subagent", "payload": {"task": "inspect"}},
                {"url": "/done", "payload": {}},
            ]
        )
        self.assertFalse(nonfinal["success"])
        self.assertIn("/subagent cannot be followed", nonfinal["error"])

        legacy_mode = parse_api_plan(
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
        self.assertFalse(legacy_mode["success"])
        self.assertIn("mode is no longer supported", legacy_mode["error"])

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
        with patch.object(api_module, "run_subagent", return_value=child), patch.object(
            api_module, "queue_dependency_cascade"
        ) as queue_cascade:
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
        self.assertEqual(read_cache["agent/changed.py"], "stale")
        queue_cascade.assert_not_called()

    def test_implement_subagent_queues_dependency_cascade_for_artifacts(self):
        child = {
            "success": True,
            "status": "completed",
            "role": "implement",
            "mode": "process",
            "summary": "Patched file",
            "artifacts": ["agent/changed.py"],
        }
        with patch.object(api_module, "run_subagent", return_value=child), patch.object(
            api_module, "read_file", return_value=(True, "new")
        ), patch.object(
            api_module, "refresh_after_file_change"
        ), patch.object(
            api_module, "queue_dependency_cascade"
        ) as queue_cascade:
            read_cache = {"agent/changed.py": "stale"}
            result = api_module.execute_api_call(
                {
                    "url": "/subagent",
                    "payload": {"task": "Implement fix", "role": "implement"},
                },
                read_cache=read_cache,
            )
        self.assertTrue(result["success"])
        queue_cascade.assert_called_once()

    def test_parent_feedback_is_summary_only_and_bounded(self):
        execution = {
            "results": [
                {
                    "url": "/subagent",
                    "output": {
                        "subagent_result": {
                            "success": True,
                            "status": "completed",
                            "role": "review",
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
        feedback = subagent_feedback_from_execution_result(execution, max_chars=1200)
        self.assertIn("<subagent_result>", feedback)
        self.assertIn("[TRUNCATED]", feedback)
        self.assertNotIn("run_state", feedback)
        self.assertNotIn("read_cache", feedback)
        self.assertLessEqual(len(feedback), 1240)

    def test_review_child_blocks_write_at_runtime(self):
        with patch.dict(os.environ, {"AGENT_SUBAGENT_DEPTH": "1", "AGENT_SUBAGENT_ROLE": "review"}):
            result = api_module.execute_api_call(
                {"url": "/write", "payload": {"path": "a.py", "content": "x"}},
            )
        self.assertFalse(result["success"])
        self.assertIn("not available to review subagents", result["error"])

    def test_process_mode_launches_isolated_worker_and_filters_result(self):
        class FakeProcess:
            pid = 12345

            def __init__(self, command):
                self.command = command
                self.task_text = ""

            def wait(self, timeout=None):
                config_path = Path(self.command[self.command.index("--config") + 1])
                config = json.loads(config_path.read_text(encoding="utf-8"))
                self.task_text = str(config.get("task") or "")
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
            process = FakeProcess(command)
            captured["process"] = process
            captured.update(kwargs)
            return process

        with patch.dict(os.environ, {"GEN2_JOB_DIR": "/tmp/parent-job"}), patch.object(
            runner.subprocess, "Popen", side_effect=fake_popen
        ):
            result = runner.run_subagent(
                "Implement one change",
                role="implement",
                files=[],
            )
        self.assertTrue(result["success"], result)
        self.assertEqual(result["summary"], "Implemented safely")
        self.assertEqual(result["artifacts"], ["agent/new.py", "agent/old.py"])
        self.assertNotIn("run_state", result)
        self.assertNotIn("read_cache", result)
        self.assertEqual(captured["env"]["AGENT_SUBAGENT_DEPTH"], "1")
        self.assertEqual(captured["env"]["AGENT_SUBAGENT_ROLE"], "implement")
        self.assertNotIn("GEN2_JOB_DIR", captured["env"])
        self.assertTrue(captured["start_new_session"])
        self.assertIn("You are an executor, not a planner", captured["process"].task_text)
        self.assertIn("ambiguous or incomplete", captured["process"].task_text)
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
                role="review",
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

        with patch.object(runner, "_max_subagent_repair_loops", return_value=1), patch.object(
            runner.subprocess,
            "Popen",
            side_effect=lambda command, **kwargs: EmptySummaryProcess(command),
        ):
            result = runner.run_subagent(
                "Task missing summary",
                role="implement",
            )
        self.assertFalse(result["success"])
        self.assertIn("empty /done summary", result["error"])

    def test_nested_process_delegation_is_rejected_before_spawn(self):
        with patch.dict(os.environ, {"AGENT_SUBAGENT_DEPTH": "1"}), patch.object(
            runner.subprocess, "Popen"
        ) as popen:
            result = runner.run_subagent("Nested task", role="review")
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "rejected")
        popen.assert_not_called()

    def test_peer_repair_retries_after_failure_then_succeeds(self):
        fail_result = {
            "success": False,
            "role": "review",
            "status": "failed",
            "summary": "Could not complete review",
            "artifacts": [],
            "error": "shell command failed",
        }
        success_result = {
            "success": True,
            "role": "review",
            "status": "done",
            "summary": "Found the call site",
            "artifacts": [],
        }
        calls = []

        def fake_run_process(task, role, files, timeout_seconds):
            calls.append({"task": task, "role": role})
            if len(calls) == 1:
                return fail_result
            return success_result

        with patch.object(runner, "_run_process", side_effect=fake_run_process):
            result = runner.run_subagent("Inspect parser", role="review")

        self.assertTrue(result["success"], result)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["role"], "review")
        self.assertEqual(calls[1]["role"], "review")
        self.assertEqual(calls[0]["task"], "Inspect parser")
        self.assertIn("<prior_subagent_failure>", calls[1]["task"])
        self.assertIn("shell command failed", calls[1]["task"])

    def test_peer_repair_stops_at_max_loops(self):
        fail_result = {
            "success": False,
            "role": "implement",
            "status": "failed",
            "summary": "still broken",
            "artifacts": [],
            "error": "write failed",
        }
        with patch.object(runner, "_max_subagent_repair_loops", return_value=10), patch.object(
            runner, "_run_process", return_value=fail_result
        ) as run_process:
            result = runner.run_subagent("Implement fix", role="implement")

        self.assertFalse(result["success"])
        self.assertEqual(run_process.call_count, 10)
        self.assertEqual(result["error"], "write failed")

    def test_peer_repair_does_not_retry_on_timeout(self):
        timeout_result = {
            "success": False,
            "role": "review",
            "status": "timed_out",
            "summary": "",
            "artifacts": [],
            "error": "subagent timed out after 1s",
        }
        with patch.object(runner, "_run_process", return_value=timeout_result) as run_process:
            result = runner.run_subagent("Slow task", role="review", timeout_seconds=1)

        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "timed_out")
        run_process.assert_called_once()

    def test_peer_repair_does_not_retry_on_rejected(self):
        with patch.object(runner, "_run_process") as run_process:
            result = runner.run_subagent("", role="review")
        self.assertEqual(result["status"], "rejected")
        run_process.assert_not_called()

    def test_subagent_roles_exist_and_child_prompt_hides_delegation(self):
        for role in ("subagent_review", "subagent_implement"):
            self.assertTrue(get_role_config(role)["model"])

        with patch.dict(os.environ, {"AGENT_SUBAGENT_DEPTH": "0"}):
            parent_prompt, _ = prompt_module.build_prompt_v2("task")
        with patch.dict(
            os.environ,
            {"AGENT_SUBAGENT_DEPTH": "1", "AGENT_SUBAGENT_ROLE": "review"},
        ):
            child_prompt, _ = prompt_module.build_prompt_v2("task")
        self.assertIn("5. /subagent", parent_prompt)
        self.assertNotIn("5. /subagent", child_prompt)

    def test_review_child_prompt_omits_blocked_endpoints(self):
        with patch.dict(
            os.environ,
            {"AGENT_SUBAGENT_DEPTH": "1", "AGENT_SUBAGENT_ROLE": "review"},
        ):
            review_prompt, _ = prompt_module.build_prompt_v2("task")
        endpoints = review_prompt.split("<endpoints>")[1].split("</endpoints>")[0]
        self.assertIn("1. /read", endpoints)
        self.assertNotIn(". /write", endpoints)
        self.assertNotIn(". /edit", endpoints)
        self.assertNotIn("/write_llm_memory", endpoints)
        self.assertNotIn("/conflict", endpoints)

    def test_implement_child_prompt_includes_write_edit(self):
        with patch.dict(
            os.environ,
            {"AGENT_SUBAGENT_DEPTH": "1", "AGENT_SUBAGENT_ROLE": "implement"},
        ):
            implement_prompt, _ = prompt_module.build_prompt_v2("task")
        self.assertIn("/write", implement_prompt)
        self.assertIn("/edit", implement_prompt)
        self.assertNotIn("5. /subagent", implement_prompt)

    def test_parent_prompt_describes_subagent_context_isolation(self):
        with patch.dict(os.environ, {"AGENT_SUBAGENT_DEPTH": "0"}):
            parent_prompt, _ = prompt_module.build_prompt_v2("task")
        self.assertIn("What each subagent receives", parent_prompt)
        self.assertIn("What subagents do NOT receive", parent_prompt)
        self.assertIn("review role", parent_prompt)
        self.assertIn("implement role", parent_prompt)
        self.assertIn("Subagents are context-isolated", parent_prompt)
        self.assertIn("Review /subagent is the default for inspection", parent_prompt)
        self.assertIn("Executor only", parent_prompt)
        self.assertIn("closed checklist", parent_prompt)
        self.assertIn("well-defined, low-reasoning write checklist", parent_prompt)
        self.assertNotIn("modify less than 3 files", parent_prompt)

    def test_implement_child_prompt_frames_executor_not_planner(self):
        with patch.dict(
            os.environ,
            {"AGENT_SUBAGENT_DEPTH": "1", "AGENT_SUBAGENT_ROLE": "implement"},
        ):
            implement_prompt, _ = prompt_module.build_prompt_v2("task")
        self.assertIn("executor, not a planner", implement_prompt)
        self.assertIn("ambiguous or incomplete", implement_prompt)
        self.assertNotIn("5. /subagent", implement_prompt)

    def test_per_job_role_overrides_apply_to_subagent_roles(self):
        with role_override_scope(
            {
                "subagent_review": {
                    "source": "cursor",
                    "model": "job-review",
                    "effort": "h",
                    "max_tokens": 1111,
                }
            }
        ):
            cfg = get_role_config("subagent_review")
            planner = get_role_config("main_planner")
        self.assertEqual(cfg["model"], "job-review")
        self.assertEqual(cfg["source"], "cursor")
        self.assertEqual(cfg["effort"], "h")
        self.assertEqual(cfg["max_tokens"], 1111)
        self.assertNotEqual(planner.get("model"), "job-review")
        self.assertNotEqual(get_role_config("subagent_review")["model"], "job-review")

    def test_subagent_skips_debug_and_returns_failure_feedback(self):
        failed_call = {"url": "/write", "payload": {"path": "a.py", "content": "x"}}
        failed_result = {
            "success": False,
            "url": "/write",
            "error": "write verifier rejected content",
            "output": None,
        }

        with patch.dict(os.environ, {"AGENT_SUBAGENT_DEPTH": "1", "AGENT_SUBAGENT_ROLE": "review"}), patch.object(
            task_module,
            "rewrite_task",
            return_value={"success": True, "rewritten_task": "delegated task"},
        ), patch.object(
            task_module, "build_prompt_v2", return_value=("system", "user")
        ), patch.object(
            task_module, "_call_planner_llm", return_value=[]
        ), patch.object(
            task_module,
            "parse_api_plan",
            return_value={"success": True, "calls": [failed_call]},
        ), patch.object(
            task_module,
            "execute_api_plan",
            return_value={
                "success": False,
                "status": "failed",
                "run_state": None,
                "read_cache": {},
                "results": [failed_result],
                "failed_call": failed_call,
                "failed_result": failed_result,
                "error": "write verifier rejected content",
            },
        ), patch.object(
            task_module, "execute_debug_v2"
        ) as debug, patch.object(
            task_module, "append_run"
        ):
            result = task_module.run_task_v2(
                "delegated task",
                max_iterations=3,
                max_retries=2,
            )

        self.assertFalse(result["success"], result)
        self.assertEqual(result["status"], "failed")
        self.assertIn("write verifier rejected content", result["reason"])
        self.assertIn("write verifier rejected content", result["summary"])
        debug.assert_not_called()

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
                                    "role": "review",
                                    "mode": "process",
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

    def test_parser_accepts_trailing_review_batch(self):
        parsed = parse_api_plan(
            [
                {"url": "/subagent", "payload": {"task": "one", "role": "review"}},
                {"url": "/subagent", "payload": {"task": "two", "role": "review"}},
            ]
        )
        self.assertTrue(parsed["success"], parsed)
        self.assertEqual(len(parsed["calls"]), 2)

    def test_parser_rejects_noncontiguous_or_oversized_subagent_batch(self):
        split = parse_api_plan(
            [
                {"url": "/subagent", "payload": {"task": "one"}},
                {"url": "/read", "payload": {"path": "a.py"}},
                {"url": "/subagent", "payload": {"task": "two"}},
            ]
        )
        self.assertFalse(split["success"])
        too_many = parse_api_plan(
            [{"url": "/subagent", "payload": {"task": f"t{i}"}} for i in range(9)]
        )
        self.assertFalse(too_many["success"])
        self.assertIn("at most", too_many["error"])

    def test_parallel_review_subagents_do_not_reenter_same_context(self):
        specs = [
            {"task": f"task-{index}", "timeout_seconds": 5}
            for index in range(4)
        ]
        with patch.object(
            runner,
            "run_subagent",
            side_effect=lambda **kwargs: {
                "success": True,
                "status": "completed",
                "role": "review",
                "mode": "process",
                "summary": kwargs.get("task", ""),
                "artifacts": [],
            },
        ) as run_subagent:
            results = runner.run_review_subagents_parallel(specs)
        self.assertEqual(len(results), 4)
        self.assertTrue(all(item.get("success") for item in results))
        self.assertEqual(run_subagent.call_count, 4)
        self.assertNotIn(
            "already entered",
            " ".join(str(item.get("error") or "") for item in results),
        )

    def test_execute_plan_runs_review_batch_in_parallel_helper(self):
        import modules.execute_api_plan as plan_module

        calls = [
            {"url": "/subagent", "payload": {"task": "one", "role": "review"}},
            {"url": "/subagent", "payload": {"task": "two", "role": "review"}},
        ]
        child = {
            "success": True,
            "status": "completed",
            "role": "review",
            "mode": "process",
            "summary": "ok",
            "artifacts": [],
        }
        with patch.object(
            plan_module, "run_review_subagents_parallel", return_value=[child, dict(child)]
        ) as parallel, patch.object(plan_module, "execute_api_call") as sequential:
            result = plan_module.execute_api_plan(calls)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["status"], "request_feedback")
        self.assertEqual(len(result["results"]), 2)
        parallel.assert_called_once()
        sequential.assert_not_called()

    def test_execute_plan_runs_mixed_batch_review_parallel_then_implement(self):
        import modules.execute_api_plan as plan_module

        calls = [
            {"url": "/subagent", "payload": {"task": "impl", "role": "implement"}},
            {"url": "/subagent", "payload": {"task": "rev", "role": "review"}},
        ]
        seen = []

        def fake_execute(call, **kwargs):
            seen.append(call["payload"]["task"])
            return {
                "success": True,
                "url": "/subagent",
                "payload": call["payload"],
                "output": {"subagent_result": {"success": True, "summary": call["payload"]["task"]}},
                "request_feedback": True,
                "done": False,
                "conflict": False,
                "error": None,
            }

        with patch.object(
            plan_module,
            "run_review_subagents_parallel",
            return_value=[{"success": True, "summary": "rev"}],
        ) as parallel, patch.object(
            plan_module, "execute_api_call", side_effect=fake_execute
        ) as sequential:
            result = plan_module.execute_api_plan(calls)
        self.assertTrue(result["success"], result)
        parallel.assert_called_once()
        sequential.assert_called_once()
        self.assertEqual(seen, ["impl"])
        self.assertEqual(len(result["results"]), 2)

    def test_execute_plan_continues_from_read_into_trailing_subagent(self):
        import modules.execute_api_plan as plan_module

        calls = [
            {"url": "/read", "payload": {"path": "a.py"}},
            {"url": "/subagent", "payload": {"task": "review a.py", "role": "review"}},
        ]
        seen = []

        def fake_execute(call, **kwargs):
            seen.append(call["url"])
            return {
                "success": True,
                "url": "/read",
                "payload": call["payload"],
                "output": {"content": "abc"},
                "error": None,
                "done": False,
                "conflict": False,
                "request_feedback": True,
            }

        with patch.object(plan_module, "execute_api_call", side_effect=fake_execute), patch.object(
            plan_module,
            "run_review_subagents_parallel",
            return_value=[{"success": True, "summary": "reviewed"}],
        ) as parallel:
            result = plan_module.execute_api_plan(calls)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["status"], "request_feedback")
        self.assertEqual(seen, ["/read"])
        self.assertEqual([item["url"] for item in result["results"]], ["/read", "/subagent"])
        parallel.assert_called_once()
        feedback = subagent_feedback_from_execution_result(result)
        self.assertIn("<subagent_result>", feedback)
        self.assertIn("reviewed", feedback)

    def test_execute_plan_continues_through_write_when_subagent_remains(self):
        import modules.execute_api_plan as plan_module

        calls = [
            {"url": "/read", "payload": {"path": "a.py"}},
            {"url": "/write", "payload": {"path": "b.py", "content": "x"}},
            {"url": "/subagent", "payload": {"task": "review", "role": "review"}},
        ]
        seen = []

        def fake_execute(call, **kwargs):
            seen.append(call["url"])
            return {
                "success": True,
                "url": call["url"],
                "payload": call["payload"],
                "output": {"ok": True},
                "error": None,
                "done": False,
                "conflict": False,
                "request_feedback": call["url"] == "/read",
            }

        with patch.object(plan_module, "execute_api_call", side_effect=fake_execute), patch.object(
            plan_module,
            "run_review_subagents_parallel",
            return_value=[{"success": True, "summary": "ok"}],
        ):
            result = plan_module.execute_api_plan(calls)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["status"], "request_feedback")
        self.assertEqual(seen, ["/read", "/write"])


if __name__ == "__main__":
    unittest.main()
