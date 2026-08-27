MODULE_METADATA = {
  "name": "generate_table",
  "type": "function",
  "description": "Generates code tables for a list of file paths by reading each file, inferring its code type, building a block table, and formatting the output with the file content and table enclosed in XML-like tags.",
  "functions": [
    {
      "name": "generate_table",
      "inputs": {
        "file_paths": "list[str] list of file paths to process",
        "max_depth": "int maximum depth for block table generation, default 10",
        "min_chunk_lines": "int minimum number of lines per chunk, default 1"
      },
      "outputs": "str formatted string containing each file's content and its block table wrapped in XML-like tags"
    }
  ]
}

import json
from modules.read_file import read_file
from modules.infer_code_type import infer_code_type
from modules.build_python_block_table import build_python_block_table
from modules.build_html_block_table import build_html_block_table
from modules.build_node_typescript_block_table import build_node_typescript_block_table
from modules.build_react_tsx_block_table import build_react_tsx_block_table

def generate_table(file_paths, max_depth=10, min_chunk_lines=1):
    output_parts = []
    for file_path in file_paths:
        success, content = read_file(file_path)
        if not success:
            continue

        code_type = infer_code_type(file_path, content)
        if code_type is None:
            continue

        if code_type == 'py':
            table = build_python_block_table(content, file_path, max_depth, min_chunk_lines)
        elif code_type == 'html':
            table = build_html_block_table(content, file_path, max_depth, min_chunk_lines)
        elif code_type == 'ts':
            table = build_node_typescript_block_table(content, file_path, max_depth, min_chunk_lines)
        elif code_type == 'react':
            table = build_react_tsx_block_table(content, file_path, max_depth, min_chunk_lines)
        else:
            continue

        table_str = json.dumps(table, indent=2)
        file_section = f"<{file_path} content>\n{content}\n\n<code table>\n{table_str}\n</code table>\n</{file_path} content>"
        output_parts.append(file_section)

    return '\n\n'.join(output_parts)