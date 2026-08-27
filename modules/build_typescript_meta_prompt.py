MODULE_METADATA = {
  "name": "build_typescript_meta_prompt",
  "type": "function",
  "description": "Builds system and user prompts for TypeScript metadata extraction from raw code.",
  "functions": [
    {
      "name": "build_typescript_meta_prompt",
      "inputs": {
        "code": "str"
      },
      "outputs": "tuple (system_prompt, user_prompt)"
    }
  ]
}

def build_typescript_meta_prompt(code: str) -> tuple[str, str]:
    """
    Build system and user prompts for TypeScript metadata extraction from raw code.
    
    Args:
        code: str - The raw TypeScript source code to analyze

    Returns:
        tuple[str, str] - (system_prompt, user_prompt) for TypeScript metadata extraction
    """
    
    system_prompt = """You are an expert TypeScript developer and code analyzer. Your task is to extract precise metadata from TypeScript source code following strict formatting rules.\n\n**Metadata Format Requirements:**\n- MODULE_METADATA must be a JSON object with exactly these fields: name, type, description, and functions array\n- Focus ONLY on exported or externally callable symbols\n- Do NOT list imports\n- For function exports, MODULE_METADATA["name"] must match the function name\n- For class exports, MODULE_METADATA["name"] must match the class name\n- For classes, include constructor as "constructor" if constructor parameters matter\n- For classes, include important public methods in functions array\n- For arrow functions assigned to const, use the const name\n- For async functions, outputs should mention Promise<...>\n- If inputs/outputs are arrays, define element type and shape (e.g. User[], number[], TensorLike[batch, dim])\n- If inputs/outputs are objects, define important fields\n- Do NOT invent symbols that are not present in code\n- Be precise and concise in descriptions\n\n**Analysis Approach:**\n1. Parse the TypeScript code to identify exported symbols\n2. For each symbol, determine its type (function, class, module, constant, type)\n3. Extract detailed information about inputs and outputs\n4. Write clear, concise descriptions of what each symbol does\n5. Return ONLY the MODULE_METADATA JSON object, no additional text or explanation\n"""

    user_prompt = f"""Please analyze this TypeScript code and extract metadata in the exact JSON format specified above. Return ONLY the MODULE_METADATA object, no additional text.\n\nCode to analyze:\n\n{code}\n"""

    return system_prompt, user_prompt