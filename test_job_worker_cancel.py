import json
import signal
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_gen2_job_worker as worker


class JobWorkerCancelTests(unittest.TestCase):
    def setUp(self):
        self.job_dir = ROOT / "agent_memory" / "jobs" / "test_cancel_worker"
        self.job_dir.mkdir(parents=True, exist_ok=True)
        worker.write_json(
            self.job_dir / "status.json",
            {
                "job_id": "test_cancel_worker",
                "status": "running",
                "success": None,
            },
        )
        result_path = self.job_dir / "result.json"
        if result_path.exists():
            result_path.unlink()

    def test_write_cancelled_result(self):
        reason = worker.write_cancelled_result(self.job_dir, signal.SIGTERM)
        self.assertIn("SIGTERM", reason)

        result = worker.read_json(self.job_dir / "result.json")
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(result["success"])

        status = worker.read_json(self.job_dir / "status.json")
        self.assertEqual(status["status"], "cancelled")
        self.assertFalse(status["success"])

    def test_install_cancel_handlers_writes_cancelled_on_sigterm(self):
        with patch.object(worker, "write_cancelled_result", wraps=worker.write_cancelled_result) as mocked:
            worker.install_cancel_handlers(self.job_dir)
            with self.assertRaises(SystemExit) as ctx:
                signal.raise_signal(signal.SIGTERM)
            self.assertEqual(ctx.exception.code, 128 + signal.SIGTERM)
            mocked.assert_called_once()
            self.assertEqual(mocked.call_args[0][0], self.job_dir)
            self.assertEqual(mocked.call_args[0][1], signal.SIGTERM)

        result = worker.read_json(self.job_dir / "result.json")
        self.assertEqual(result["status"], "cancelled")

    def test_refresh_current_respects_cancelled_result(self):
        import gen2_web_gui_tracked as gui

        memory = ROOT / "agent_memory"
        with patch.object(gui, "memory_dir", return_value=memory):
            gui.ensure_storage()
            jid = "test_refresh_cancel"
            d = gui.job_dir(jid)
            d.mkdir(parents=True, exist_ok=True)
            gui.write_json(
                d / "status.json",
                {"job_id": jid, "status": "running", "success": None},
            )
            gui.write_json(
                d / "result.json",
                {
                    "success": False,
                    "status": "cancelled",
                    "reason": "terminated by user (SIGTERM)",
                },
            )
            gui.save_current({"job_id": jid, "pid": 999999999})

            with patch.object(gui, "alive", return_value=False):
                gui.refresh_current()

            status = gui.read_json(d / "status.json")
            self.assertEqual(status["status"], "cancelled")
            self.assertEqual(gui.load_current(), {})


if __name__ == "__main__":
    unittest.main()
