# Gen2 Agent

Gen2 is a modular coding agent that plans and executes software tasks through a compact JSON API. It reads and edits files, runs shell commands, delegates work to isolated subagents, and verifies changes before finishing.

The agent ships with a web GUI, an HTTP subagent API, Cursor SDK integration, and an OpenCode Go LLM backend.

## Features

- **Planner–executor loop** — The main planner emits structured API calls (`/read`, `/write`, `/edit`, `/shell`, `/subagent`, `/request_feedback`, `/done`) that the backend executes in order.
- **Structured code editing** — Parser-generated block tables for Python, TypeScript/Node, React TSX, and HTML. The model selects block IDs; the backend applies edits using deterministic line spans.
- **Module metadata registry** — Tracked folders expose `MODULE_METADATA` summaries so the planner can discover available functions without reading every file.
- **Subagent delegation** — Delegate bounded tasks to review or implement roles in isolated process workers. Review subagents can read, shell, and analyze in parallel. Implement subagents run sequentially with write/edit tools and should only receive a finished, well-defined checklist (not open-ended design or diagnosis). Only summaries and changed artifact paths return to the main planner.
- **Web GUI and job queue** — Submit tasks through a browser UI or JSON API. Jobs run one at a time through a sequential queue with logs and status polling.
- **Discussion mode** — Multi-turn conversations to resolve planning conflicts by revising `project.md` and `current_plan.md`.
- **Dual LLM backends** — OpenCode Go subscription (default) or Cursor SDK (`cursor-sdk`) with per-role model configuration and fallback chains.
- **Agent memory** — Persistent project context, plans, run history, and reasoning notes under `agent_memory/`.
- **MCP HTTP gateway** — Optional Streamable-HTTP MCP server with real file download/upload and script tools for other agents/IDEs (`mcp_gateway/`, see [mcp_gateway/README.md](mcp_gateway/README.md)).

## Project layout

Gen2 is designed to live inside a parent project as the `agent/` directory. The agent treats the parent directory as the project root.

```text
your-project/
├── agent/                  # This repository
│   ├── agent.py            # Public Python entry point
│   ├── modules/            # Agent implementation modules
│   ├── gen2_web_gui_tracked.py
│   ├── run_gen2_job_worker.py
│   └── run_subagent_worker.py
├── agent_memory/           # Runtime state (created at project root)
│   ├── core/
│   ├── planning/
│   ├── summaries/
│   ├── reasoning/
│   ├── execution/
│   └── jobs/
└── runs/                   # Optional run artifacts
```

When you clone this repo, place its contents in `your-project/agent/`. Copy or initialize `agent_memory/` at the project root before running tasks.

## Requirements

