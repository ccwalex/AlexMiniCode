MODULE_METADATA = {
  "name": "build_react_tsx_meta_prompt",
  "type": "function",
  "description": "Builds system and user prompts for React TSX metadata extraction from raw code.",
  "functions": [
    {
      "name": "build_react_tsx_meta_prompt",
      "inputs": {
        "code": "str"
      },
      "outputs": "tuple (system_prompt, user_prompt)"
    }
  ]
}


def build_react_tsx_meta_prompt(code: str) -> tuple[str, str]:
    """
    Build system and user prompts for React TSX metadata extraction from raw code.
    
    Args:
        code: str - Raw React TSX source code

    Returns:
        tuple[str, str] - (system_prompt, user_prompt) for metadata extraction
    """
    system_prompt = """You are an expert React TSX metadata extractor. \
Your task is to analyze React TSX code and generate metadata in the following JSON format:

MODULE_METADATA = {
  "name": "component_or_primary_export_name",
  "type": "component | hook | function | module",
  "description": "what UI/functionality it provides",
  "functions": [
    {
      "name": "component_or_callable_name",
      "inputs": {
        "prop_or_arg": "TypeScript type, shape, or role"
      },
      "outputs": "JSX.Element | ReactNode | return type"
    }
  ]
}

Rules:
- For React components, MODULE_METADATA["name"] must match the component name.
- For components, the primary function entry describes props as inputs and JSX.Element/ReactNode as output.
- Do not list imports.
- Include custom hooks if exported or externally reused.
- Include important exported helper functions if present.
- Do not list internal handlers unless exported or central to usage.
- For array props, define element type and shape.
- For callback props, define function signature.
- For state setters passed as props, define input type.
- For components with no props, inputs should be {}.
- Do not invent props or outputs not visible in code.

Return ONLY the MODULE_METADATA dictionary as valid JSON."""

    user_prompt = f"""React TSX Code:\ntsx\n{code}\n\n\nGenerate metadata for this React TSX file in the exact MODULE_METADATA format specified above.\nReturn ONLY the JSON object, no additional text."""

    return system_prompt, user_prompt