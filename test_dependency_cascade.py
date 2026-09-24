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

from modules.extract_public_contract import classify_io_delta, extract_public_contract, output_format_changed
from modules.find_module_dependents import find_module_dependents, is_denied_dependency_path
from modules.propagate_module_io_change import (
    flush_dependency_cascades,
    propagate_module_io_change,
    queue_dependency_cascade,
)
from modules.run_state import RunState


class DependencyCascadeTests(unittest.TestCase):
    def test_python_ast_return_change_not_metadata(self):
        before = extract_public_contract(
            "mod.py",
            "def foo(x: int) -> dict:\n    return {}\n",
            "py",
        )
        metadata_only = extract_public_contract(
            "mod.py",
            'def foo(x: int) -> dict:\n    return {"a": 1}\n',
            "py",
        )
        changed_ret = extract_public_contract(
            "mod.py",
            "def foo(x: int) -> list:\n    return []\n",
            "py",
        )
        unchanged, _, needs_llm = output_format_changed(before, metadata_only)
        self.assertFalse(unchanged)
        self.assertFalse(needs_llm)
        changed, _, needs_llm = output_format_changed(before, changed_ret)
        self.assertTrue(changed)
        self.assertFalse(needs_llm)

    def test_helper_change_ignored_when_primary_export_unchanged(self):
        before = extract_public_contract(
            "mod.py",
            '__all__ = ["foo"]\n'
            "def helper() -> dict:\n    return {}\n"
            "def foo(x: int) -> dict:\n    return {}\n",
            "py",
        )
        after = extract_public_contract(
            "mod.py",
            '__all__ = ["foo"]\n'
            "def helper() -> list:\n    return []\n"
            "def foo(x: int) -> dict:\n    return {}\n",
            "py",
        )
        changed, _, needs_llm = output_format_changed(before, after)
        self.assertFalse(changed)
        self.assertFalse(needs_llm)

    def test_primary_change_in_multi_export_module_needs_llm(self):
        before = extract_public_contract(
            "mod.py",
            '__all__ = ["foo"]\n'
            "def helper() -> dict:\n    return {}\n"
            "def foo(x: int) -> dict:\n    return {}\n",
            "py",
        )
        after = extract_public_contract(
            "mod.py",
            '__all__ = ["foo"]\n'
            "def helper() -> dict:\n    return {}\n"
            "def foo(x: int) -> list:\n    return []\n",
            "py",
        )
        changed, _, needs_llm = output_format_changed(before, after)
        self.assertTrue(changed)
        self.assertTrue(needs_llm)

    def test_multi_export_without_metadata_is_ambiguous(self):
        before = extract_public_contract(
            "mod.py",
            "def helper() -> dict:\n    return {}\n"
            "def foo(x: int) -> dict:\n    return {}\n",
            "py",
        )
        after = extract_public_contract(
            "mod.py",
            "def helper() -> dict:\n    return {}\n"
            "def foo(x: int) -> list:\n    return []\n",
            "py",
        )
        changed, reason, needs_llm = output_format_changed(before, after)
        self.assertFalse(changed)
        self.assertTrue(needs_llm)
        self.assertIn("ambiguous", reason)

    def test_grep_searches_project_and_skips_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "agent").mkdir()
            (root / "app").mkdir()
            (root / "agent" / "hidden.py").write_text("from foo import bar\n")
            (root / "app" / "use.py").write_text("from foo import bar\n")
            class Cfg:
                PROJECT_ROOT = str(root)
            with patch("modules.find_module_dependents.CFG", Cfg):
                deps = find_module_dependents("lib/foo.py")
        self.assertIn("app/use.py", deps)
        self.assertTrue(all(not item.startswith("agent/") for item in deps))

    def test_untracked_skips_without_grep(self):
        with patch("modules.propagate_module_io_change.is_tracked", return_value=False), patch(
            "modules.propagate_module_io_change.find_module_dependents"
        ) as grep:
            result = propagate_module_io_change("elsewhere/mod.py", "def foo() -> int:\n    return 1\n", "def foo() -> str:\n    return 'x'\n")
        self.assertFalse(result["tracked"])
        grep.assert_not_called()

    def test_unchanged_outputs_do_not_grep(self):
        src = "def foo(x: int) -> dict:\n    return {}\n"
        with patch("modules.propagate_module_io_change.is_tracked", return_value=True), patch(
            "modules.propagate_module_io_change.find_module_dependents"
        ) as grep, patch("modules.propagate_module_io_change.limited_update_dependent") as limited:
            result = propagate_module_io_change("pkg/mod.py", src, src)
        self.assertFalse(result["changed"])
        self.assertFalse(result["grepped"])
        grep.assert_not_called()
        limited.assert_not_called()

    def test_helper_only_change_does_not_cascade(self):
        before = (
            '__all__ = ["foo"]\n'
            "def helper() -> dict:\n    return {}\n"
            "def foo(x: int) -> dict:\n    return {}\n"
        )
        after = (
            '__all__ = ["foo"]\n'
            "def helper() -> list:\n    return []\n"
            "def foo(x: int) -> dict:\n    return {}\n"
        )
        with patch("modules.propagate_module_io_change.is_tracked", return_value=True), patch(
            "modules.propagate_module_io_change.find_module_dependents"
        ) as grep, patch("modules.propagate_module_io_change.limited_update_dependent") as limited:
            result = propagate_module_io_change("pkg/mod.py", before, after)
        self.assertFalse(result["changed"])
        self.assertFalse(result["grepped"])
        grep.assert_not_called()
        limited.assert_not_called()

    def test_classify_io_delta_skips_optional_param(self):
        before = extract_public_contract(
            "mod.py",
            "def foo(x: int) -> dict:\n    return {}\n",
            "py",
        )
        after = extract_public_contract(
            "mod.py",
            "def foo(x: int, y: int = 1) -> dict:\n    return {}\n",
            "py",
        )
        delta = classify_io_delta(before, after)
        self.assertEqual(delta["action"], "skip")

    def test_classify_io_delta_flags_required_param(self):
        before = extract_public_contract(
            "mod.py",
            "def foo(x: int) -> dict:\n    return {}\n",
            "py",
        )
        after = extract_public_contract(
            "mod.py",
            "def foo(x: int, y: int) -> dict:\n    return {}\n",
            "py",
        )
        delta = classify_io_delta(before, after)
        self.assertEqual(delta["action"], "update_callers")
        self.assertIn("y", delta["hints"]["added_required_params"])

    def test_changed_outputs_grep_and_limited_edit(self):
        before = "def foo(x: int) -> dict:\n    return {}\n"
        after = "def foo(x: int) -> list:\n    return []\n"
        with patch("modules.propagate_module_io_change.is_tracked", return_value=True), patch(
            "modules.propagate_module_io_change.find_module_dependents",
            return_value=["app/use_foo.py"],
        ) as grep, patch(
            "modules.propagate_module_io_change.limited_update_dependent",
            return_value={"success": True, "escalate": False, "reason": "updated"},
        ) as limited, patch(
            "modules.propagate_module_io_change.refresh_after_file_change"
        ), patch(
            "modules.propagate_module_io_change._read",
            return_value="from mod import foo\n\ndef use():\n    return foo(1)\n",
        ):
            result = propagate_module_io_change("pkg/mod.py", before, after)
        self.assertTrue(result["changed"])
        self.assertTrue(result["grepped"])
        grep.assert_called_once_with("pkg/mod.py")
        limited.assert_called_once()

    def test_unparseable_skips_without_review_or_grep(self):
        with patch("modules.propagate_module_io_change.is_tracked", return_value=True), patch(
            "modules.propagate_module_io_change.find_module_dependents"
        ) as grep, patch(
            "modules.propagate_module_io_change.limited_update_dependent"
        ) as limited:
            result = propagate_module_io_change("ui/App.tsx", "not valid {", "still not valid {")
        self.assertFalse(result["changed"])
        self.assertEqual(result["delta_action"], "skip")
        grep.assert_not_called()
        limited.assert_not_called()

    def test_recursion_cap(self):
        before = "def foo() -> dict:\n    return {}\n"
        after = "def foo() -> list:\n    return []\n"
        with patch("modules.propagate_module_io_change.is_tracked", return_value=True), patch(
            "modules.propagate_module_io_change.find_module_dependents",
            return_value=["a.py"],
        ), patch("modules.propagate_module_io_change.limited_update_dependent") as limited, patch(
            "modules.propagate_module_io_change.MAX_DEPTH", 0
        ):
            result = propagate_module_io_change("pkg/mod.py", before, after, depth=1)
        self.assertEqual(result["reason"], "max cascade depth")
        limited.assert_not_called()

    def test_queue_dedupes_same_path_and_flush_runs_once(self):
        run_state = RunState(task="demo")
        queue_dependency_cascade(run_state, "pkg/mod.py", "before")
        queue_dependency_cascade(run_state, "pkg/mod.py", "ignored-later-pre")
        self.assertEqual(len(run_state.pending_dependency_cascades), 1)

        with patch(
            "modules.propagate_module_io_change.propagate_module_io_change",
            return_value={"changed": False, "reason": "no change"},
        ) as propagate:
            cascades = flush_dependency_cascades(
                run_state,
                read_cache={"pkg/mod.py": "after"},
            )
        propagate.assert_called_once()
        call = propagate.call_args
        self.assertEqual(call.args[0], "pkg/mod.py")
        self.assertEqual(call.kwargs.get("pre_content"), "before")
        self.assertEqual(call.kwargs.get("post_content"), "after")
        self.assertEqual(call.kwargs.get("run_state"), run_state)
        self.assertEqual(cascades, [{"changed": False, "reason": "no change"}])
        self.assertEqual(run_state.pending_dependency_cascades, {})

    def test_ast_only_path_does_not_emit_dependency_progress(self):
        src = "def foo(x: int) -> dict:\n    return {}\n"
        with patch("modules.propagate_module_io_change.is_tracked", return_value=True), patch(
            "modules.propagate_module_io_change.find_module_dependents"
        ), patch("modules.propagate_module_io_change.limited_update_dependent"), patch(
            "modules.propagate_module_io_change._emit_dependency_substep"
        ) as emit_sub, patch("modules.propagate_module_io_change._maybe_start_parent_dependency") as emit_parent:
            result = propagate_module_io_change(
                "pkg/mod.py",
                src,
                src,
                batch_id="batch-1",
                progress_state={"parent_started": False, "llm_started": False},
            )
        self.assertFalse(result["changed"])
        self.assertFalse(result.get("llm_used"))
        emit_sub.assert_not_called()
        emit_parent.assert_not_called()

    def test_denied_paths_skip_cascade(self):
        self.assertTrue(is_denied_dependency_path("agent/hidden.py"))
        self.assertTrue(is_denied_dependency_path("pkg/.ipynb_checkpoints/x.py"))
        with patch("modules.propagate_module_io_change.is_tracked", return_value=True), patch(
            "modules.propagate_module_io_change.find_module_dependents"
        ) as grep:
            result = propagate_module_io_change(
                "agent/mod.py",
                "def foo() -> dict:\n    return {}\n",
                "def foo() -> list:\n    return []\n",
            )
        self.assertEqual(result["reason"], "denied dependency path")
        grep.assert_not_called()

    def test_implement_path_emits_dependency_progress(self):
        before = "def foo(x: int) -> dict:\n    return {}\n"
        after = "def foo(x: int) -> list:\n    return []\n"
        with patch("modules.propagate_module_io_change.is_tracked", return_value=True), patch(
            "modules.propagate_module_io_change.find_module_dependents",
            return_value=["app/use_foo.py"],
        ), patch(
            "modules.propagate_module_io_change.limited_update_dependent",
            return_value={"success": True, "escalate": False, "reason": "updated"},
        ), patch("modules.propagate_module_io_change.refresh_after_file_change"), patch(
            "modules.propagate_module_io_change._read",
            return_value="from mod import foo\n\ndef use():\n    return foo(1)\n",
        ), patch("modules.propagate_module_io_change._emit_dependency_substep") as emit_sub, patch(
            "modules.propagate_module_io_change._maybe_start_parent_dependency"
        ) as emit_parent:
            result = propagate_module_io_change(
                "pkg/mod.py",
                before,
                after,
                batch_id="batch-1",
                progress_state={"parent_started": False, "llm_started": False},
            )
        self.assertTrue(result["changed"])
        self.assertTrue(result.get("llm_used"))
        self.assertEqual(result.get("implement_calls"), 1)
        implement_calls = [
            call for call in emit_sub.call_args_list
            if len(call.args) > 3 and call.args[3] == "dependency_implement"
        ]
        self.assertEqual(len(implement_calls), 2)
        self.assertEqual(implement_calls[0].args[2], "running")
        self.assertEqual(implement_calls[1].args[2], "done")
        self.assertTrue(
            any(
                c.args[0] == "batch-1" and c.args[1] == "pkg/mod.py"
                for c in emit_parent.call_args_list
            )
        )

    def test_flush_emits_parent_only_when_llm_used(self):
        run_state = RunState(task="demo")
        queue_dependency_cascade(run_state, "pkg/mod.py", "before")
        with patch(
            "modules.propagate_module_io_change.propagate_module_io_change",
            return_value={"changed": False, "reason": "no change", "llm_used": False},
        ), patch("job_progress.emit_turn_dependency") as emit_parent:
            flush_dependency_cascades(
                run_state,
                read_cache={"pkg/mod.py": "after"},
                batch_id="batch-1",
            )
        emit_parent.assert_not_called()

        run_state.pending_dependency_cascades = {
            "pkg/mod.py": {"path": "pkg/mod.py", "pre_content": "before"},
        }
        with patch(
            "modules.propagate_module_io_change.propagate_module_io_change",
            return_value={
                "changed": True,
                "llm_used": True,
                "review_called": 0,
                "implement_calls": 1,
                "dependents": ["app/use_foo.py"],
                "updates": [{"path": "app/use_foo.py", "success": True}],
            },
        ), patch("job_progress.emit_turn_dependency") as emit_parent:
            flush_dependency_cascades(
                run_state,
                read_cache={"pkg/mod.py": "after"},
                batch_id="batch-1",
            )
        self.assertEqual(
            [call.args[1] for call in emit_parent.call_args_list],
            ["running", "done"],
        )

    def test_execute_api_plan_escalation_requests_feedback(self):
        import modules.execute_api_plan as plan_module

        run_state = RunState(task="demo")
        run_state.pending_dependency_cascades = {
            "pkg/mod.py": {"path": "pkg/mod.py", "pre_content": "before"},
        }
        with patch(
            "modules.execute_api_plan.flush_dependency_cascades",
            return_value=[
                {
                    "changed": True,
                    "escalations": [
                        {
                            "path": "app/use_foo.py",
                            "changed_path": "pkg/mod.py",
                            "reason": "limited block edit insufficient",
                            "hints": {"added_required_params": ["y"]},
                        }
                    ],
                }
            ],
        ):
            result = plan_module._finalize_plan_return(
                run_state,
                {"pkg/mod.py": "after"},
                "batch-1",
                {
                    "success": True,
                    "status": "done",
                    "run_state": run_state,
                    "read_cache": {"pkg/mod.py": "after"},
                    "results": [],
                    "failed_call": None,
                    "failed_result": None,
                    "feedback": None,
                    "done": True,
                    "conflict": False,
                    "error": None,
                },
            )
        self.assertEqual(result["status"], "request_feedback")
        self.assertTrue(result.get("request_feedback"))
        self.assertIn("dependency_escalate", result["feedback"])

    def test_execute_api_plan_flushes_dependency_cascade_at_end_of_turn(self):
        import modules.execute_api_plan as plan_module

        run_state = RunState(task="demo")
        run_state.pending_dependency_cascades = {
            "pkg/mod.py": {"path": "pkg/mod.py", "pre_content": "before"},
        }
        with patch(
            "modules.execute_api_plan.flush_dependency_cascades",
            return_value=[{"changed": False}],
        ) as flush:
            result = plan_module._finalize_plan_return(
                run_state,
                {"pkg/mod.py": "after"},
                "batch-1",
                {
                    "success": True,
                    "status": "request_feedback",
                    "run_state": run_state,
                    "read_cache": {"pkg/mod.py": "after"},
                    "results": [],
                    "failed_call": None,
                    "failed_result": None,
                    "feedback": None,
                    "done": False,
                    "conflict": False,
                    "error": None,
                },
            )
        flush.assert_called_once_with(
            run_state,
            read_cache={"pkg/mod.py": "after"},
            batch_id="batch-1",
        )
        self.assertEqual(result["dependency_cascades"], [{"changed": False}])


if __name__ == "__main__":
    unittest.main()
