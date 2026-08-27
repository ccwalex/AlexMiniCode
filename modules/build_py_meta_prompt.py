MODULE_METADATA = {
    "name": "build_py_meta_prompt",
    "type": "function",
    "description": "Build system and user prompts for generating Python module metadata in the project's standard MODULE_METADATA-compatible format.",
    "functions": [
        {
            "name": "build_py_meta_prompt",
            "inputs": {
                "code": "str containing Python source code",
                "path": "str optional project-relative file path"
            },
            "outputs": "tuple (system_prompt, user_prompt)"
        }
    ]
}


def build_py_meta_prompt(code, path=""):
    system_prompt = """
You generate metadata for Python source files.

Return ONLY valid JSON.
Do not return markdown.
Do not return explanations.
Do not wrap output in code fences.

The output must match this metadata shape:

{
  "name": "module_name",
  "type": "function or class",
  "description": "what it does",
  "functions": [
    {
      "name": "function_name",
      "inputs": {
        "arg": "type, shape of input"
      },
      "outputs": "type, shape of output"
    }
  ]
}

Focus only on what downstream code needs to import or call.
Do not list imports.
Do not include private helpers unless they are the primary purpose of the file.
"""

    user_prompt = f"""
Generate metadata for this Python file.

Path:
{path}

Rules:
- MODULE_METADATA["name"] must exactly match the primary exported class or function name that downstream code should import.
- If the file exposes one main function, use type "function".
- If the file exposes one main class, use type "class".
- Identify main component by 2 rules: name does not start with _ and is not reused in other functions / classes in the same document
- For classes:
  - functions must include "__init__" if constructor inputs matter.
  - functions must include important public methods.
  - do not flatten nested methods into separate top-level metadata objects.
- For functions:
  - include all meaningful parameters in inputs.
  - include return type / structure in outputs.
- If inputs or outputs are arrays, tensors, matrices, dataframes, or nested lists, describe shape and element type.
- If PyTorch tensors are used, describe tensor shape convention when inferable, e.g. Tensor[batch, channels, height, width].
- If numpy arrays are used, describe ndarray shape when inferable.
- If the function mutates files, registry, memory, global state, or object state, mention the side effect in outputs.
- Do not invent functions, methods, arguments, or outputs.
- Do not include imports.
- Return one JSON object only.

Python source:
{code}
"""

    return system_prompt, user_prompt