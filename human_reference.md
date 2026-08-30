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
| delegate sequential task | /subagent | {"task": str, "role": "explore\|review\|implement", "mode": "process\|readonly", "files": list[str], "timeout_seconds": int} |
| request feedback | /request_feedback | {} |
| finish | /done | {"summary": str} |

`/subagent` must be the final call in a planner turn. Process mode launches an
isolated blocking worker and is required for implementation. Readonly mode is a
single in-process LLM call for bounded exploration or review. Only the child
summary, status, and changed artifact paths return to the main planner.


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