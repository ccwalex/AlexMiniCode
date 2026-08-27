from modules.code_edit_core import CodeEdit

source = """def fn_1():
    print("hello")

def fn_2():
    print("world")
"""

block_table = [
    {"id": "fn_1", "start_line": 1, "end_line": 2},
    {"id": "fn_2", "start_line": 4, "end_line": 5}
]

editor = CodeEdit(source, block_table)

editor.replace_text("fn_1", "hello", "hi")
editor.insert_after("fn_1", "\n# comment\n")
editor.delete("fn_2")

print("Mutation Log:")
for log in editor.mutation_log:
    print(log)

reconstructed = editor.reconstruct()
print("\nReconstructed Source:")
print(reconstructed)

assert "hi" in reconstructed
assert "world" not in reconstructed
assert "# comment" in reconstructed
print("\nTests passed.")
