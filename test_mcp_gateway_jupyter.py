"""Tests for Jupyter proxy URL helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from mcp_gateway.server import build_download_url


class JupyterUrlTests(unittest.TestCase):
    def test_download_url_without_auth_query(self) -> None:
        url = build_download_url("http://host:7192/proxy/7890", "data/a.txt")
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/proxy/7890/download")
        self.assertEqual(qs["path"], ["data/a.txt"])
        self.assertNotIn("token", qs)

    def test_download_url_with_jupyter_token(self) -> None:
        url = build_download_url(
            "http://host:7192/proxy/7890",
            "data/a.txt",
            "token=secret123",
        )
        qs = parse_qs(urlparse(url).query)
        self.assertEqual(qs["path"], ["data/a.txt"])
        self.assertEqual(qs["token"], ["secret123"])


if __name__ == "__main__":
    unittest.main()
