import ast
MODULE_METADATA = {
  "name": "extract_module_metadata_from_content",
  "type": "function",
  "description": "extract metadata from a python module with code and metadata",
  "functions": [
    {
      "name": "extract_module_metadata_from_content",
      "inputs": {
        "content": "body of the code"
      },
      "outputs": "tuple (meta, str) where meta is metadata if successful, else None. str is description of parse error or None if successful"
    }
  ]
}

def extract_module_metadata_from_content(content):

    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        return None, f"python syntax error before metadata extraction: {e}"

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "MODULE_METADATA":
                    try:
                        meta = ast.literal_eval(node.value)
                    except Exception as e:
                        return None, f"MODULE_METADATA parse error: {e}"

                    if not isinstance(meta, dict):
                        return None, "MODULE_METADATA must be a dictionary"

                    return meta, None

    return None, "missing top-level MODULE_METADATA dictionary"