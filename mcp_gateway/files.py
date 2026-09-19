"""Download / upload helpers for the MCP gateway."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from mcp_gateway.sandbox import PathEscapeError, resolve_under_root


DEFAULT_MAX_UPLOAD_BYTES = 64 * 1024 * 1024


def download_response(root: Path, rel_path: str) -> Response:
    """Build a FileResponse with attachment disposition for ``rel_path``."""
    try:
        full = resolve_under_root(root, rel_path)
    except PathEscapeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    if not full.is_file():
        return JSONResponse({"error": f"file not found: {rel_path}"}, status_code=404)

    media_type, _ = mimetypes.guess_type(full.name)
    filename = full.name
    # RFC 5987 filename* for non-ASCII; keep simple filename= for common clients.
    disposition = f'attachment; filename="{filename}"; filename*=UTF-8\'\'{quote(filename)}'
    return FileResponse(
        path=full,
        media_type=media_type or "application/octet-stream",
        headers={"Content-Disposition": disposition},
        filename=filename,
    )


async def handle_upload(
    request: Request,
    root: Path,
    *,
    max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
) -> Response:
    """Accept multipart ``path`` + ``file`` and write under ``root``."""
    form = await request.form()
    rel_path = form.get("path")
    upload = form.get("file")

    if not isinstance(rel_path, str) or not rel_path.strip():
        return JSONResponse({"error": "missing form field: path"}, status_code=400)
    if upload is None or not hasattr(upload, "read"):
        return JSONResponse({"error": "missing form field: file"}, status_code=400)

    try:
        full = resolve_under_root(root, rel_path.strip())
    except PathEscapeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    if full.exists() and full.is_dir():
        return JSONResponse({"error": f"path is a directory: {rel_path}"}, status_code=400)

    data = await upload.read()
    if hasattr(upload, "close"):
        await upload.close()
    if len(data) > max_bytes:
        return JSONResponse(
            {"error": f"upload exceeds max size of {max_bytes} bytes"},
            status_code=413,
        )

    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(data)
    return JSONResponse(
        {
            "ok": True,
            "path": rel_path.strip(),
            "bytes": len(data),
        }
    )
