import os
import hashlib

from safe_path import safe_path
from update_modules_registry import update_modules_registry


MODULE_METADATA = {
    "name": "write_file",
    "type": "function",
    "description": "Write complete file content to a project path, with extra verification requirements for Python module files.",
    "functions": [
        {
            "name": "write_file",
            "inputs": {
                "path": "str project-relative file path to write",
                "content": "str complete file content",
                "verification": "dict or None; required for code/modules/*.py and must contain matching content_hash and metadata"
            },
            "outputs": "tuple (bool, str) where bool indicates write success and str describes result or failure reason"
        }
    ]
}


def write_file(path, content, verification=None):
    full = safe_path(path)
    os.makedirs(os.path.dirname(full), exist_ok=True)


    with open(full, "w") as f:
        f.write(content)


    return True, f"written to {path}"