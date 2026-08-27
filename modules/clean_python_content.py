import html


MODULE_METADATA = {
    "name": "clean_python_content",
    "type": "function",
    "description": "Clean common HTML escape artifacts from Python source code.",
    "functions": [
        {
            "name": "clean_python_content",
            "inputs": {
                "content": "str containing Python source code"
            },
            "outputs": "str containing cleaned Python source code"
        }
    ]
}


def clean_python_content(content: str) -> str:
    """
    Clean common HTML escape artifacts in Python code.
    """

    original = content

    content = html.unescape(content)

    if content != original:
        print("⚠️ cleaned HTML artifacts in Python code")

    return content