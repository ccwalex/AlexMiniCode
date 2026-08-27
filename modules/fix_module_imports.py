MODULE_METADATA = {
    "name": "fix_module_imports",
    "type": "function",
    "description": "Normalize imports inside code/modules so modules import one another directly without the modules. prefix.",
    "functions": [
        {
            "name": "fix_module_imports",
            "inputs": {
                "modules_dir": "str path to code/modules directory"
            },
            "outputs": "dict containing modified files and counts"
        }
    ]
}

import os
import re


def fix_module_imports(modules_dir="code/modules"):
    """
    Convert:

        from modules.foo import bar

    into:

        from foo import bar

    for files located inside code/modules.

    This follows the project convention:

    - code/modules/*.py use direct imports
        from foo import bar

    - code/scripts/*.py use package imports
        from modules.foo import bar
    """

    if not os.path.isdir(modules_dir):
        raise FileNotFoundError(
            f"Modules directory not found: {modules_dir}"
        )

    module_names = {
        filename[:-3]
        for filename in os.listdir(modules_dir)
        if filename.endswith(".py")
    }

    changed_files = []

    pattern = re.compile(
        r"^from\s+modules\.([A-Za-z0-9_]+)\s+import\s+(.+)$"
    )

    for filename in os.listdir(modules_dir):

        if not filename.endswith(".py"):
            continue

        path = os.path.join(modules_dir, filename)

        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        changed = False
        new_lines = []

        for line in lines:

            match = pattern.match(line.strip())

            if match:

                module_name = match.group(1)

                if module_name in module_names:

                    new_lines.append(
                        f"from {module_name} import {match.group(2)}\n"
                    )

                    changed = True
                    continue

            new_lines.append(line)

        if changed:

            with open(path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)

            changed_files.append(filename)

    return {
        "success": True,
        "modified_count": len(changed_files),
        "modified_files": changed_files
    }