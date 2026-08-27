from code_edit_core import CodeEdit
from run_edit_fn import run_edit_fn

MODULE_METADATA = {
    "name": "edit_transaction",
    "type": "function",
    "description": "Run multiple edit functions sequentially over a CodeEdit instance and return reconstructed source.",
    "functions": [
        {
            "name": "edit_transaction",
            "inputs": {
                "source": "str",
                "block_table": "list[dict]",
                "edit_fns": "list[str]"
            },
            "outputs": "dict"
        }
    ]
}

def edit_transaction(source, block_table, edit_fns):
    code = CodeEdit(source, block_table)
    errors = []
    
    for fn_str in edit_fns:
        try:
            res = run_edit_fn(code, fn_str)
        except Exception as e:
            errors.append(f"Run edit function {fn_str} raised exception: {str(e)}")
            return {
                "success": False,
                "original_source": source,
                "reconstructed_source": None,
                "mutation_log": code.mutation_log,
                "errors": errors
            }
        if not res.get("success", False):
            error_msg = res.get("error", "Unknown error during edit function")
            errors.append(error_msg)
            return {
                "success": False,
                "original_source": source,
                "reconstructed_source": None,
                "mutation_log": code.mutation_log,
                "errors": errors
            }
        code = res.get("code")
        if code is None:
            errors.append(f"Edit function {fn_str} did not return 'code'")
            return {
                "success": False,
                "original_source": source,
                "reconstructed_source": None,
                "mutation_log": code.mutation_log if code else [],
                "errors": errors
            }

    try:
        reconstructed = code.reconstruct()
        return {
            "success": True,
            "original_source": source,
            "reconstructed_source": reconstructed,
            "mutation_log": code.mutation_log,
            "errors": errors
        }
    except Exception as e:
        errors.append(f"Reconstruction failed: {str(e)}")
        return {
            "success": False,
            "original_source": source,
            "reconstructed_source": None,
            "mutation_log": code.mutation_log,
            "errors": errors
        }
