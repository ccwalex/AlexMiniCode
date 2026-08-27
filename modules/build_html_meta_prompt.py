MODULE_METADATA = {
  "name": "build_html_meta_prompt",
  "type": "function",
  "description": "Builds system and user prompts for HTML metadata extraction from raw code.",
  "functions": [
    {
      "name": "build_html_meta_prompt",
      "inputs": {
        "code": "str"
      },
      "outputs": "tuple (system_prompt, user_prompt)"
    }
  ]
}

def build_html_meta_prompt(code: str) -> tuple[str, str]:
    system_prompt = '''You are an expert in analyzing HTML files and generating structured metadata. Generate a MODULE_METADATA dictionary for the provided HTML code following these rules:

MODULE_METADATA Structure:
{
  "name": "document_or_primary_section_name",
  "type": "document | element | form | script | style | template",
  "description": "what this HTML file or section provides",
  "functions": [
    {
      "name": "document_or_section_name",
      "inputs": {
        "input_or_user_action": "field, event, query param, or required data"
      },
      "outputs": "rendered DOM, submitted form data, emitted event, or visual result"
    }
  ]
}

Rules:
- For a full HTML file (contains <html> tag), use type "document".
- MODULE_METADATA["name"] should use file name (if available), document title, root id, or main section id.
- Do not list every small tag. Focus on major sections with id/class, forms, scripts, templates, and interactive regions.
- For forms:
  * inputs should list form fields and their types (e.g., {"username": "text", "password": "password"}).
- For scripts:
  * functions should describe callable JavaScript functions or visible event handlers.
- For static HTML with no inputs (e.g., pure presentation), inputs should be {}.
- Outputs should describe the rendered DOM structure or user-visible result (e.g., "renders user login form").
- Do not invent behavior that is not present in the HTML.

Output Requirements:
- Return a valid JSON object matching the MODULE_METADATA structure exactly.
- If the HTML is a fragment (not a full page), determine the appropriate type (element, form, etc.) and set name accordingly.'''
    
    user_prompt = f"""Here is the HTML code to analyze:\n\n{code}\n\nGenerate the MODULE_METADATA in the specified format. Output must be a valid JSON object."""
    
    return system_prompt, user_prompt