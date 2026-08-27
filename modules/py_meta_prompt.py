MODULE_METADATA = {
  "name": "build_py_meta_prompt",
  "type": "function",
  "description": "Builds system and user prompts for Python metadata extraction from raw code.",
  "functions": [
    {
      "name": "build_py_meta_prompt",
      "inputs": { "code": "str" },
      "outputs": "tuple (system_prompt, user_prompt)"
    }
  ]
}

def build_py_meta_prompt(code: str) -> tuple[str, str]:
    system_prompt = """You are an expert Python metadata extractor. Generate metadata JSON for the provided code following these rules:

Rules:
1. name: Exact primary exported class or function name.
2. type: "class" or "function".
3. description: Concise summary of the module's purpose.
4. functions: List of all class methods or top-level functions with:
   - name: Function/method name.
   - inputs: Dict of {argument: type/description}.
   - outputs: Return type/description.
   - description: Brief purpose.

Example (class):
{
  "name": "CFG",
  "type": "class",
  "description": "Central configuration class storing default runtime options...",
  "functions": [
    {
      "name": "get_timeout",
      "inputs": {
        "name": "str",
        "default": "int | None"
      },
      "outputs": "int",
      "description": "Return timeout value for the given timeout key."
    }
  ]
}

Example (function):
{
  "name": "refresh_modules_registry",
  "type": "function",
  "description": "Rebuild the Python module registry...",
  "functions": [
    {
      "name": "refresh_modules_registry",
      "inputs": {
        "modules_dir": "str",
        "registry_path": "str",
        "strict": "bool"
      },
      "outputs": "tuple (bool, str)",
      "description": "Rebuild the Python module registry..."
    }
  ]
}

Return ONLY valid JSON matching this structure with no additional text."""

    user_prompt = f"""Generate metadata for this Python code:

{code}

Return ONLY the metadata JSON object following the exact structure shown in the examples."""

    return (system_prompt, user_prompt)