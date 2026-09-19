#!/usr/bin/env bash
# Start the MCP gateway inside a Jupyter Docker container.
# Jupyter proxies /proxy/7890/* to this process on localhost:7890.
set -euo pipefail

ROOT="${MCP_GATEWAY_ROOT:-$(pwd)}"
JUPYTER_PUBLIC_HOST="${JUPYTER_PUBLIC_HOST:-100.125.87.90}"
JUPYTER_PUBLIC_PORT="${JUPYTER_PUBLIC_PORT:-7192}"
GATEWAY_PORT="${MCP_GATEWAY_PORT:-7890}"

PUBLIC_BASE="http://${JUPYTER_PUBLIC_HOST}:${JUPYTER_PUBLIC_PORT}/proxy/${GATEWAY_PORT}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

exec python "${AGENT_DIR}/run_mcp_gateway.py" \
  --jupyter \
  --root "${ROOT}" \
  --host 127.0.0.1 \
  --port "${GATEWAY_PORT}" \
  --public-base "${PUBLIC_BASE}"
