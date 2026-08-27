MODULE_METADATA = {
    "name": "group_edit_calls",
    "type": "function",
    "description": "Group /edit calls by file path.",
    "functions": [
        {
            "name": "group_edit_calls",
            "inputs": {
                "api_calls": "list[dict]"
            },
            "outputs": "dict"
        }
    ]
}

def group_edit_calls(api_calls):
    grouped = {}
    for call in api_calls:
        if call.get("url") != "/edit":
            continue

        payload = call.get("payload", {})
        path = payload.get("path")
        edit_fn = payload.get("edit_fn")

        if not path:
            raise ValueError("Edit call missing payload.path")
        if not edit_fn:
            raise ValueError(f"Edit call for {path} missing payload.edit_fn")

        grouped.setdefault(path, []).append(edit_fn)

    return grouped
