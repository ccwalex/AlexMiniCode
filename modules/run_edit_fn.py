MODULE_METADATA = {
    "name": "run_edit_fn",
    "type": "function",
    "description": "Execute a constrained Python edit function against a CodeEdit-like object.",
    "functions": [
        {
            "name": "run_edit_fn",
            "inputs": {
                "code": "object",
                "edit_fn": "str"
            },
            "outputs": "dict"
        }
    ]
}

def run_edit_fn(code, edit_fn):
    allowed_builtins = {
        "len": len, "range": range, "str": str, "int": int, "float": float,
        "bool": bool, "list": list, "dict": dict, "set": set, "tuple": tuple,
        "enumerate": enumerate, "zip": zip, "min": min, "max": max, "sum": sum,
        "sorted": sorted, "isinstance": isinstance, "print": print,
        "Exception": Exception, "ValueError": ValueError, "TypeError": TypeError,
        "KeyError": KeyError, "IndexError": IndexError
    }
    
    global_env = {"__builtins__": allowed_builtins}
    local_env = {}
    
    try:
        exec(edit_fn, global_env, local_env)
        if "edit" not in local_env or not callable(local_env["edit"]):
            return {"success": False, "code": code, "error": "Function 'edit(code)' not defined in edit_fn."}
            
        result = local_env["edit"](code)
        final_code = result if result is not None else code
        return {"success": True, "code": final_code, "error": None}
    except Exception as e:
        return {"success": False, "code": code, "error": str(e)}
