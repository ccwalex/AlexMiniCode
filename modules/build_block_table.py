from infer_code_type import infer_code_type
from build_python_block_table import build_python_block_table
from build_html_block_table import build_html_block_table
from build_node_typescript_block_table import build_node_typescript_block_table
from build_react_tsx_block_table import build_react_tsx_block_table

MODULE_METADATA = {
    "name": "build_block_table",
    "type": "function",
    "description": "Raw block table dispatcher for backend use.",
    "functions": [
        {
            "name": "build_block_table",
            "inputs": {
                "source": "str",
                "path": "str",
                "code_type": "str | None",
                "max_depth": "int",
                "min_chunk_lines": "int"
            },
            "outputs": "list[dict]"
        }
    ]
}

def build_block_table(source, path="", code_type=None, max_depth=10, min_chunk_lines=1):
    if code_type is None:
        code_type = infer_code_type(path, source)
        
    c_type = str(code_type).lower() if code_type else "py"
    
    if c_type in ["html"]:
        return build_html_block_table(source, path, max_depth, min_chunk_lines)
    elif c_type in ["react", "tsx", "jsx"]:
        return build_react_tsx_block_table(source, path, max_depth, min_chunk_lines)
    elif c_type in ["ts", "typescript", "node"]:
        return build_node_typescript_block_table(source, path, max_depth, min_chunk_lines)
    else:
        return build_python_block_table(source, path, max_depth, min_chunk_lines)
