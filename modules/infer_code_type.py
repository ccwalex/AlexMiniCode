import os
import re

MODULE_METADATA = {
  "name": "infer_code_type",
  "type": "function",
  "description": "Infers the type of code from file path and content. Supported types: 'py', 'html', 'react', 'ts'. Returns None if code type is ambiguous or none of the types match.",
  "functions": [
    {
      "name": "infer_code_type",
      "inputs": {
        "path": "str",
        "content": "str | None"
      },
      "outputs": "str | None"
    }
  ]
}

def infer_code_type(path: str, content: str | None = None) -> str | None:
    if not path:
        return None

    path_lower = path.lower()
    basename = os.path.basename(path_lower)

    # Extension-based decisions should have priority.
    # TSX/JSX are React-like files for this agent's block-table/metadata system.
    if path_lower.endswith(".py"):
        return "py"

    if path_lower.endswith((".html", ".htm")):
        return "html"

    if path_lower.endswith((".tsx", ".jsx")):
        return "react"

    if path_lower.endswith((".ts", ".js", ".mjs", ".cjs")):
        return "ts"

    ts_config_files = {
        "package.json",
        "tsconfig.json",
        "vite.config.ts",
        "vite.config.js",
        "webpack.config.js",
        "next.config.js",
        "tailwind.config.js",
    }

    if basename in ts_config_files:
        return "ts"

    if content:
        # React-ish content indicators.
        if re.search(r"\bimport\s+React\b|\bfrom\s+['\"]react['\"]", content):
            return "react"

        if re.search(r"<[A-Z][A-Za-z0-9]*[\s>/]", content):
            return "react"

        # Node/TS indicators.
        if re.search(r"\bimport\s+(?:\{[\s\S]*?\}|\*\s+as\s+\w+|\w+)\s+from\s+['\"]", content):
            return "ts"

        if "export function" in content:
            return "ts"

        if "module.exports" in content:
            return "ts"

        if "require(" in content:
            return "ts"

        if re.search(r"\binterface\b", content):
            return "ts"

        if re.search(r"\btype\s+\w+\s*=", content):
            return "ts"

        if re.search(r"\bconst\s+\w+\s*=", content):
            return "ts"

    return None