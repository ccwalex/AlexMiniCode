import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
MODULES = ROOT / "modules"
for path in (str(ROOT), str(MODULES)):
    if path not in sys.path:
        sys.path.insert(0, path)

import modules.job_restart as job_restart


class JobRestartTests(unittest.TestCase):
    def test_build_restart_task_preserves_attached_context(self):
        original = (
            "<user_request>\nFix parser.\n</user_request>\n\n"
            "<module_registry>\n<group name=\"core\" />\n</module_registry>\n\n"
            "<file_context>\n<file_1 path=\"a.py\">\n<content>\nprint('hi')\n</content>\n</file_1>\n</file_context>"
        )
        rebuilt = job_restart.build_restart_task(
            original,
            "Rewritten task with project context.",
        )
        self.assertIn("Rewritten task with project context.", rebuilt)
        self.assertIn("<module_registry>", rebuilt)
        self.assertIn("<file_context>", rebuilt)

    def test_build_restart_task_uses_final_task_builder_when_metadata_present(self):
        def fake_final_task(prompt, files, groups):
            return f"PROMPT={prompt}|FILES={','.join(files)}|GROUPS={','.join(groups)}"

        rebuilt = job_restart.build_restart_task(
            "<user_request>\nOld\n</user_request>",
            "Rewritten",
            selected_files=["a.py"],
            selected_registry_groups=["core"],
            final_task_builder=fake_final_task,
        )
        self.assertEqual(rebuilt, "PROMPT=Rewritten|FILES=a.py|GROUPS=core")

    def test_persist_and_load_rewritten_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            config_path = job_dir / "config.json"
            config_path.write_text(json.dumps({"task": "original"}), encoding="utf-8")

            self.assertTrue(
                job_restart.persist_rewritten_task(job_dir, "Rewritten from run")
            )
            self.assertTrue((job_dir / job_restart.REWRITTEN_TASK_FILENAME).exists())

            updated = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["rewritten_task"], "Rewritten from run")

            loaded = job_restart.load_rewritten_task(job_dir, updated)
            self.assertEqual(loaded, "Rewritten from run")

    def test_load_rewritten_task_falls_back_to_result_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            result = {
                "run_state": {
                    "rewritten_task": "From completed result",
                }
            }
            loaded = job_restart.load_rewritten_task(job_dir, {}, result)
            self.assertEqual(loaded, "From completed result")

    def test_build_restart_config_sets_skip_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            config = {
                "task": "<user_request>\nOriginal\n</user_request>",
                "selected_files": ["a.py"],
            }
            job_restart.persist_rewritten_task(job_dir, "Rewritten planner task")

            restarted = job_restart.build_restart_config(
                config,
                job_dir,
                final_task_builder=lambda prompt, files, groups: (
                    f"<user_request>\n{prompt}\n</user_request>\n<file>{files[0]}</file>"
                ),
            )

            self.assertTrue(restarted.get("skip_task_rewrite"))
            self.assertEqual(restarted.get("rewritten_task"), "Rewritten planner task")
            self.assertIn("Rewritten planner task", restarted.get("task", ""))
            self.assertIn("a.py", restarted.get("task", ""))

    def test_persist_rewritten_task_from_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"GEN2_JOB_DIR": tmp}):
                self.assertTrue(
                    job_restart.persist_rewritten_task_from_env("Saved from run_task_v2")
                )
            loaded = job_restart.load_rewritten_task(tmp)
            self.assertEqual(loaded, "Saved from run_task_v2")


if __name__ == "__main__":
    unittest.main()