- Python 3.10+
- [`requests`](https://pypi.org/project/requests/) — required for OpenCode LLM calls and the HTTP subagent client
- [`PyYAML`](https://pypi.org/project/PyYAML/) and [`ruff`](https://pypi.org/project/ruff/) — required for vendored deterministic Python checking (`modules/python_checker/`)
- [`cursor-sdk`](https://pypi.org/project/cursor-sdk/) — optional; required only when using the Cursor LLM backend

## Installation

```bash
# From your project root
git clone <repo-url> agent
cd agent

pip install -r requirements.txt

# Optional: enable Cursor SDK backend
pip install cursor-sdk

# Optional: MCP HTTP gateway (file download/upload + run_script tools)
pip install -r requirements-mcp.txt
# See mcp_gateway/README.md
```

Set your OpenCode API key for the default backend:

```bash
export OPENCODE_API_KEY="your-opencode-go-api-key"
```

OpenCode Go uses `https://opencode.ai/zen/go/v1`. Per-role model settings live in `agent_memory/model_config.json`.

## Quick start

All commands below assume your shell is at the **project root** (the parent of `agent/`).

### Python API

```python
from agent import run_task

result = run_task(
    task="Add a docstring to agent/modules/cfg.py",
    model="deepseek-v4-flash",
    effort="l",
    max_tokens=4096,
)

print(result["success"], result["status"], result["reason"])
```

### Web GUI

```bash
python agent/gen2_web_gui_tracked.py --host 127.0.0.1 --port 7860
# Also starts the Jupyter MCP gateway on port 7890 (use --no-mcp-gateway to skip)
```

Open [http://127.0.0.1:7860/](http://127.0.0.1:7860/) in a browser to submit tasks, browse the file tree, and inspect job logs. **Stop Agent** terminates the running job worker (SIGTERM, then SIGKILL), the same as stopping the script.

### Subagent API (HTTP)

```bash
curl -s -X POST http://127.0.0.1:7860/api/subagent/run \
  -H 'Content-Type: application/json' \
  -d '{
    "prompt": "Add a docstring to agent/modules/cfg.py",
    "selected_files": ["agent/modules/cfg.py"],
    "wait": true,
    "include_logs": true
  }'
```

### Subagent API (Python client)

```python
from modules.call_gen2_subagent import call_gen2_subagent

result = call_gen2_subagent(
    prompt="Add a docstring to agent/modules/cfg.py",
    selected_files=["agent/modules/cfg.py"],
    base_url="http://127.0.0.1:7860",
    wait=True,
    include_logs=True,
)

print(result["success"], result["reason"])
```

## How it works

```text
User task
    │
    ▼
┌─────────────────┐
│  run_task_v2    │  Planner–executor loop
│  (agent.py)     │
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
 Planner    Executor
 (LLM)     (API calls)
    │         │
    │    read / write / edit
    │    shell / subagent
    │    request_feedback / done
    ▼
 Debug & verify
 (on failure)
```

Each planner turn produces a JSON array of API calls. The executor runs them sequentially and stops on failure, feedback request, or completion. On parse or execution errors, a debug pass attempts repair before retrying.

### Planner API surface

| Action | Endpoint | Payload |
|--------|----------|---------|
| Read file | `/read` | `{"path": str}` |
| Write file | `/write` | `{"path": str, "content": str}` |
| Edit file | `/edit` | `{"df": str, "commands": list[str]}` |
| Run shell | `/shell` | `{"cmd": str}` |
| Delegate task | `/subagent` | `{"task": str, "role": "review\|implement", "files": list[str]}` |
| Request feedback | `/request_feedback` | `{}` |
| Finish | `/done` | `{"summary": str}` |

`/subagent` calls must form a trailing batch in a planner turn (optional `/request_feedback` after them). Review subagents are isolated process workers with read/shell/scratchpad/drop_cache only; they run in parallel and may survey or reason. Implement subagents have full write/edit tools, run sequentially, and should execute a finished checklist only (exact paths, concrete edits, constraints, verify) — not open-ended diagnosis or design. Prefer parent `/write`/`/edit` for small localized patches after a clear review summary. In mixed batches, all review subagents run first in parallel, then implement subagents one at a time.

## Configuration

| File | Purpose |
|------|---------|
| `modules/cfg.py` | Global defaults: OpenCode base URL, timeouts, iteration limits, token budgets |
| `agent_memory/model_config.json` | Per-role LLM source, model, effort, and token settings |
| `agent_memory/core/project.md` | Durable project description and constraints |
| `agent_memory/planning/current_plan.md` | Active execution plan |
| `agent_memory/core/modules.json` | Module registry index |

Default planner settings:

| Setting | Default |
|---------|---------|
| Model | `deepseek-v4-flash` |
| Effort | `l` (low) |
| Max tokens | `16384` |
| Max iterations | `20` |

## Testing

```bash
# From the agent/ directory
python3 -m unittest discover -s . -p 'test_*.py' -v
```

Test modules cover code editing, subagent delegation, the stage-1 edit pipeline, React TSX parsing, and mocked planner runs.

## Documentation

| Document | Description |
|----------|-------------|
| [`manual.md`](manual.md) | Full web GUI and subagent HTTP API reference |
| [`human_reference.md`](human_reference.md) | Project layout, API table, and block-table schema |
| [`metadata_reference.txt`](metadata_reference.txt) | `MODULE_METADATA` format for Python, TypeScript, and React |

## License

No license file is included yet. Add one before distributing or contributing.
