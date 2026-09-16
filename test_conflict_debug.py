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

import modules.execute_api_plan as plan_module
import modules.run_task_v2 as task_module


class ConflictDebugTests(unittest.TestCase):
    def test_failed_conflict_marks_plan_result_as_conflict(self):
        call = {"url": "/conflict", "payload": {"conflict": ""}}
        failed_result = {
            "success": False,
            "url": "/conflict",
            "payload": {"conflict": ""},
            "error": "Missing non-empty conflict in /conflict payload",
            "conflict": True,
            "output": {"success": False, "error": "Missing non-empty conflict in /conflict payload"},
        }
        with patch.object(plan_module, "execute_api_call", return_value=failed_result):
            result = plan_module.execute_api_plan([call])

        self.assertFalse(result["success"])
        self.assertTrue(result["conflict"])
        self.assertEqual(result["failed_call"], call)

    def test_run_task_skips_debug_after_conflict_failure(self):
        failed_call = {
            "url": "/conflict",
            "payload": {"conflict": "requirements conflict between A and B"},
        }
        failed_result = {
            "success": False,
            "url": "/conflict",
            "error": "conflict operation failed",
            "conflict": True,
            "output": {"success": False, "error": "disk full"},
        }

        with patch.object(
            task_module,
            "rewrite_task",
            return_value={"success": True, "rewritten_task": "task"},
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
                "conflict": True,
                "conflict_output": failed_result["output"],
                "error": "disk full",
            },
        ), patch.object(
            task_module, "execute_debug_v2"
        ) as debug, patch.object(
            task_module, "append_run"
        ):
            result = task_module.run_task_v2("task", max_iterations=3)

        self.assertFalse(result["success"])
        self.assertTrue(result["conflict"])
        debug.assert_not_called()


if __name__ == "__main__":
    unittest.main()
