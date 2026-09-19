"""Optional bearer-token ASGI middleware for the MCP gateway."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send


class BearerTokenMiddleware:
    """Require ``Authorization: Bearer <token>`` when a token is configured."""

    def __init__(self, app: ASGIApp, token: str | None) -> None:
        self.app = app
        self.token = token.strip() if token else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.token:
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        auth = request.headers.get("authorization", "")
        expected = f"Bearer {self.token}"
        if auth != expected:
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def check_bearer(request: Request, token: str | None) -> Response | None:
    """Return a 401 response if bearer auth fails; otherwise ``None``."""
    if not token:
        return None
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {token}":
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return None
