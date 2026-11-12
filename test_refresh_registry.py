import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "modules"))

import refresh_registry
from refresh_registry import run_registry_metadata_pass
from update_registry import update_registry


class FakeTrackFolders:
    def __init__(self, folders):
        self._folders = folders

    def get_all(self):
        return self._folders


class RegistryPassTests(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = self._tmpdir.name
        os.chdir(self.root)

        self.src = os.path.join(self.root, "code")
        self.meta = os.path.join(self.root, "agent_memory", "meta", "code")
        os.makedirs(self.src)
        os.makedirs(self.meta)
        os.makedirs(os.path.join(self.root, "agent_memory", "core"))

        self.src_file = os.path.join(self.src, "example.py")
        with open(self.src_file, "w", encoding="utf-8") as f:
            f.write("print('hello')\n")

        self.sidecar = os.path.join(self.meta, "example.py.txt")
        old = {"name": "old_example", "type": "function", "path": "code/example.py"}
        with open(self.sidecar, "w", encoding="utf-8") as f:
            json.dump(old, f)

        self.folders = [
            {
                "folder_path": self.src,
                "code_type": "py",
                "metadata_path": self.meta,
            }
        ]
        self.meta_calls = []

        def fake_read_file(path):
            full = os.path.join(self.root, path) if not os.path.isabs(path) else path
            with open(full, "r", encoding="utf-8") as f:
                return True, f.read()

        def fake_meta_caller(path, content=None):
            self.meta_calls.append(path)
            return {
                "name": "new_example",
                "type": "function",
                "description": "regenerated",
                "path": path,
            }

        def fake_meta_writer(metadata, path):
            return None

        self.patches = [
            patch.object(refresh_registry.CFG, "PROJECT_ROOT", self.root),
            patch.object(refresh_registry, "TrackFolders", lambda: FakeTrackFolders(self.folders)),
            patch.object(refresh_registry, "read_file", fake_read_file),
            patch.object(refresh_registry, "meta_caller", fake_meta_caller),
            patch.object(refresh_registry, "meta_writer", fake_meta_writer),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        os.chdir(self._cwd)
        self._tmpdir.cleanup()

    def _sidecar(self):
        with open(self.sidecar, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_update_skips_existing_sidecar(self):
        ok, msg = run_registry_metadata_pass(overwrite=False, summary_verb="updated")
        self.assertTrue(ok)
        self.assertEqual(self.meta_calls, [])
        self.assertEqual(self._sidecar()["name"], "old_example")
        self.assertIn("existing=1", msg)
        self.assertIn("generated=0", msg)

    def test_refresh_overwrites_existing_sidecar(self):
        ok, msg = run_registry_metadata_pass(overwrite=True, summary_verb="refreshed")
        self.assertTrue(ok)
        self.assertEqual(self.meta_calls, ["code/example.py"])
        self.assertEqual(self._sidecar()["name"], "new_example")
        self.assertIn("generated=1", msg)
        self.assertIn("existing=0", msg)

    def test_refresh_keeps_old_sidecar_on_generate_failure(self):
        def failing_caller(path, content=None):
            self.meta_calls.append(path)
            return {"error": "llm failed"}

        with patch.object(refresh_registry, "meta_caller", failing_caller):
            ok, msg = run_registry_metadata_pass(overwrite=True, summary_verb="refreshed")

        self.assertFalse(ok)
        self.assertEqual(self._sidecar()["name"], "old_example")
        self.assertIn("failed=1", msg)

    def test_update_registry_wrapper_skips_existing(self):
        ok, msg = update_registry()
        self.assertTrue(ok)
        self.assertEqual(self._sidecar()["name"], "old_example")
        self.assertIn("updated", msg)


if __name__ == "__main__":
    unittest.main()
