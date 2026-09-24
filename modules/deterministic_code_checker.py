MODULE_METADATA = {
    "name": "deterministic_code_checker",
    "type": "function",
    "description": "Deterministic pre-write/pre-edit checks for Python via vendored python_checker.",
    "functions": [
        {
            "name": "deterministic_code_check",
            "inputs": {
                "path": "str project-relative file path",
                "content": "str proposed file content",
            },
            "outputs": "dict with approved, escalate, status, reason, checks, and findings",
        }
    ],
}

def _get_check_source():
    try:
        from python_checker import check_source
        return check_source
    except ImportError as exc:
        raise ImportError(
            "Vendored python_checker failed to load. Install dependencies: "
            "pip install -r requirements.txt"
        ) from exc


def _serialize_findings(result):
    if result is None:
        return []
    findings = getattr(result, "findings", None) or []
    serialized = []
    for finding in findings:
        if hasattr(finding, "to_dict"):
            serialized.append(finding.to_dict())
        elif isinstance(finding, dict):
            serialized.append(finding)
    return serialized


def _format_checker_reason(status, findings):
    if not findings:
        return f"python_checker status: {status}"
    parts = []
    for finding in findings[:5]:
        if isinstance(finding, dict):
            line = finding.get("line", "?")
            code = finding.get("code", "")
            message = finding.get("message", "")
            parts.append(f"line {line} [{code}]: {message}")
        else:
            parts.append(str(finding))
    suffix = ""
    if len(findings) > 5:
        suffix = f" (+{len(findings) - 5} more)"
    return f"python_checker {status}: " + "; ".join(parts) + suffix


def _reject(status, reason, checks, findings=None, escalate=False):
    return {
        "approved": False,
        "escalate": bool(escalate),
        "status": status,
        "reason": reason,
        "checks": checks,
        "findings": findings or [],
    }


def _approve(status, reason, checks, findings=None):
    return {
        "approved": True,
        "escalate": False,
        "status": status,
        "reason": reason,
        "checks": checks,
        "findings": findings or [],
    }


def deterministic_code_check(path, content):
    if not isinstance(path, str) or not path.strip():
        return _reject("error", "Path must be a non-empty string.", [])

    if not isinstance(content, str) or not content.strip():
        return _reject("error", "Content must be a non-empty string.", [])

    if not path.endswith(".py"):
        return _approve("skipped", "Non-Python file; deterministic checker skipped.", [])

    checks = ["python_checker"]
    try:
        check_source = _get_check_source()
    except ImportError as exc:
        return _reject("uncovered", str(exc), checks, escalate=True)

    result = check_source(content, path=path, ruff=True)
    status = getattr(getattr(result, "status", None), "value", None) or str(
        getattr(result, "status", "ok")
    )
    findings = _serialize_findings(result)

    if status == "error":
        return _reject(
            "error",
            _format_checker_reason(status, findings),
            checks,
            findings=findings,
        )

    if status == "uncovered":
        return _reject(
            "uncovered",
            _format_checker_reason(status, findings),
            checks,
            findings=findings,
            escalate=True,
        )

    return _approve(
        "ok",
        "Passed python_checker.",
        checks,
        findings=findings,
    )


if __name__ == "__main__":
    import sys
    from unittest.mock import patch

    _self = sys.modules[__name__]

    valid_module = """def dummy_func():
    pass
"""

    class _FakeStatus:
        def __init__(self, value):
            self.value = value

    class _FakeResult:
        def __init__(self, status, findings=None):
            self.status = _FakeStatus(status)
            self.findings = findings or []

    def _fake_ok(source, path="<string>", **kwargs):
        return _FakeResult("ok")

    def _fake_error(source, path="<string>", **kwargs):
        return _FakeResult(
            "error",
            [{"line": 1, "code": "SYNTAX_ERROR", "message": "invalid syntax"}],
        )

    def _fake_uncovered(source, path="<string>", **kwargs):
        return _FakeResult(
            "uncovered",
            [{"line": 3, "code": "UNCOVERED_CALL", "message": "unknown API"}],
        )

    with patch.object(_self, "_get_check_source", return_value=_fake_ok):
        ok = deterministic_code_check("code/modules/example.py", valid_module)
        assert ok["approved"] is True, ok
        assert ok["status"] == "ok", ok
        assert ok["escalate"] is False, ok

        module_ok = deterministic_code_check("code/modules/missing.py", valid_module)
        assert module_ok["approved"] is True, module_ok

        non_module = deterministic_code_check("code/example.py", "def dummy_func():\n    pass\n")
        assert non_module["approved"] is True, non_module

    with patch.object(_self, "_get_check_source", return_value=_fake_error):
        bad = deterministic_code_check("code/modules/bad.py", "def foo(\n")
        assert bad["approved"] is False, bad
        assert bad["status"] == "error", bad
        assert bad["escalate"] is False, bad

    with patch.object(_self, "_get_check_source", return_value=_fake_uncovered):
        ambiguous = deterministic_code_check("code/example.py", "x = mystery()\n")
        assert ambiguous["approved"] is False, ambiguous
        assert ambiguous["status"] == "uncovered", ambiguous
        assert ambiguous["escalate"] is True, ambiguous

    def _raise_import_error():
        raise ImportError("missing PyYAML")

    with patch.object(_self, "_get_check_source", side_effect=_raise_import_error):
        missing_pkg = deterministic_code_check("code/example.py", "x = 1\n")
        assert missing_pkg["approved"] is False, missing_pkg
        assert missing_pkg["escalate"] is True, missing_pkg

    check_source = _get_check_source()
    if check_source is not None:
        live_ok = deterministic_code_check("code/modules/example.py", valid_module)
        assert live_ok["approved"] is True, live_ok

        live_bad = deterministic_code_check("code/modules/bad.py", "def foo(\n")
        assert live_bad["approved"] is False, live_bad
        assert live_bad["status"] == "error", live_bad

    print("DETERMINISTIC_CODE_CHECKER SELF TEST PASSED")
