# Gen2 Web GUI API Manual

This document describes the HTTP API exposed by `gen2_web_gui_tracked.py` and the Python client in `modules/call_gen2_subagent.py`.

The Gen2 agent is a coding subagent: it accepts a task prompt, optionally attaches files and registry context, runs jobs through a sequential queue, and executes a planner/executor loop via `run_task_v2`.

---

## Quick start

### 1. Start the server

From the project root (parent of `agent/`):

```bash
python agent/gen2_web_gui_tracked.py --host 127.0.0.1 --port 7860
```

Default URL:

```text
http://127.0.0.1:7860/
```

Open that URL in a browser for the web GUI, or call the JSON API endpoints below.

### 2. Call Gen2 as a subagent (recommended)

**HTTP**

```bash
curl -s -X POST http://127.0.0.1:7860/api/subagent/run \
  -H 'Content-Type: application/json' \
  -d '{
    "prompt": "Add a docstring to agent/modules/cfg.py",
    "selected_files": ["agent/modules/cfg.py"],
    "wait": true,
    "timeout_seconds": 3600,
    "include_logs": true
  }'
```

**Python**

```python
from modules.call_gen2_subagent import call_gen2_subagent

result = call_gen2_subagent(
    prompt="Add a docstring to agent/modules/cfg.py",
    selected_files=["agent/modules/cfg.py"],
    base_url="http://127.0.0.1:7860",
    wait=True,
    include_logs=True,
)

print(result["success"])
print(result["reason"])
```

---

## Conventions

| Item | Value |
|------|-------|
| Content-Type | `application/json` for POST bodies |
| Response format | JSON |
| Path style | Project-root-relative paths, e.g. `agent/modules/cfg.py` |
| Job statuses | `queued`, `running`, `completed`, `failed`, `cancelled` |
| Default model | `nova` |
| Default effort | `l` (low) |
| Default max tokens | `16384` |

All POST requests accept an empty JSON object `{}` when no body fields are required.

Errors return JSON like:

```json
{
  "success": false,
  "error": "prompt required"
}
```

---

## Subagent API

Use these endpoints when another agent wants to delegate work to Gen2.

### `GET /api/subagent/status`

Returns service health, queue state, and current job info.

**Example**

```bash
curl -s http://127.0.0.1:7860/api/subagent/status
```

**Response**

```json
{
  "success": true,
  "service": "gen2_web_gui",
  "project_root": "/path/to/cursor_project",
  "queue_length": 0,
  "queue": [],
  "current": {},
  "current_job": null,
  "worker": "/path/to/agent/run_gen2_job_worker.py"
}
```

---

### `POST /api/subagent/run`

Submit a task to Gen2 and optionally wait for completion.

This is the main subagent entry point. It:

1. Optionally initializes agent memory
2. Builds the task prompt with attached files/registry context
3. Enqueues a job
4. Starts the worker if the queue is free
5. Optionally polls until the job reaches a terminal state

#### Request body

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `prompt` | string | required | The task for Gen2 |
| `selected_files` | string[] | `[]` | Project-relative files to inject into prompt context |
| `selected_registry_groups` | string[] | `[]` | Tracked folder paths whose metadata registries are injected |
| `model` | string | `nova` | Model selector: `nova`, `pro`, `gemini-3.5-flash`, etc. |
| `effort` | string | `l` | Planner effort: `l`, `m`, `h` |
| `max_tokens` | int | `16384` | Planner max token budget |
| `shell_instruction_prompt` | string | built-in default | Shell permission/safety rules |
| `max_iterations` | int | from CFG | Max planner loops |
| `max_feedback_loops` | int | from CFG | Max feedback loops |
| `max_retries` | int | from CFG | Max retries after parse/execution failure |
| `wait` | bool | `true` | If true, block until job finishes or times out |
| `timeout_seconds` | int | `3600` | Max wait time when `wait=true` |
| `poll_interval_seconds` | float | `2` | Poll interval while waiting |
| `include_logs` | bool | `false` | Include stdout/stderr in response |
| `init_if_needed` | bool | `true` | Run memory initialization before submitting |

#### Example: wait for completion

```bash
curl -s -X POST http://127.0.0.1:7860/api/subagent/run \
  -H 'Content-Type: application/json' \
  -d '{
    "prompt": "Explain what run_task_v2 does and add a one-line module docstring if missing.",
    "selected_files": ["agent/modules/run_task_v2.py"],
    "selected_registry_groups": ["agent/modules"],
    "model": "nova",
    "effort": "l",
    "wait": true,
    "timeout_seconds": 1800,
    "include_logs": true
  }'
```

