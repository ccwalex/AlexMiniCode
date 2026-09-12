MODULE_METADATA = {
    "name": "clean_python_content",
    "type": "function",
    "description": "Deprecated no-op. HTML unescaping is disabled because Python sources may contain HTML and a code verifier already checks writes.",
    "functions": [
        {
            "name": "clean_python_content",
            "inputs": {
                "content": "str containing Python source code"
            },
            "outputs": "str containing the same source, unchanged"
        }
    ]
}


def clean_python_content(content: str) -> str:
    """Return Python source unchanged. HTML artifact cleaning is disabled."""
    return content