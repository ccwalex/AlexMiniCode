MODULE_METADATA = {
    "name": "validate_module_metadata",
    "type": "function",
    "description": "Validate the current Python MODULE_METADATA dictionary format for function/class modules.",
    "functions": [
        {
            "name": "validate_module_metadata",
            "inputs": {
                "meta": "dict containing Python module metadata"
            },
            "outputs": "tuple (bool, str) where bool indicates whether metadata is valid and str describes success or validation error"
        }
    ]
}


def validate_module_metadata(meta):
    required_top_level = ["name", "type", "description", "functions"]

    for key in required_top_level:
        if key not in meta:
            return False, f"metadata missing required key: {key}"

    if meta["type"] not in ["function", "class"]:
        return False, "metadata type must be 'function' or 'class'"

    if not isinstance(meta["name"], str) or not meta["name"].strip():
        return False, "metadata name must be a non-empty string"

    if not isinstance(meta["description"], str) or not meta["description"].strip():
        return False, "metadata description must be a non-empty string"

    if not isinstance(meta["functions"], list):
        return False, "metadata functions must be a list"

    if len(meta["functions"]) == 0:
        return False, "metadata functions must contain at least one function/method entry"

    for fn in meta["functions"]:
        if not isinstance(fn, dict):
            return False, "each function metadata entry must be a dictionary"

        for key in ["name", "inputs", "outputs"]:
            if key not in fn:
                return False, f"function metadata missing key: {key}"

        if not isinstance(fn["name"], str) or not fn["name"].strip():
            return False, "function name must be a non-empty string"

        if not isinstance(fn["inputs"], dict):
            return False, f"inputs for {fn.get('name')} must be a dictionary"

    return True, "metadata valid"