#### Example response when `wait=true` and job completes

```json
{
  "success": true,
  "job_id": "job_20260818_133045_ab12cd34",
  "started": {
    "started": true,
    "current": {
      "job_id": "job_20260818_133045_ab12cd34",
      "pid": 12345,
      "started_at": "2026-08-18T13:30:45+00:00"
    },
    "reason": "worker started"
  },
  "status": "completed",
  "timed_out": false,
  "reason": "Gen2 task completed with status: done",
  "result": {
    "success": true,
    "status": "done",
    "reason": "Gen2 task completed with status: done"
  },
  "job": {
    "status": { "job_id": "...", "status": "completed", "success": true },
    "config": { "...": "..." },
    "result": { "...": "..." }
  },
  "poll_url": "/api/job/job_20260818_133045_ab12cd34",
  "logs_url": "/api/job/job_20260818_133045_ab12cd34/logs",
  "logs": {
    "job_id": "job_20260818_133045_ab12cd34",
    "stdout": "=== GEN2 TASK: ...",
    "stderr": ""
  }
}
```

#### Example: fire-and-forget

```bash
curl -s -X POST http://127.0.0.1:7860/api/subagent/run \
  -H 'Content-Type: application/json' \
  -d '{
    "prompt": "Run a quick inspection of agent/modules",
    "wait": false
  }'
```

#### Example response when `wait=false`

```json
{
  "success": true,
  "job_id": "job_20260818_133100_ef56gh78",
  "started": { "started": true, "reason": "worker started" },
  "status": "running",
  "poll_url": "/api/job/job_20260818_133100_ef56gh78",
  "logs_url": "/api/job/job_20260818_133100_ef56gh78/logs"
}
```

Poll with:

```bash
curl -s http://127.0.0.1:7860/api/job/job_20260818_133100_ef56gh78
```

---

## Python client

Module: `agent/modules/call_gen2_subagent.py`

### HTTP mode

Use when the Gen2 server is already running.

```python
from modules.call_gen2_subagent import call_gen2_subagent, gen2_subagent_status

status = gen2_subagent_status(base_url="http://127.0.0.1:7860")
print(status["queue_length"], status["current"])

result = call_gen2_subagent(
    prompt="Fix the shell output bug in build_feedback_context.py",
    selected_files=["agent/modules/build_feedback_context.py"],
    base_url="http://127.0.0.1:7860",
    wait=True,
    timeout_seconds=3600,
    include_logs=True,
)

if result["success"]:
    print("Done:", result["reason"])
else:
    print("Failed:", result["reason"])
    if result.get("logs"):
        print(result["logs"]["stderr"])
```

### Direct mode

Use when you want to invoke Gen2 in-process without HTTP.

```python
from modules.call_gen2_subagent import call_gen2_subagent

result = call_gen2_subagent(
    prompt="List the main functions in agent/modules/run_task_v2.py",
    selected_files=["agent/modules/run_task_v2.py"],
    mode="direct",
    wait=True,
)
```

Direct mode imports `gen2_web_gui_tracked.py` and calls `subagent_run()` directly.

---

## Job API

### `POST /api/submit`

Submit a job without the subagent wait/poll wrapper. Same payload fields as `/api/subagent/run`, except subagent-only fields (`wait`, `timeout_seconds`, etc.) are ignored.

```bash
curl -s -X POST http://127.0.0.1:7860/api/submit \
  -H 'Content-Type: application/json' \
  -d '{
    "prompt": "Create a hello world script in code/demo.py",
    "selected_files": [],
    "model": "nova",
    "effort": "l",
    "max_tokens": 16384
  }'
```

Response:

```json
{
  "success": true,
  "job_id": "job_20260818_133200_aa11bb22",
  "started": {
    "started": true,
    "reason": "worker started"
  }
}
```

### `GET /api/jobs`

List all jobs, newest first.

```bash
curl -s http://127.0.0.1:7860/api/jobs
```

### `GET /api/job/{job_id}`

Get one job's status, config, and result.

```bash
curl -s http://127.0.0.1:7860/api/job/job_20260818_133200_aa11bb22
```

Response shape:

