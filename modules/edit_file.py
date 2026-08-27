from read_file import read_file
from write_file import write_file
from infer_code_type import infer_code_type
from edit_transaction import edit_transaction
from verify_edit import verify_edit
from build_block_table import build_block_table
from repair_write_step import repair_write_step

MODULE_METADATA = {
    "name": "edit_file",
    "type": "function",
    "description": "Apply structured edit pipeline to a file.",
    "functions": [
        {
            "name": "edit_file",
            "inputs": {
                "path": "str",
                "edit_fns": "list[str]",
                "code_type": "str | None"
            },
            "outputs": "dict"
        }
    ]
}
def validate_block_table(source: str, block_table: list[dict]) -> list[dict]:
    lines = source.splitlines()
    line_count = len(lines)

    seen = set()

    for block in block_table:
        block_id = block.get("id")
        start_line = block.get("start_line")
        end_line = block.get("end_line")

        if not block_id:
            raise ValueError(f"Block missing id: {block}")

        if block_id in seen:
            raise ValueError(f"Duplicate block id: {block_id}")

        seen.add(block_id)

        if not isinstance(start_line, int) or not isinstance(end_line, int):
            raise ValueError(f"Block {block_id} has non-int line range: {block}")

        if start_line < 1:
            raise ValueError(f"Block {block_id} start_line < 1: {start_line}")

        if end_line < start_line:
            raise ValueError(
                f"Block {block_id} end_line < start_line: "
                f"{start_line} > {end_line}"
            )

        if end_line > line_count:
            raise ValueError(
                f"Block {block_id} line range exceeds source length: "
                f"start_line={start_line}, end_line={end_line}, "
                f"source_line_count={line_count}"
            )

    return block_table
def edit_file(path, edit_fns, code_type=None):
    success, result = read_file(path)
    if not success:
        return {
            "success": False,
            "path": path,
            "original_source": None,
            "reconstructed_source": None,
            "mutation_log": [],
            "reason": f"Failed to read file: {result}"
        }
    
    source = result
    
    if code_type is None:
        code_type = infer_code_type(path, source)
        
    block_table = build_block_table(source, path, code_type, 10, 1)
    block_table = validate_block_table(source, block_table)
    trans_res = edit_transaction(source, block_table, edit_fns)
    if not trans_res["success"]:
        return {
            "success": False,
            "path": path,
            "original_source": source,
            "reconstructed_source": None,
            "mutation_log": trans_res.get("mutation_log", []),
            "reason": "Edit transaction failed: " + "; ".join(trans_res.get("errors", []))
        }
        
    reconstructed = trans_res["reconstructed_source"]
    mutation_log = trans_res["mutation_log"]
    
    c_type = str(code_type).lower() if code_type else "py"
    verify_res = verify_edit(path, source, reconstructed, mutation_log, code_type=c_type)
    if not verify_res["approved"]:
        repair_attempt = 0
        content = reconstructed
        reason = verify_res.get("reason", "Edit content verification failed.")
    
        while repair_attempt < 3:
            repair_attempt += 1
    
            repair = repair_write_step(
                
                    path,
                   content,
                
                reason,
            )
    
            if not isinstance(repair, dict) or not repair.get("success"):
                break
    
            content = repair["content"]
    
            verify_res = verify_edit(
                path,
                source,
                content,
                mutation_log=[],
                code_type=c_type,
                use_llm=True,
            )
    
            if verify_res.get("approved"):
                reconstructed = verify_res.get("content", content)
                break
    
            reason = verify_res.get("reason", reason)
    
        if not verify_res.get("approved"):
            return {
                "success": False,
                "path": path,
                "original_source": source,
                "reconstructed_source": reconstructed,
                "mutation_log": mutation_log,
                "reason": f"Verification failed after repair attempts: {reason}",
            }
            
    write_success, write_msg = write_file(path, reconstructed, None)
    if not write_success:
        return {
            "success": False,
            "path": path,
            "original_source": source,
            "reconstructed_source": reconstructed,
            "mutation_log": mutation_log,
            "reason": f"Write failed: {write_msg}"
        }
        
    return {
        "success": True,
        "path": path,
        "original_source": source,
        "reconstructed_source": reconstructed,
        "mutation_log": mutation_log,
        "reason": "Edit applied successfully."
    }
