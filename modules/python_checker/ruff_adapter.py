"""Ruff integration adapter."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from python_checker.report import Finding, Severity

RUFF_SELECT = ["F", "E9"]


def run_ruff(path: str, source: str | None = None) -> list[Finding]:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as handle:
        target = path
        if source is not None:
            handle.write(source)
            handle.flush()
            target = handle.name
        else:
            target = str(Path(path).resolve())

        cmd = [
            sys.executable,
            "-m",
            "ruff",
            "check",
            target,
            "--output-format",
            "json",
            "--select",
            ",".join(RUFF_SELECT),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        findings = _parse_ruff_json(proc.stdout, path if source is None else path)
        if source is not None:
            Path(handle.name).unlink(missing_ok=True)
        return findings


def _parse_ruff_json(payload: str, display_path: str) -> list[Finding]:
    if not payload.strip():
        return []
    data = json.loads(payload)
    findings: list[Finding] = []
    for item in data:
        code = item.get("code", "RUFF")
        severity = Severity.ERROR if item.get("severity") == "error" else Severity.WARNING
        location = item.get("location", {})
        findings.append(
            Finding(
                code=code,
                severity=severity,
                message=item.get("message", ""),
                path=display_path,
                line=int(location.get("row", 1)),
                col=int(location.get("column", 1)),
                rule_id=f"ruff.{code}",
            )
        )
    return findings
