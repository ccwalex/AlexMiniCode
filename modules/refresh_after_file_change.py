from read_file import read_file
from is_tracked import is_tracked
from infer_code_type import infer_code_type
from meta_caller import meta_caller
from meta_writer import meta_writer
from refresh_registry import refresh_registry

MODULE_METADATA = {
    "name": "refresh_after_file_change",
    "type": "function",
    "description": "Refresh metadata and registry after a successful real file mutation if the changed file belongs to a tracked folder.",
    "functions": [
        {
            "name": "refresh_after_file_change",
            "inputs": {
                "path": "str project-relative path of the changed file",
                "run_state": "RunState object or None for recording metadata refresh result",
                "code_type": "str or None optional code type override"
            },
            "outputs": "dict with success bool, path, tracked bool, metadata and registry refresh status, code_type, reason, and metadata"
        }
    ]
}

def refresh_after_file_change(path, run_state=None, code_type=None):
    result = {
        "success": False,
        "path": path,
        "tracked": False,
        "metadata_generated": False,
        "metadata_written": False,
        "registry_refreshed": False,
        "code_type": code_type,
        "reason": "",
        "metadata": None
    }

    def record(success, reason):
        result["success"] = success
        result["reason"] = reason
        if run_state is not None and hasattr(run_state, "add_metadata_refresh"):
            try:
                run_state.add_metadata_refresh(path, success, reason)
            except Exception:
                pass

    if not path or not isinstance(path, str):
        record(False, "Invalid path provided")
        return result

    try:
        tracked = is_tracked(path)
        result["tracked"] = tracked
    except Exception as e:
        record(False, f"is_tracked raised exception: {str(e)}")
        return result

    if not tracked:
        record(True, "File is not tracked, metadata refresh skipped")
        return result

    try:
        read_success, content = read_file(path)
        if not read_success:
            record(False, f"Failed to read file: {content}")
            return result
    except Exception as e:
        record(False, f"read_file raised exception: {str(e)}")
        return result

    try:
        if result["code_type"] is None:
            result["code_type"] = infer_code_type(path, content)
    except Exception as e:
        record(False, f"infer_code_type raised exception: {str(e)}")
        return result

    try:
        metadata = meta_caller(path, content)
        if not isinstance(metadata, dict):
            record(False, "meta_caller returned non-dict")
            return result
        result["metadata"] = metadata
        result["metadata_generated"] = True
    except Exception as e:
        record(False, f"meta_caller raised exception: {str(e)}")
        return result

    try:
        meta_writer(metadata, path)
        result["metadata_written"] = True
    except Exception as e:
        record(False, f"meta_writer raised exception: {str(e)}")
        return result

    try:
        reg_out = refresh_registry()
        reg_success = True
        if isinstance(reg_out, tuple) and len(reg_out) > 0:
            reg_success = bool(reg_out[0])
        elif isinstance(reg_out, dict):
            reg_success = bool(reg_out.get("success", True))
        elif reg_out is False:
            reg_success = False

        if not reg_success:
            record(False, "refresh_registry indicated failure")
            return result

        result["registry_refreshed"] = True
    except Exception as e:
        record(False, f"refresh_registry raised exception: {str(e)}")
        return result

    record(True, "Metadata and registry refreshed successfully")
    return result

if __name__ == "__main__":
    class FakeRunState:
        def __init__(self):
            self.records = []
        def add_metadata_refresh(self, path, success, reason):
            self.records.append((path, success, reason))

    # Monkeypatch for self-test
    globals()["is_tracked"] = lambda p: p == "code/example.py"
    globals()["read_file"] = lambda p: (True, "print('hello')")
    globals()["infer_code_type"] = lambda p, c: "py"
    globals()["meta_caller"] = lambda p, c: {"name": "example", "type": "function"}
    globals()["meta_writer"] = lambda m, p: None
    globals()["refresh_registry"] = lambda: (True, "ok")

    fake_run_state = FakeRunState()

    res1 = refresh_after_file_change("code/example.py", fake_run_state)
    print("Tracked file test result:", res1)
    assert res1["success"] is True
    assert res1["tracked"] is True
    assert res1["metadata_generated"] is True
    assert res1["metadata_written"] is True
    assert res1["registry_refreshed"] is True
    assert len(fake_run_state.records) == 1

    res2 = refresh_after_file_change("untracked/file.txt", fake_run_state)
    print("Untracked file test result:", res2)
    assert res2["success"] is True
    assert res2["tracked"] is False
    assert len(fake_run_state.records) == 2

    print("REFRESH_AFTER_FILE_CHANGE SELF TEST PASSED")
