import json


MODULE_METADATA = {
    "name": "preview",
    "type": "function",
    "description": "Render an object into a short string preview for logging or display.",
    "functions": [
        {
            "name": "preview",
            "inputs": {
                "obj": "any Python object to preview",
                "max_chars": "int maximum number of characters to return, default 1000"
            },
            "outputs": "str preview text truncated to max_chars"
        }
    ]
}


def preview(obj, max_chars=1000):
    try:
        if isinstance(obj, (dict, list)):
            text = json.dumps(obj, indent=2, ensure_ascii=False)
        else:
            text = str(obj)
    except Exception:
        text = str(obj)

    return text[:max_chars]