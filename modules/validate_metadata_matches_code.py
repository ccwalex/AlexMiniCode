import ast

MODULE_METADATA = {
  "name": "validate_metadata_matches_code",
  "type": "function",
  "description": "validate python module metadata name",
  "functions": [
    {
      "name": "validate_metadata_matches_code",
      "inputs": {
        "meta": "metadata of the code",
        "content": "body of the code"
      },
      "outputs": "tuple (bool, str) where bool is whether name matches and str is description of metadata error or success"
    }
  ]
}

def validate_metadata_matches_code(meta, content):
    def get_top_level_symbols(content):
        tree = ast.parse(content)
    
        classes = []
        functions = []
    
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                classes.append(node.name)
            elif isinstance(node, ast.FunctionDef):
                functions.append(node.name)
    
        return classes, functions
    classes, functions = get_top_level_symbols(content)

    name = meta.get("name")
    typ = meta.get("type")

    if typ == "class":
        if name not in classes:
            return False, (
                f"metadata name '{name}' does not match any top-level class. "
                f"Found classes: {classes}"
            )

    elif typ == "function":
        if name not in functions:
            return False, (
                f"metadata name '{name}' does not match any top-level function. "
                f"Found functions: {functions}"
            )

    return True, "metadata matches code"