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

## Jupyter Docker / `/proxy/<port>/` (flybrain_agent)

When the gateway runs **inside a Jupyter-controlled Docker container**, expose it only on `127.0.0.1` and let Jupyter proxy external traffic:

```text
Cursor  →  http://<jupyter-host>:7192/proxy/7890/mcp  →  localhost:7890 (gateway in container)
```

### 1. Start the gateway inside the container

The MCP gateway starts **automatically** when you launch the Gen2 web GUI:

```bash
python gen2_web_gui_tracked.py
```

To start it manually instead (or without the GUI):

```bash
export MCP_GATEWAY_ROOT=/mnt/zpool1/docker_dir/Documents/flybrain_exp
export JUPYTER_PUBLIC_HOST=100.125.87.90   # host Cursor reaches
export JUPYTER_PUBLIC_PORT=7192            # Jupyter Server port
# JUPYTER_TOKEN is usually already set in the image

cd /path/to/agent
bash mcp_gateway/start_jupyter.sh
```

Pass `--no-mcp-gateway` to the web GUI if you need to disable auto-start. Logs go to `agent_memory/jobs/mcp_gateway.log` (or `agent/mcp_gateway.log` as fallback).

Or manually:

```bash
python run_mcp_gateway.py \
  --jupyter \
  --root "$MCP_GATEWAY_ROOT" \
  --host 127.0.0.1 \
  --port 7890 \
  --public-base "http://100.125.87.90:7192/proxy/7890"
```

**Do not** set `MCP_GATEWAY_TOKEN` in Jupyter mode — Jupyter auth is the outer boundary. A second bearer token would conflict with Cursor's `Authorization` header.

`--jupyter` also picks up `JUPYTER_TOKEN` (or `JUPYTERHUB_API_TOKEN`) and appends it to `get_download_url` results so file downloads work without MCP headers.

### 2. Configure Cursor (`~/.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "flybrain_agent": {
      "url": "http://100.125.87.90:7192/proxy/7890/mcp",
      "headers": {
        "Authorization": "token ${env:JUPYTER_TOKEN}"
      }
    }
  }
}
```

Set `JUPYTER_TOKEN` in your Mac shell to the same token the Jupyter container uses (from `jupyter server list`, the container env, or the Jupyter URL `?token=…`). Restart Cursor after editing.

Jupyter expects `Authorization: token <value>` — **not** `Bearer`.

### 3. Verify from your Mac

```bash
export JUPYTER_TOKEN='paste-token-here'

curl -s -H "Authorization: token $JUPYTER_TOKEN" \
  "http://100.125.87.90:7192/proxy/7890/health"

curl -s -X POST -H "Content-Type: application/json" \
  -H "Authorization: token $JUPYTER_TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' \
  "http://100.125.87.90:7192/proxy/7890/mcp"
```

You should get JSON (`{"ok":true,...}` or MCP `initialize` result), **not** Jupyter `403 Forbidden` HTML.

### Common failures

| Symptom | Cause | Fix |
|---------|-------|-----|
| `403 Forbidden` HTML from Jupyter | Missing/invalid Jupyter token | Set `JUPYTER_TOKEN` in Cursor env + `mcp.json` headers |
| Connection refused on `:7890` direct | Port not exposed (by design) | Use `/proxy/7890/` URL, not direct port |
| MCP connects but downloads fail | Download URL lacks Jupyter token | Use `--jupyter` so `JUPYTER_TOKEN` is appended to URLs |
| Tools empty / auth error | `MCP_GATEWAY_TOKEN` set inside container | Remove it; rely on Jupyter auth only |

See also [examples/cursor-jupyter-mcp.json](examples/cursor-jupyter-mcp.json).
