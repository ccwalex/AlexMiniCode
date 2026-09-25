#!/usr/bin/env python3
"""CLI entry for the Gen2 MCP HTTP gateway."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `python run_mcp_gateway.py` from the agent/ directory without install.
_AGENT_DIR = Path(__file__).resolve().parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from mcp_gateway.server import main


if __name__ == "__main__":
    main()
