"""HTTP smoke tests for mcp_gateway download/upload + tools."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from mcp_gateway.server import create_asgi_app, create_gateway


class GatewayHttpSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name).resolve()
        (self.root / "data").mkdir()
        self.token = "test-token-xyz"
        self.mcp = create_gateway(
            root=self.root,
            host="127.0.0.1",
            port=8765,
            token=self.token,
            public_base="http://127.0.0.1:8765",
        )
        self.app = create_asgi_app(self.mcp)
        self.client = TestClient(self.app)
        self.auth = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self) -> None:
        self.client.close()
        self._tmpdir.cleanup()

    def test_unauthorized_without_token(self) -> None:
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 401)

    def test_health(self) -> None:
        resp = self.client.get("/health", headers=self.auth)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["auth_required"])

    def test_upload_download_roundtrip(self) -> None:
        payload = b"\x00\x01binary-smoke-\xff\xfe"
        resp = self.client.post(
            "/upload",
            headers=self.auth,
            data={"path": "data/blob.bin"},
            files={"file": ("blob.bin", payload, "application/octet-stream")},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["bytes"], len(payload))

        written = (self.root / "data" / "blob.bin").read_bytes()
        self.assertEqual(written, payload)

        dl = self.client.get(
            "/download",
            headers=self.auth,
            params={"path": "data/blob.bin"},
        )
        self.assertEqual(dl.status_code, 200)
        self.assertEqual(dl.content, payload)
        disposition = dl.headers.get("content-disposition", "")
        self.assertIn("attachment", disposition)
        self.assertIn("blob.bin", disposition)

    def test_download_escape_rejected(self) -> None:
        resp = self.client.get(
            "/download",
            headers=self.auth,
            params={"path": "../outside.txt"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_upload_escape_rejected(self) -> None:
        resp = self.client.post(
            "/upload",
            headers=self.auth,
            data={"path": "../escape.bin"},
            files={"file": ("escape.bin", b"x", "application/octet-stream")},
        )
        self.assertEqual(resp.status_code, 400)

    def test_write_file_and_list_dir_tools(self) -> None:
        import asyncio

        async def _call(name: str, arguments: dict):
            return await self.mcp.call_tool(name, arguments)

        wres = asyncio.run(
            _call("write_file", {"path": "hello.txt", "content": "hello gateway"})
        )
        self.assertTrue(wres)

        listed = asyncio.run(_call("list_dir", {"path": "."}))
        self.assertTrue(listed)

        url_res = asyncio.run(_call("get_download_url", {"path": "hello.txt"}))
        self.assertTrue(url_res)
        # Structured dict or text content with the download URL.
        blob = url_res if isinstance(url_res, dict) else str(url_res)
        self.assertIn("download", str(blob))

        script = asyncio.run(
            _call(
                "run_script",
                {
                    "command": f"{sys.executable} -c \"print('ok-smoke')\"",
                    "timeout_seconds": 30,
                },
            )
        )
        self.assertTrue(script)
        self.assertIn("ok-smoke", str(script))

        self.assertEqual(
            (self.root / "hello.txt").read_text(encoding="utf-8"),
            "hello gateway",
        )


if __name__ == "__main__":
    unittest.main()
