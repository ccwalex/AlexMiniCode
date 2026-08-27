import ast
import json
import re


MODULE_METADATA = {
    "name": "extract_plan",
    "type": "function",
    "description": "Parse and normalize planner output into a flat list of executable action dictionaries.",
    "functions": [
        {
            "name": "extract_plan",
            "inputs": {
                "raw_input": (
                    "dict, list, or str planner output. Accepts {'plan': [...]}, "
                    "{'action': ...}, list of actions, raw JSON string, Python-literal-like string, "
                    "or common LLM response wrappers"
                )
            },
            "outputs": "list of dict action items normalized from planner output"
        }
    ]
}


def extract_plan(raw_input):
    """
    Robust planner output normalizer.

    Preferred input:
    {
      "plan": [
        {"action": "..."}
      ]
    }

    Also accepts:
    - {"action": "..."}
    - [{"action": "..."}, ...]
    - raw JSON string
    - Python-dict-like string
    - Gemini/OpenAI wrapper if accidentally passed through
    """

    def normalize_plan_items(items):
        """
        Normalize planner output into a flat list of executable action dicts.

        Preferred format:
        {
          "plan": [
            {"action": "write_file", ...},
            {"action": "run_shell", ...}
          ]
        }

        Also tolerates accidental nested {"actions": [...]} output.
        """

        if not isinstance(items, list):
            items = [items]

        flat = []

        for item in items:
            if not isinstance(item, dict):
                continue

            # Preferred flat action.
            if "action" in item:
                flat.append(item)

            # Backward tolerance for accidental nested actions.
            elif "actions" in item and isinstance(item["actions"], list):
                for action in item["actions"]:
                    if isinstance(action, dict) and "action" in action:
                        flat.append(action)

        return flat

    if isinstance(raw_input, dict):
        if "plan" in raw_input:
            return normalize_plan_items(raw_input["plan"])

        if "action" in raw_input:
            return [raw_input]

        try:
            content = raw_input["choices"][0]["message"]["content"]
            return extract_plan(content)
        except Exception:
            pass

        try:
            content = raw_input["candidates"][0]["content"]["parts"][0]["text"]
            return extract_plan(content)
        except Exception:
            pass

        print("❌ Dict received but no plan/action found")
        return []

    if isinstance(raw_input, list):
        return normalize_plan_items(raw_input)

    text = str(raw_input).strip()

    # Remove common markdown fences if model accidentally emits them.
    text = text.replace("```json", "").replace("```", "").strip()

    match = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)

    if not match:
        print(f"❌ No JSON-like structure found in output: {text[:200]}...")
        return []

    content = match.group(1)

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        try:
            data = ast.literal_eval(content)
        except Exception as e:
            print("❌ Failed to parse as JSON or Python literal")
            print("Parser error:", e)
            print("Content snippet:", content[:500])
            return []

    return extract_plan(data)