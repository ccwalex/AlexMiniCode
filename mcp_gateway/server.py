"""FastMCP Streamable-HTTP gateway with download/upload routes."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mcp_gateway.auth import BearerTokenMiddleware
from mcp_gateway.files import DEFAULT_MAX_UPLOAD_BYTES, download_response, handle_upload
from mcp_gateway.sandbox import PathEscapeError, relpath_under_root, resolve_under_root

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 30 * 60
MAX_BODY = 64 * 1024 * 1024


def default_project_root() -> Path:
    """Parent of the agent/ directory (Gen2 CFG.PROJECT_ROOT layout)."""
    # mcp_gateway/ -> agent/ -> project root
    return Path(__file__).resolve().parent.parent.parent


def build_public_base_url(host: str, port: int, public_base: str | None) -> str:
    if public_base:
        return public_base.rstrip("/")
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    return f"http://{display_host}:{port}"


def build_download_url(
    base_url: str,
    rel_path: str,
    url_auth_query: str | None = None,
) -> str:
    """Build a public download URL, optionally appending auth query params.

    ``url_auth_query`` is useful behind Jupyter's ``/proxy/<port>/`` where
    clients that fetch the URL directly (not via MCP headers) still need the
    Jupyter token, e.g. ``token=<jupyter-token>``.
    """
    params = {"path": rel_path}
    if url_auth_query:
        for part in url_auth_query.split("&"):
            if not part or "=" not in part:
                continue
            key, value = part.split("=", 1)
            if key:
                params[key] = value
    return f"{base_url.rstrip('/')}/download?{urlencode(params)}"


def create_gateway(
    *,
    root: Path,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    token: str | None = None,
    public_base: str | None = None,
    url_auth_query: str | None = None,
) -> FastMCP:
    """Create a configured FastMCP server with thin tools + file routes."""
    root = root.resolve()
    if not root.is_dir():
        raise SystemExit(f"project root is not a directory: {root}")

    base_url = build_public_base_url(host, port, public_base)

    # Allow non-loopback binds when explicitly configured.
    transport_security = None
    if host not in ("127.0.0.1", "localhost", "::1"):
        transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        )

    mcp = FastMCP(
        name="gen2-mcp-gateway",
        instructions=(
            "Thin MCP gateway for sandboxed project file transfer and script "
            "execution. Use get_download_url then HTTP GET /download for file "
            "bytes. Prefer POST /upload for binary or large files."
        ),
        host=host,
        port=port,
        streamable_http_path="/mcp",
        max_request_body_size=MAX_BODY,
        stateless_http=True,
        transport_security=transport_security,
    )

    @mcp.tool()
    def get_download_url(path: str) -> dict[str, Any]:
        """Return an HTTP URL to download a project file as an attachment.

        Clients should fetch the returned URL (with the same bearer token if
        configured) rather than expecting file bytes in this tool result.
        """
        try:
            full = resolve_under_root(root, path)
        except PathEscapeError as exc:
            return {"ok": False, "error": str(exc)}
        if not full.is_file():
            return {"ok": False, "error": f"file not found: {path}"}
        rel = relpath_under_root(root, full)
        url = build_download_url(base_url, rel, url_auth_query)
        return {"ok": True, "path": rel, "url": url}

    @mcp.tool()
    def write_file(path: str, content: str) -> dict[str, Any]:
        """Create or fully replace a UTF-8 text file under the project root."""
        try:
            full = resolve_under_root(root, path)
        except PathEscapeError as exc:
            return {"ok": False, "error": str(exc)}
        if full.exists() and full.is_dir():
            return {"ok": False, "error": f"path is a directory: {path}"}
        full.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        full.write_bytes(data)
        return {
            "ok": True,
            "path": relpath_under_root(root, full),
            "bytes": len(data),
        }

    @mcp.tool()
    def list_dir(path: str = ".") -> dict[str, Any]:
        """List directory entries under the project root."""
        try:
            full = resolve_under_root(root, path)
        except PathEscapeError as exc:
            return {"ok": False, "error": str(exc)}
        if not full.exists():
            return {"ok": False, "error": f"not found: {path}"}
        if not full.is_dir():
            return {"ok": False, "error": f"not a directory: {path}"}

        entries = []
        for child in sorted(full.iterdir(), key=lambda p: p.name.lower()):
            entries.append(
                {
                    "name": child.name,
                    "path": relpath_under_root(root, child),
                    "type": "dir" if child.is_dir() else "file",
                    "size": child.stat().st_size if child.is_file() else None,
                }
            )
        return {
            "ok": True,
            "path": relpath_under_root(root, full),
            "entries": entries,
        }

    @mcp.tool()
    def run_script(
        command: str,
        timeout_seconds: int = DEFAULT_TIMEOUT,
    ) -> dict[str, Any]:
        """Run a shell command with cwd set to the project root."""
        if not command or not str(command).strip():
            return {"ok": False, "error": "empty command"}
        try:
            timeout = int(timeout_seconds)
        except (TypeError, ValueError):
            return {"ok": False, "error": "timeout_seconds must be an integer"}
        if timeout <= 0:
            return {"ok": False, "error": "timeout_seconds must be positive"}
        if timeout > MAX_TIMEOUT:
            timeout = MAX_TIMEOUT

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            return {
                "ok": False,
                "error": f"timed out after {timeout}s",
                "stdout": stdout[-8000:],
                "stderr": stderr[-8000:],
                "timeout_seconds": timeout,
            }
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": (result.stdout or "")[-8000:],
            "stderr": (result.stderr or "")[-8000:],
            "timeout_seconds": timeout,
        }

    @mcp.custom_route("/download", methods=["GET"])
    async def download_route(request: Request) -> Response:
        path = request.query_params.get("path")
        if not path:
            return JSONResponse({"error": "missing query param: path"}, status_code=400)
        return download_response(root, path)

    @mcp.custom_route("/upload", methods=["POST"])
    async def upload_route(request: Request) -> Response:
        return await handle_upload(
            request,
            root,
            max_bytes=DEFAULT_MAX_UPLOAD_BYTES,
        )

    @mcp.custom_route("/health", methods=["GET"])
    async def health_route(_request: Request) -> Response:
        return JSONResponse(
            {
                "ok": True,
                "root": str(root),
                "auth_required": bool(token),
            }
        )

    # Stash config for create_asgi_app / run helpers.
    mcp._gateway_token = token  # type: ignore[attr-defined]
    mcp._gateway_root = root  # type: ignore[attr-defined]
    mcp._gateway_base_url = base_url  # type: ignore[attr-defined]
    return mcp


def create_asgi_app(mcp: FastMCP):
    """Return the Streamable-HTTP Starlette app wrapped with optional auth."""
    app = mcp.streamable_http_app()
    token = getattr(mcp, "_gateway_token", None)
    return BearerTokenMiddleware(app, token)


def run_server(
    *,
    root: Path | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    token: str | None = None,
    public_base: str | None = None,
    url_auth_query: str | None = None,
) -> None:
    """Build and serve the gateway over Streamable HTTP."""
    root = (root or default_project_root()).resolve()
    mcp = create_gateway(
        root=root,
        host=host,
        port=port,
        token=token,
        public_base=public_base,
        url_auth_query=url_auth_query,
    )
    app = create_asgi_app(mcp)
    print(
        f"[mcp-gateway] root={root} listen=http://{host}:{port}/mcp "
        f"download={build_public_base_url(host, port, public_base)}/download "
        f"auth={'on' if token else 'off'}",
        flush=True,
    )
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gen2 MCP HTTP gateway (thin tools + file transfer)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Project root to sandbox (default: parent of agent/)",
    )
    parser.add_argument("--host", default=os.environ.get("MCP_GATEWAY_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MCP_GATEWAY_PORT", DEFAULT_PORT)),
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("MCP_GATEWAY_TOKEN"),
        help="Optional bearer token (or MCP_GATEWAY_TOKEN)",
    )
    parser.add_argument(
        "--public-base",
        default=os.environ.get("MCP_GATEWAY_PUBLIC_BASE"),
        help="Public base URL used in get_download_url (e.g. http://host:8765)",
    )
    parser.add_argument(
        "--url-auth-query",
        default=os.environ.get("MCP_GATEWAY_URL_AUTH_QUERY"),
        help=(
            "Extra query string appended to get_download_url results "
            "(e.g. token=<jupyter-token> for Jupyter /proxy/ access)"
        ),
    )
    parser.add_argument(
        "--jupyter",
        action="store_true",
        help=(
            "Preset for Jupyter /proxy/<port>/ inside Docker: bind 127.0.0.1, "
            "default port 7890, warn if MCP_GATEWAY_TOKEN is set"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    root = args.root
    if root is None:
        env_root = os.environ.get("MCP_GATEWAY_ROOT")
        root = Path(env_root) if env_root else default_project_root()

    host = args.host
    port = args.port
    token = args.token
    public_base = args.public_base
    url_auth_query = args.url_auth_query

    if args.jupyter:
        if host == DEFAULT_HOST and os.environ.get("MCP_GATEWAY_HOST") is None:
            host = "127.0.0.1"
        if port == DEFAULT_PORT and os.environ.get("MCP_GATEWAY_PORT") is None:
            port = 7890
        if token:
            print(
                "WARNING: --jupyter mode: omit MCP_GATEWAY_TOKEN; Jupyter "
                "proxy auth is the outer boundary.",
                file=sys.stderr,
            )
        if not public_base:
            print(
                "WARNING: --jupyter requires --public-base, e.g. "
                "http://<host>:7192/proxy/7890",
                file=sys.stderr,
            )
        jupyter_token = os.environ.get("JUPYTER_TOKEN") or os.environ.get("JUPYTERHUB_API_TOKEN")
        if jupyter_token and not url_auth_query:
            url_auth_query = f"token={jupyter_token}"

    if host not in ("127.0.0.1", "localhost", "::1") and not token:
        print(
            "WARNING: binding non-loopback without --token / MCP_GATEWAY_TOKEN "
            "is unsafe.",
            file=sys.stderr,
        )
    run_server(
        root=root,
        host=host,
        port=port,
        token=token,
        public_base=public_base,
        url_auth_query=url_auth_query,
    )


if __name__ == "__main__":
    main()