```json
{
  "status": {
    "job_id": "job_20260818_133200_aa11bb22",
    "status": "completed",
    "success": true,
    "reason": "...",
    "created_at": "...",
    "started_at": "...",
    "ended_at": "..."
  },
  "config": {
    "task": "<user_request>...</user_request>...",
    "original_prompt": "...",
    "selected_files": [],
    "model": "nova"
  },
  "result": {
    "success": true,
    "status": "done",
    "reason": "..."
  },
  "effective_config": null
}
```

### `GET /api/job/{job_id}/logs`

Get stdout/stderr log tails for a job.

```bash
curl -s http://127.0.0.1:7860/api/job/job_20260818_133200_aa11bb22/logs
```

Response:

```json
{
  "job_id": "job_20260818_133200_aa11bb22",
  "stdout": "...",
  "stderr": "..."
}
```

### `POST /api/tick`

Advance the queue: refresh current job state and start the next queued job if idle.

```bash
curl -s -X POST http://127.0.0.1:7860/api/tick
```

---

## Job control API

### `POST /api/stop_current_job`

Stop the currently running worker process and mark the job `cancelled`.

```bash
curl -s -X POST http://127.0.0.1:7860/api/stop_current_job
```

### `POST /api/stop_all_jobs`

Stop the current job and clear all queued jobs.

```bash
curl -s -X POST http://127.0.0.1:7860/api/stop_all_jobs
```

### `POST /api/restart_current_job`

Stop the current job, recreate it from the same config, and start it again.

```bash
curl -s -X POST http://127.0.0.1:7860/api/restart_current_job
```

### `POST /api/restart_job`

Restart a specific job as a new job.

```bash
curl -s -X POST http://127.0.0.1:7860/api/restart_job \
  -H 'Content-Type: application/json' \
  -d '{"job_id": "job_20260818_133200_aa11bb22"}'
```

---

## Context API

These endpoints manage the file tree, tracked folders, and registry metadata injected into prompts.

### `GET /api/file_tree`

Returns the project file tree for GUI/API file selection.

```bash
curl -s http://127.0.0.1:7860/api/file_tree
```

### `GET /api/tracked_folders`

List tracked module folders.

```bash
curl -s http://127.0.0.1:7860/api/tracked_folders
```

### `POST /api/tracked_folders/add`

Add or update a tracked folder.

```bash
curl -s -X POST http://127.0.0.1:7860/api/tracked_folders/add \
  -H 'Content-Type: application/json' \
  -d '{
    "folder_path": "agent/modules",
    "code_type": "py"
  }'
```

Supported `code_type` values include: `py`, `html`, `react`, `ts`.

### `POST /api/tracked_folders/remove`

```bash
curl -s -X POST http://127.0.0.1:7860/api/tracked_folders/remove \
  -H 'Content-Type: application/json' \
  -d '{"folder_path": "agent/modules"}'
```

### `GET /api/registry_contexts`

List registry metadata available for prompt injection.

```bash
curl -s http://127.0.0.1:7860/api/registry_contexts
```

Use `selected_registry_groups` in submit/subagent requests with the `folder_path` values from this endpoint.

---

## System API

### `POST /api/init`

Initialize agent memory files and job storage.

```bash
curl -s -X POST http://127.0.0.1:7860/api/init
```

### `POST /api/refresh_all_registries`

Refresh tracked-folder metadata registries.

```bash
curl -s -X POST http://127.0.0.1:7860/api/refresh_all_registries
```

### `POST /api/git`

Run git commands in the project root.

Supported actions:

| action | extra fields | git command |
|--------|--------------|-------------|
| `status` | — | `git status --short` |
| `diff` | — | `git diff` |
| `log` | — | `git log --oneline --graph --decorate --all -n 30` |
| `add_all` | — | `git add .` |
| `commit` | `message` required | `git commit -m "..."` |

Example:

```bash
curl -s -X POST http://127.0.0.1:7860/api/git \
  -H 'Content-Type: application/json' \
  -d '{"action": "status"}'
```

---

## Recommended subagent workflow

For a calling agent, this is the usual sequence:

1. Check Gen2 is idle or acceptable to queue into:

   ```bash
   curl -s http://127.0.0.1:7860/api/subagent/status
   ```

2. Submit work and wait:

   ```bash
   curl -s -X POST http://127.0.0.1:7860/api/subagent/run \
     -H 'Content-Type: application/json' \
     -d '{"prompt":"...", "selected_files":["..."], "wait": true}'
   ```

3. Inspect result:

   - `success` — whether Gen2 completed successfully
   - `reason` — human-readable completion/failure reason
   - `result` — worker result from `run_task_v2`
   - `logs.stdout` / `logs.stderr` — if `include_logs=true`

