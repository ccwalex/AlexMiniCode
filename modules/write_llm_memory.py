import json
import os
from datetime import datetime
from cfg import CFG


MODULE_METADATA = {
    "name": "write_llm_memory",
    "type": "function",
    "description": "Append reusable LLM lesson/workaround to agent_memory/reasoning/llm_memory.json.",
    "functions": [
        {
            "name": "write_llm_memory",
            "inputs": {
                "issue": "str reusable problem",
                "solution": "str reusable solution",
                "check": "str future detection string/pattern",
                "confidence": "str high|medium|low"
            },
            "outputs": "dict"
        }
    ]
}


def write_llm_memory(issue, solution, check="", confidence="medium"):
    path = os.path.join(CFG.PROJECT_ROOT, "agent_memory", "reasoning", "llm_memory.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = []

    if not isinstance(data, list):
        data = []

    item = {
        "created_at": datetime.utcnow().isoformat() + "Z",
        "issue": str(issue).strip(),
        "solution": str(solution).strip(),
        "check": str(check).strip(),
        "confidence": str(confidence or "medium").strip(),
    }

    data.append(item)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return {
        "success": True,
        "path": "agent_memory/reasoning/llm_memory.json",
        "item": item,
    }