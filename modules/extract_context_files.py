MODULE_METADATA = {
    "name": "extract_context_files",
    "type": "function",
    "description": "Extract project-relative file paths listed inside a <context_files> block in the task prompt.",
    "functions": [
        {
            "name": "extract_context_files",
            "inputs": {
                "task": "str task prompt that may contain a <context_files>...</context_files> block"
            },
            "outputs": "list of str file paths extracted from the context_files block, or empty list if absent"
        }
    ]
}


def extract_context_files(task: str):
    start_tag = "<context_files>"
    end_tag = "</context_files>"

    if start_tag not in task or end_tag not in task:
        return []

    block = task.split(start_tag, 1)[1].split(end_tag, 1)[0]

    return [
        line.strip()
        for line in block.splitlines()
        if line.strip()
    ]