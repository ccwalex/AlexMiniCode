# MCP HTTP Gateway

Thin Streamable-HTTP MCP server for sandboxed **file download/upload** and **script execution**. Other agents or IDEs connect over HTTP; file bytes go through real download/upload endpoints (not a text `read_file` tool).

## Install

From the `agent/` directory:

```bash
python3 -m venv .venv-mcp
source .venv-mcp/bin/activate
pip install -r requirements-mcp.txt
```

## Run

```bash
python run_mcp_gateway.py --root /path/to/project --port 8765
```

Optional auth:

```bash
export MCP_GATEWAY_TOKEN=secret
python run_mcp_gateway.py --root /path/to/project --token "$MCP_GATEWAY_TOKEN"
```

| Flag / env | Default | Meaning |
|------------|---------|---------|
| `--root` / `MCP_GATEWAY_ROOT` | parent of `agent/` | Sandbox project root |
| `--host` / `MCP_GATEWAY_HOST` | `127.0.0.1` | Bind address |
| `--port` / `MCP_GATEWAY_PORT` | `8765` | Port |
| `--token` / `MCP_GATEWAY_TOKEN` | unset | Bearer token for all routes |
| `--public-base` / `MCP_GATEWAY_PUBLIC_BASE` | derived from host/port | Base URL returned by `get_download_url` |

Binding `0.0.0.0` without a token is unsafe.

## Endpoints

| Path | Role |
|------|------|
| `/mcp` | MCP Streamable HTTP |
| `GET /download?path=…` | Download file as attachment |
| `POST /upload` | Multipart upload/replace (`path` + `file`) |
| `GET /health` | Liveness + root info |

### Download

```bash
curl -OJ -H "Authorization: Bearer $MCP_GATEWAY_TOKEN" \
  "http://127.0.0.1:8765/download?path=reports/out.pdf"
```

### Upload

```bash
curl -H "Authorization: Bearer $MCP_GATEWAY_TOKEN" \
  -F "path=data/in.bin" -F "file=@./in.bin" \
  http://127.0.0.1:8765/upload
```

## MCP tools

| Tool | Purpose |
|------|---------|
| `get_download_url` | Returns `http://…/download?path=…` for a project file |
| `write_file` | Small UTF-8 text create/replace |
| `list_dir` | List directory entries |
| `run_script` | Shell command with `cwd` = project root |

Point an HTTP MCP client at `http://127.0.0.1:8765/mcp` (include the bearer header when a token is set). Prefer `POST /upload` for binary or large files; use `get_download_url` + `GET /download` to fetch bytes.
