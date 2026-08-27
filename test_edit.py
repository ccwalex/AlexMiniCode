import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'modules'))
import json
from modules.build_python_block_table import build_python_block_table
from modules.edit_transaction import edit_transaction
from modules.verify_edit import verify_edit
from modules.edit_file import edit_file
from modules.write_file import write_file
from modules.read_file import read_file

def main():
    source = '''
def func1():
    print("Hello")


def func2():
    print("World")
'''
    print("1. Testing edit_transaction and verify_edit...")
    block_table = build_python_block_table(source, "dummy.py", 10, 1)
    
    edit_fn = '''
def edit(code):
    for block in code.blocks():
        if "func1" in block["content"]:
            code.replace_text(block["id"], "Hello", "Goodbye", False)
    return code
'''
    trans_res = edit_transaction(source, block_table, [edit_fn])
    print("Transaction Success:", trans_res["success"])
    reconstructed = trans_res.get("reconstructed_source")
    print("Mutation Log:", json.dumps(trans_res.get("mutation_log", []), indent=2))
    
    if reconstructed:
        verify_res = verify_edit("dummy.py", source, reconstructed, trans_res["mutation_log"], "py")
        print("Verify Edit Approved:", verify_res["approved"])
        print("Verify Edit Reason:", verify_res["reason"])

    print("\n2. Testing edit_file on a temporary file...")
    temp_path = "code/test_temp.py"
    write_success, msg = write_file(temp_path, source, None)
    if not write_success:
        print("Failed to write temp file:", msg)
        return
        
    edit_res = edit_file(temp_path, [edit_fn])
    print("Edit File Success:", edit_res["success"])
    print("Edit File Reason:", edit_res.get("reason"))
    print("Edit File Mutation Log:", json.dumps(edit_res.get("mutation_log", []), indent=2))
    
    success, final_content = read_file(temp_path)
    if success:
        print("Final Content:\n" + final_content)
    else:
        print("Could not read final content")

if __name__ == "__main__":
    main()
