"""Public check API."""

from __future__ import annotations

from pathlib import Path

from python_checker.facts import FactsStore
from python_checker.parse import parse_source
from python_checker.passes.imports import collect_import_facts
from python_checker.passes.library_rules import check_library_rules
from python_checker.passes.natures import propagate_natures
from python_checker.passes.variables import check_variables
from python_checker.passes.shapes import check_shapes
from python_checker.passes.structure import check_structure
from python_checker.report import CheckResult, Finding, Severity, compute_status
from python_checker.ruff_adapter import run_ruff

DEFAULT_PACKS = ["numpy", "pandas", "sklearn", "openai", "fastapi", "pytorch"]


def check_source(
    source: str,
    path: str = "<string>",
    *,
    packs: list[str] | None = None,
    ruff: bool = True,
) -> CheckResult:
    selected_packs = packs if packs is not None else DEFAULT_PACKS
    findings: list[Finding] = []

    if ruff:
        findings.extend(run_ruff(path, source=source))

    try:
        parsed = parse_source(source, path=path)
    except SyntaxError as exc:
        findings.append(
            Finding(
                code="SYNTAX_ERROR",
                severity=Severity.ERROR,
                message=str(exc.msg or exc),
                path=path,
                line=int(exc.lineno or 1),
                col=int(exc.offset or 0) + 1,
                rule_id="parse.syntax",
            )
        )
        return CheckResult(status=compute_status(findings), findings=findings, path=path)

    findings.extend(check_structure(parsed))

    facts = FactsStore()
    collect_import_facts(parsed, facts)
    propagate_natures(parsed, facts, selected_packs)
    findings.extend(check_variables(parsed, facts, selected_packs))
    findings.extend(check_library_rules(parsed, facts, selected_packs))
    findings.extend(check_shapes(parsed, facts, selected_packs))

    return CheckResult(status=compute_status(findings), findings=findings, path=path)


def check_file(
    file_path: str | Path,
    *,
    packs: list[str] | None = None,
    ruff: bool = True,
) -> CheckResult:
    path = Path(file_path)
    source = path.read_text(encoding="utf-8")
    return check_source(source, path=str(path), packs=packs, ruff=ruff)
