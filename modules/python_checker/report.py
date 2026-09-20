"""Finding schema and result aggregation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    UNCOVERED = "uncovered"


class Status(str, Enum):
    OK = "ok"
    ERROR = "error"
    UNCOVERED = "uncovered"


@dataclass
class Finding:
    code: str
    severity: Severity
    message: str
    path: str
    line: int
    col: int
    rule_id: str
    symbol: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["severity"] = self.severity.value
        return data


@dataclass
class CheckResult:
    status: Status
    findings: list[Finding] = field(default_factory=list)
    path: str = "<string>"

    @property
    def summary(self) -> dict[str, int]:
        errors = sum(1 for f in self.findings if f.severity == Severity.ERROR)
        warnings = sum(1 for f in self.findings if f.severity == Severity.WARNING)
        uncovered = sum(1 for f in self.findings if f.severity == Severity.UNCOVERED)
        return {"errors": errors, "warnings": warnings, "uncovered": uncovered}

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "path": self.path,
            "findings": [f.to_dict() for f in self.findings],
            "summary": self.summary,
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def compute_status(findings: list[Finding]) -> Status:
    if any(f.severity == Severity.ERROR for f in findings):
        return Status.ERROR
    if any(f.severity == Severity.UNCOVERED for f in findings):
        return Status.UNCOVERED
    return Status.OK


def exit_code_for_status(status: Status) -> int:
    if status == Status.ERROR:
        return 1
    if status == Status.UNCOVERED:
        return 2
    return 0


def format_text(result: CheckResult) -> str:
    lines: list[str] = []
    lines.append(f"status: {result.status.value}")
    lines.append(
        "summary: "
        f"errors={result.summary['errors']} "
        f"warnings={result.summary['warnings']} "
        f"uncovered={result.summary['uncovered']}"
    )
    for finding in result.findings:
        symbol = f" ({finding.symbol})" if finding.symbol else ""
        lines.append(
            f"{finding.path}:{finding.line}:{finding.col} "
            f"[{finding.severity.value}] {finding.code}{symbol}: {finding.message}"
        )
    return "\n".join(lines)
