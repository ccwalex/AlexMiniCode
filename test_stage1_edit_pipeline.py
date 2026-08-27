import sys
import os
import json
sys.path.insert(0, os.path.abspath('code/modules'))

from edit_transaction import edit_transaction
from build_block_table import build_block_table
from group_edit_calls import group_edit_calls
from edit_file import edit_file
from write_file import write_file
from read_file import read_file

def test_pipeline():
    source = '''def func1():
    print("Hello")
    
def func2():
    print("World")
'''
    block_table = build_block_table(source, path="test.py", code_type="py")
    
    edit_fn = '''def edit(code):
    for block in code.blocks():
        text = code.get(block["id"])
        if "def func1" in text and "def func2" not in text:
            code.replace_text(block["id"], "Hello", "Goodbye", all=False)
            break
    return code
'''

    trans_res = edit_transaction(source, block_table, [edit_fn])
    if not trans_res.get("success"):
        print("edit_transaction failed:", json.dumps(trans_res, indent=2))
        sys.exit(1)
        
    reconstructed_source = trans_res["reconstructed_source"]
    assert reconstructed_source is not None, "reconstructed_source is None"
    assert "Goodbye" in reconstructed_source, "Goodbye not in reconstructed_source"
    assert "Hello" not in reconstructed_source, "Hello still in reconstructed_source"
    assert len(trans_res["mutation_log"]) > 0, "mutation_log is empty"

    api_calls = [
        {"url": "/edit", "payload": {"path": "test.py", "edit_fn": edit_fn}},
        {"url": "/edit", "payload": {"path": "test.py", "edit_fn": "def edit2(code): return code"}}
    ]
    grouped = group_edit_calls(api_calls)
    assert len(grouped["test.py"]) == 2, "group_edit_calls failed to group properly"
    
    try:
        group_edit_calls([{"url": "/edit", "payload": {"path": "test.py"}}])
        assert False, "Should raise ValueError for missing edit_fn"
    except ValueError:
        pass

    try:
        group_edit_calls([{"url": "/edit", "payload": {"edit_fn": "fn"}}])
        assert False, "Should raise ValueError for missing path"
    except ValueError:
        pass

    test_path = "code/test_temp_stage1.py"
    write_success, _ = write_file(test_path, source, None)
    assert write_success, "write_file failed for test file"
    
    edit_res = edit_file(test_path, [edit_fn], code_type="py")
    if not edit_res.get("success"):
        print("edit_file failed:", json.dumps(edit_res, indent=2))
        sys.exit(1)
    
    read_success, final_content = read_file(test_path)
    assert read_success, "read_file failed for test file"
    assert "Goodbye" in final_content, "edit_file didn't correctly modify the file"
    
    print("STAGE 1 EDIT PIPELINE SMOKE TEST PASSED")

if __name__ == "__main__":
    test_pipeline()
