from safe_path import safe_path


MODULE_METADATA = {
    "name": "read_file",
    "type": "function",
    "description": "Read a project-relative file through safe_path and return success status with file content or error text.",
    "functions": [
        {
            "name": "read_file",
            "inputs": {
                "path": "str project-relative file path to read"
            },
            "outputs": "tuple (bool, str) where bool indicates read success and str contains file content if successful or error message if failed"
        }
    ]
}


def read_file(path):
    try:
        with open(safe_path(path), "r") as f:
            return True, f.read()

    except Exception as e:
        return False, str(e)