4. If using `wait=false`, poll until terminal:

   ```bash
   curl -s http://127.0.0.1:7860/api/job/{job_id}
   ```

   Stop polling when `status.status` is one of: `completed`, `failed`, `cancelled`.

---

## Demo scenarios

### Demo 1: Simple one-shot task

```python
from modules.call_gen2_subagent import call_gen2_subagent

print(call_gen2_subagent(
    prompt="Summarize what agent/gen2_web_gui_tracked.py does in 5 bullet points.",
    selected_files=["agent/gen2_web_gui_tracked.py"],
    wait=True,
    mode="direct",
))
```

### Demo 2: Task with module registry context

```python
result = call_gen2_subagent(
    prompt="Find the function responsible for building planner prompts and explain its inputs.",
    selected_files=["agent/modules/build_prompt_v2.py"],
    selected_registry_groups=["agent/modules"],
    base_url="http://127.0.0.1:7860",
    wait=True,
)
```

### Demo 3: Async submit + manual polling

```bash
JOB=$(curl -s -X POST http://127.0.0.1:7860/api/subagent/run \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Inspect agent/modules/cfg.py","wait":false}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")

echo "job_id=$JOB"

while true; do
  STATUS=$(curl -s "http://127.0.0.1:7860/api/job/$JOB" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['status']['status'])")
  echo "status=$STATUS"
  case "$STATUS" in completed|failed|cancelled) break ;; esac
  sleep 2
done

curl -s "http://127.0.0.1:7860/api/job/$JOB/logs"
```

### Demo 4: Cancel a long-running job

```bash
curl -s -X POST http://127.0.0.1:7860/api/stop_current_job
```

---

## Job lifecycle

```text
submit/subagent_run
    -> queued
    -> running
    -> completed | failed | cancelled
```

Notes:

- Only one job runs at a time; others wait in `agent_memory/jobs/queue.json`.
- Job artifacts live under `agent_memory/jobs/{job_id}/`:
  - `config.json`
  - `status.json`
  - `result.json`
  - `stdout.log`
  - `stderr.log`
  - `task.txt`
- The worker script is `agent/run_gen2_job_worker.py`.
- The actual agent loop is `modules/run_task_v2.py`.

---

## Endpoint index

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Web GUI |
| GET | `/api/subagent/status` | Subagent health/status |
| POST | `/api/subagent/run` | Run Gen2 as subagent |
| POST | `/api/submit` | Submit job |
| GET | `/api/jobs` | List jobs |
| GET | `/api/job/{id}` | Job details |
| GET | `/api/job/{id}/logs` | Job logs |
| POST | `/api/tick` | Advance queue |
| POST | `/api/init` | Initialize memory/storage |
| POST | `/api/refresh_all_registries` | Refresh metadata registries |
| GET | `/api/file_tree` | Project file tree |
| GET | `/api/tracked_folders` | List tracked folders |
| POST | `/api/tracked_folders/add` | Add tracked folder |
| POST | `/api/tracked_folders/remove` | Remove tracked folder |
| GET | `/api/registry_contexts` | List registry contexts |
| POST | `/api/stop_current_job` | Stop running job |
| POST | `/api/stop_all_jobs` | Stop all and clear queue |
| POST | `/api/restart_current_job` | Restart current job |
| POST | `/api/restart_job` | Restart specific job |
| POST | `/api/git` | Git helper actions |

---

## Troubleshooting

| Problem | Likely cause | Fix |
|---------|--------------|-----|
| Connection refused | Server not running | Start `gen2_web_gui_tracked.py` |
| `prompt required` | Missing prompt | Include `"prompt": "..."` |
| Job stays `queued` | Another job is running | Wait, or stop current job |
| `timed_out: true` | Task exceeded `timeout_seconds` | Increase timeout or inspect logs |
| Empty registry context | Tracked folder not configured | Add tracked folder and refresh registries |
| Path errors | Non-project-relative path | Paths must be relative to project root |

---

## Related files

| File | Role |
|------|------|
| `agent/gen2_web_gui_tracked.py` | HTTP server and API implementation |
| `agent/run_gen2_job_worker.py` | Job worker process |
| `agent/modules/call_gen2_subagent.py` | Python client for subagent calls |
| `agent/modules/run_task_v2.py` | Gen2 planner/executor loop |
| `agent/human_reference.md` | Project/agent architecture notes |
