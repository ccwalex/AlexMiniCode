"""Unit tests for mcp_gateway path sandboxing."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from mcp_gateway.sandbox import PathEscapeError, relpath_under_root, resolve_under_root


class SandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name).resolve()
        (self.root / "sub").mkdir()
        (self.root / "sub" / "a.txt").write_text("hi", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_relative_inside(self) -> None:
        full = resolve_under_root(self.root, "sub/a.txt")
        self.assertEqual(full, self.root / "sub" / "a.txt")
        self.assertEqual(relpath_under_root(self.root, full), "sub/a.txt")

    def test_dot_parent_escape(self) -> None:
        with self.assertRaises(PathEscapeError):
            resolve_under_root(self.root, "../outside.txt")

    def test_absolute_outside(self) -> None:
        outside = Path(tempfile.gettempdir()).resolve() / "mcp_gateway_escape_probe.txt"
        with self.assertRaises(PathEscapeError):
            resolve_under_root(self.root, outside)

    def test_absolute_inside_allowed(self) -> None:
        inside = self.root / "sub" / "a.txt"
        full = resolve_under_root(self.root, inside)
        self.assertEqual(full, inside.resolve())

    def test_root_itself(self) -> None:
        full = resolve_under_root(self.root, ".")
        self.assertEqual(full, self.root)
        self.assertEqual(relpath_under_root(self.root, full), ".")


if __name__ == "__main__":
    unittest.main()
