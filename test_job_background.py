import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gen2_web_gui_tracked as gui


class _Proc:
    def __init__(self, pid):
        self.pid = pid


class JobBackgroundTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.memory = Path(self.tmp.name)
        self.alive_pids = set()
        self.term_calls = []
        self.patches = [
            patch.object(gui, "memory_dir", return_value=self.memory),
            patch.object(gui, "project_root", return_value=ROOT),
            patch.object(gui, "worker_script", return_value=Path(__file__)),
            patch.object(gui, "terminate_process_group", side_effect=self._term),
            patch.object(gui, "alive", side_effect=self._alive),
        ]
        for p in self.patches:
            p.start()
        gui.ensure_storage()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _alive(self, pid):
        try:
            return int(pid) in self.alive_pids
        except Exception:
            return False

    def _term(self, pid, grace_seconds=2.0):
        self.term_calls.append(pid)
        try:
            self.alive_pids.discard(int(pid))
        except Exception:
            pass
        return True, "terminated"

    def _seed_job(self, jid, status="running"):
        d = gui.job_dir(jid)
        d.mkdir(parents=True, exist_ok=True)
        gui.write_json(
            d / "status.json",
            {"job_id": jid, "status": status, "success": None},
        )
        gui.write_json(
            d / "config.json",
            {"task": "do it", "original_prompt": "do it"},
        )
        return d

    def _set_current(self, jid, pid):
        self._seed_job(jid)
        self.alive_pids.add(int(pid))
        gui.save_current({"job_id": jid, "pid": pid, "started_at": "t"})

    def test_background_demotes_without_killing(self):
        self._set_current("job_a", 111)
        result = gui.background_current_job()
        self.assertTrue(result["backgrounded"])
        self.assertEqual(result["old_job_id"], "job_a")
        self.assertEqual(self.term_calls, [])
        self.assertEqual(gui.load_current(), {})
        bg = gui.load_background()
        self.assertEqual(len(bg), 1)
        self.assertEqual(bg[0]["job_id"], "job_a")
        self.assertEqual(bg[0]["pid"], 111)
        st = gui.read_json(gui.job_dir("job_a") / "status.json")
        self.assertEqual(st["status"], "running")

    def test_stack_backgrounds_then_start_next(self):
        self._set_current("job_a", 111)
        gui.background_current_job()
        self._set_current("job_b", 222)
        gui.background_current_job()
        queued = gui.create_job({"task": "next", "original_prompt": "next"})
        with patch.object(gui.subprocess, "Popen", return_value=_Proc(333)) as popen:
            started = gui.start_next()
        self.assertTrue(started["started"])
        popen.assert_called_once()
        self.alive_pids.add(333)
        self.assertEqual(gui.load_current().get("job_id"), queued)
        self.assertEqual([e["job_id"] for e in gui.load_background()], ["job_a", "job_b"])
        self.assertEqual(gui.running_count(), 3)

    def test_reap_one_background_keeps_others(self):
        self._seed_job("job_a")
        self._seed_job("job_b")
        self._set_current("job_c", 333)
        gui.save_background(
            [
                {"job_id": "job_a", "pid": 111},
                {"job_id": "job_b", "pid": 222},
            ]
        )
        self.alive_pids.update({222, 333})
        gui.write_json(
            gui.job_dir("job_a") / "result.json",
            {"success": True, "reason": "worker finished"},
        )
        gui.refresh_running()
        self.assertEqual(gui.load_current().get("job_id"), "job_c")
        self.assertEqual([e["job_id"] for e in gui.load_background()], ["job_b"])
        st = gui.read_json(gui.job_dir("job_a") / "status.json")
        self.assertEqual(st["status"], "completed")

    def test_stop_job_kills_only_that_id(self):
        self._set_current("job_fg", 100)
        self._seed_job("job_a")
        self._seed_job("job_b")
        self.alive_pids.update({111, 222})
        gui.save_background(
            [
                {"job_id": "job_a", "pid": 111},
                {"job_id": "job_b", "pid": 222},
            ]
        )
        result = gui.stop_job({"job_id": "job_a"})
        self.assertTrue(result["stopped"])
        self.assertEqual(self.term_calls, [111])
        self.assertEqual(gui.load_current().get("job_id"), "job_fg")
        self.assertEqual([e["job_id"] for e in gui.load_background()], ["job_b"])
        st = gui.read_json(gui.job_dir("job_a") / "status.json")
        self.assertEqual(st["status"], "cancelled")
        self.assertEqual(
            gui.read_json(gui.job_dir("job_b") / "status.json")["status"], "running"
        )

    def test_stop_all_kills_foreground_and_every_background(self):
        self._set_current("job_fg", 100)
        self._seed_job("job_a")
        self._seed_job("job_b")
        self.alive_pids.update({111, 222})
        gui.save_background(
            [
                {"job_id": "job_a", "pid": 111},
                {"job_id": "job_b", "pid": 222},
            ]
        )
        queued = gui.create_job({"task": "later", "original_prompt": "later"})
        result = gui.stop_all_jobs()
        self.assertTrue(result["success"])
        self.assertEqual(set(self.term_calls), {100, 111, 222})
        self.assertEqual(gui.load_current(), {})
        self.assertEqual(gui.load_background(), [])
        self.assertEqual(gui.load_queue(), [])
        self.assertEqual(
            gui.read_json(gui.job_dir(queued) / "status.json")["status"], "cancelled"
        )

    def test_clear_completed_skips_live_background(self):
        self._seed_job("job_done", status="completed")
        gui.update_status("job_done", status="completed", success=True)
        self._seed_job("job_bg")
        self.alive_pids.add(222)
        gui.save_background([{"job_id": "job_bg", "pid": 222}])
        result = gui.clear_completed_jobs()
        self.assertIn("job_done", result["removed"])
        self.assertFalse((self.memory / "jobs" / "job_done").exists())
        self.assertTrue((self.memory / "jobs" / "job_bg").exists())
        skipped_ids = {row["job_id"] for row in result["skipped"]}
        self.assertIn("job_bg", skipped_ids)

    def test_parallel_failsafe_blocks_start_not_two(self):
        self._seed_job("job_a")
        self._seed_job("job_b")
        self.alive_pids.update({111, 222})
        gui.save_background(
            [
                {"job_id": "job_a", "pid": 111},
                {"job_id": "job_b", "pid": 222},
            ]
        )
        gui.create_job({"task": "third", "original_prompt": "third"})
        with patch.object(gui, "MAX_PARALLEL_JOBS", 2):
            started = gui.start_next()
        self.assertFalse(started["started"])
        self.assertEqual(started["reason"], "parallel job limit")
        self.assertEqual(gui.load_current(), {})
        self.assertEqual(len(gui.load_background()), 2)

    def test_git_write_refused_when_parallel(self):
        self._set_current("job_fg", 100)
        self._seed_job("job_bg")
        self.alive_pids.add(222)
        gui.save_background([{"job_id": "job_bg", "pid": 222}])
        result = gui.git_action({"action": "add_all"})
        self.assertFalse(result["success"])
        self.assertIn("multiple jobs", result["reason"])

    def test_restart_background_keeps_foreground(self):
        self._set_current("job_fg", 100)
        self._seed_job("job_bg")
        self.alive_pids.add(222)
        gui.save_background([{"job_id": "job_bg", "pid": 222}])
        with patch.object(gui, "restart_config_from_job", side_effect=lambda job_dir, config: dict(config)):
            result = gui.restart_job({"job_id": "job_bg"})
        self.assertTrue(result["success"])
        self.assertEqual(gui.load_current().get("job_id"), "job_fg")
        self.assertEqual(gui.load_background(), [])
        self.assertIn(result["new_job_id"], gui.load_queue())
        self.assertEqual(self.term_calls, [222])

    def _popen_side_effect(self, pids):
        it = iter(pids)

        def popen(*args, **kwargs):
            proc = _Proc(next(it))
            self.alive_pids.add(proc.pid)
            return proc

        return popen

    def test_start_selected_queued_parallel_keeps_current(self):
        self._set_current("job_fg", 100)
        queued = gui.create_job({"task": "q", "original_prompt": "q"})
        leftover = gui.create_job({"task": "q2", "original_prompt": "q2"})
        with patch.object(gui.subprocess, "Popen", side_effect=self._popen_side_effect([501])):
            result = gui.start_queued_jobs_parallel({"job_ids": [queued]})
        self.assertEqual(result["started_count"], 1)
        self.assertEqual(result["started"][0]["slot"], "background")
        self.assertEqual(gui.load_current().get("job_id"), "job_fg")
        self.assertEqual([e["job_id"] for e in gui.load_background()], [queued])
        self.assertEqual(gui.load_queue(), [leftover])
        st = gui.read_json(gui.job_dir(queued) / "status.json")
        self.assertEqual(st["status"], "running")

    def test_start_selected_parallel_fills_empty_current(self):
        first = gui.create_job({"task": "a", "original_prompt": "a"})
        second = gui.create_job({"task": "b", "original_prompt": "b"})
        with patch.object(gui.subprocess, "Popen", side_effect=self._popen_side_effect([601, 602])):
            result = gui.start_queued_jobs_parallel({"job_ids": [first, second]})
        self.assertEqual(result["started_count"], 2)
        self.assertEqual(gui.load_current().get("job_id"), first)
        self.assertEqual([e["job_id"] for e in gui.load_background()], [second])
        self.assertEqual(gui.load_queue(), [])
        slots = {row["job_id"]: row["slot"] for row in result["started"]}
        self.assertEqual(slots[first], "current")
        self.assertEqual(slots[second], "background")

    def test_start_selected_parallel_skips_not_queued(self):
        self._set_current("job_fg", 100)
        result = gui.start_queued_jobs_parallel({"job_ids": ["job_missing"]})
        self.assertEqual(result["started_count"], 0)
        self.assertEqual(result["skipped"][0]["reason"], "not in queue")
        self.assertEqual(gui.load_current().get("job_id"), "job_fg")

    def test_start_selected_parallel_respects_cap(self):
        self._set_current("job_fg", 100)
        queued = gui.create_job({"task": "q", "original_prompt": "q"})
        with patch.object(gui, "MAX_PARALLEL_JOBS", 1):
            result = gui.start_queued_jobs_parallel({"job_ids": [queued]})
        self.assertEqual(result["started_count"], 0)
        self.assertEqual(result["skipped"][0]["reason"], "parallel job limit")
        self.assertEqual(gui.load_queue(), [queued])

    def test_cancel_queued_skips_background_ids(self):
        self._seed_job("job_bg")
        self.alive_pids.add(222)
        gui.save_background([{"job_id": "job_bg", "pid": 222}])
        result = gui.cancel_queued_jobs({"job_ids": ["job_bg"]})
        self.assertEqual(result["cancelled"], [])
        self.assertEqual(result["skipped"][0]["reason"], "currently running")


if __name__ == "__main__":
    unittest.main()
