import sys
import traceback
import json
from infer_code_type import infer_code_type
from build_block_table import build_block_table

MODULE_METADATA = {
    "name": "build_inspection_feedback",
    "type": "function",
    "description": "Build deterministic feedback blocks for successful read-like operations and shell inspection commands, including code block tables for read code files.",
    "functions": [
        {
            "name": "build_inspection_feedback",
            "inputs": {
                "records": "list of read or shell inspection record dictionaries",
                "read_cache": "dict or None mapping paths to file contents",
                "max_chars": "int maximum feedback length"
            },
            "outputs": "dict with success bool, feedback string, and error string or None"
        }
    ]
}

def truncate_text(text, max_chars, tail=False):
    if not text:
        return ""
    text_str = str(text)
    if len(text_str) <= max_chars:
        return text_str
    if tail:
        return "[TRUNCATED]\n...\n" + text_str[-(max_chars - 15):]
    return text_str[:(max_chars - 15)] + "\n...\n[TRUNCATED]"

def escape_opening_tag_text(text):
    if not text:
        return ""
    return str(text).replace("\n", " ").replace("\t", " ").strip()

def is_inspection_command(cmd):
    category = classify_inspection_command(cmd)
    return category != "not_inspection"

def classify_inspection_command(cmd):
    if not cmd:
        return "not_inspection"
    base_cmd = cmd.strip().split()[0] if cmd.strip() else ""
    
    direct_read_cmds = ["cat", "head", "tail", "less"]
    search_cmds = ["grep", "rg", "awk", "sed"]
    listing_cmds = ["ls", "find", "wc", "tree"]
    
    if base_cmd in direct_read_cmds:
        return "direct_file_read"
    if base_cmd in search_cmds:
        if base_cmd == "sed" and "-n" not in cmd:
            return "not_inspection"  # sed without -n is often modifying or unsupported as safe read
        return "search_output"
    if base_cmd in listing_cmds:
        return "listing_output"
        
    return "not_inspection"

def extract_direct_read_paths(cmd):
    if not cmd:
        return []
    # Split off pipe and redirect operators
    for op in ["|", ">", "<", "&&"]:
        cmd = cmd.split(op)[0]
    
    tokens = cmd.strip().split()
    if not tokens:
        return []
    
    paths = []
    # Skip the base command
    for token in tokens[1:]:
        if token.startswith("-"):
            continue
        paths.append(token)
    return paths

def render_read_file_feedback(path, content, source="read", max_content_chars=12000):
    safe_path = escape_opening_tag_text(path)
    truncated_content = truncate_text(content, max_content_chars)
    
    code_type = infer_code_type(path, content)
    supported_types = ["py", "python", "html", "react", "react_tsx", "ts", "node_typescript"]
    
    table_text = ""
    if code_type in supported_types:
        try:
            block_table = build_block_table(
                source=content, 
                path=path, 
                code_type=code_type, 
                max_depth=10, 
                min_chunk_lines=1
            )
            if block_table:
                rows = ["id | type | name | start_line | end_line | parent | depth | vars_defined"]
                for b in block_table:
                    row = f"{b.get('id', '')} | {b.get('type', '')} | {b.get('name', '')} | {b.get('start_line', '')} | {b.get('end_line', '')} | {b.get('parent', '')} | {b.get('depth', '')} | {b.get('vars_defined', [])}"
                    rows.append(row)
                table_text = "\n".join(rows)
        except Exception as e:
            table_text = f"[BLOCK TABLE ERROR: {str(e)}]"
            
    output_lines = []
    output_lines.append(f"<read {safe_path}>")
    output_lines.append(truncated_content)
    if table_text:
        output_lines.append("")
        output_lines.append("<table>")
        output_lines.append(table_text)
        output_lines.append("</table>")
    output_lines.append("</read>")
    
    return "\n".join(output_lines)

def render_shell_inspection_feedback(cmd, output, max_output_chars=6000):
    safe_cmd = escape_opening_tag_text(cmd)
    truncated_output = truncate_text(output, max_output_chars, tail=True)
    
    output_lines = []
    output_lines.append(f"<output {safe_cmd}>")
    if truncated_output:
        output_lines.append(truncated_output)
    output_lines.append("</output>")
    
    return "\n".join(output_lines)

def build_inspection_feedback(records, read_cache=None, max_chars=12000):
    try:
        if read_cache is None:
            read_cache = {}
            
        blocks = []
        for record in records:
            kind = record.get("kind")
            if kind == "read":
                path = record.get("path", "")
                content = record.get("content", "")
                if not content and path in read_cache:
                    content = read_cache[path]
                blocks.append(render_read_file_feedback(path, content))
            
            elif kind == "shell":
                cmd = record.get("cmd", "")
                output = record.get("output", "")
                
                if classify_inspection_command(cmd) == "direct_file_read":
                    paths = extract_direct_read_paths(cmd)
                    handled_by_cache = False
                    if paths:
                        for p in paths:
                            if p in read_cache:
                                blocks.append(render_read_file_feedback(p, read_cache[p], source="shell"))
                                handled_by_cache = True
                    if not handled_by_cache:
                        blocks.append(render_shell_inspection_feedback(cmd, output))
                else:
                    blocks.append(render_shell_inspection_feedback(cmd, output))
                    
        final_feedback = "\n\n".join(blocks)
        final_feedback = truncate_text(final_feedback, max_chars, tail=True)
        
        return {
            "success": True,
            "feedback": final_feedback,
            "error": None
        }
    except Exception as e:
        return {
            "success": False,
            "feedback": "",
            "error": traceback.format_exc()
        }

if __name__ == "__main__":
    fake_py = """\
class Dummy:
    def foo(self):
        return 1
    
    def bar(self):
        return 2
"""
    cache = {"code/example.py": fake_py}
    recs = [
        {"kind": "read", "path": "code/example.py", "content": fake_py},
        {"kind": "shell", "cmd": "head -20 code/example.py", "success": True, "output": fake_py},
        {"kind": "shell", "cmd": "grep -R func code", "success": True, "output": "code/example.py: def foo(self):"}
    ]
    
    res = build_inspection_feedback(recs, cache, 20000)
    assert res["success"] is True, "Expected success True"
    fb = res["feedback"]
    
    assert "<read code/example.py>" in fb, "Missing read start tag"
    assert "</read>" in fb, "Missing read end tag"
    assert "<table>" in fb, "Missing table start tag"
    assert "</table>" in fb, "Missing table end tag"
    assert "<output grep -R func code>" in fb, "Missing output start tag"
    assert "</output>" in fb, "Missing output end tag"
    
    assert classify_inspection_command("cat code/example.py") == "direct_file_read"
    assert classify_inspection_command("grep -R foo code") == "search_output"
    assert is_inspection_command("python code/train.py") is False
    
    print("\nFEEDBACK OUTPUT:")
    print(fb)
    print("\nBUILD_INSPECTION_FEEDBACK SELF TEST PASSED")
