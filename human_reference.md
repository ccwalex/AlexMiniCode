human reference

per-project agent layout
project
- agent/code/module
- agent/frontend/src
- agent_memory/
- runs/
- frontend html
- code or other project data dir
where agent folder holds its own code/module
and a frontend html resides in project


agent_memory/
  core/
    project.md
    principles.md
    [dir1_module].json
    [dir2_module].json
    tracking.json

  planning/
    current_plan.md

  summaries/
    compressed_context.md

  reasoning/
    llm_memory.json
    failures.md

  execution/
    recent_runs.json


multi-dir module tracking: tracking.json contains path to tracked module folder and type of code: python or typescript / nodejs


new call format

[
  {
    "url": "/read",
    "payload": {
      "path": "agent/code/modules/run_task.py"
    }
  },
  {
    "url": "/edit",
    "payload": {
      "df": "agent/code/modules/run_task.py",
      "commands": [
        "..."
      ]
    }
  },
  {
    "url": "/request_feedback",
    "payload": {}
  }
]

endpoint table injected to prompt
| action | url | payload |
|---|---|---|
| read file | /read | {"path": str} |
| write complete file | /write | {"path": str, "content": str} |
| edit existing file | /edit | {"df": str, "commands": list[str]} |
| run shell | /shell | {"cmd": str} |
| delegate task | /subagent | {"task": str, "role": "review\|implement", "files": list[str], "timeout_seconds": int} |
| request feedback | /request_feedback | {} |
| scratchpad notes | /scratchpad | {"action": "read\|set\|append\|clear", "content": str} |
| drop cached files | /drop_cache | {"paths": list[str]} |
| finish | /done | {"summary": str} |

`/subagent` calls must form a trailing batch in a planner turn (optional
`/request_feedback` after them). Review subagents are isolated process workers
with read/shell/scratchpad/drop_cache only; they run in parallel and may survey
or reason. Implement subagents have full write/edit tools, run sequentially,
and should execute a finished checklist only (exact paths, concrete edits,
constraints, verify) — not open-ended diagnosis or design. Prefer parent
`/write`/`/edit` for small localized patches after a clear review summary. In
mixed batches, all review subagents run first in parallel, then implement
subagents one at a time. Only the child summary, status, and changed artifact
paths return to the main planner.

`/scratchpad` is task-local RAM for a live working plan across planner turns
(conclusions, next steps, constraints, open questions). Prefer `append` for
new findings; use `set` only when rewriting the whole plan. Do not copy the
current task into the scratchpad. Content is not persisted across jobs.


Gen 2 structured edit uses a parser-generated flat block table.

The table contains every meaningful container row, not only leaves.

Rows may overlap because parent containers include child containers.

Each row has a unique backend-assigned id, explicit line span, parent pointer, depth, and optional variable/symbol metadata.

The LLM selects one or more row ids.

The backend applies edits using the selected row's parser-derived span.

The LLM never composes start/end spans from multiple ids.

table schema of deterministic anchors
id
type
name
start_line
end_line
parent
depth
vars_defined
+/- preview/header