"""CLI entry point."""

from __future__ import annotations

import argparse
import sys

from python_checker.api import check_file, check_source
from python_checker.report import exit_code_for_status, format_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic Python checker")
    parser.add_argument("path", nargs="?", help="Python file to check")
    parser.add_argument("--format", choices=["json", "text"], default="json")
    parser.add_argument(
        "--packs",
        default="numpy,pandas,sklearn,openai,fastapi,pytorch",
        help="Comma-separated rule packs",
    )
    parser.add_argument("--no-ruff", action="store_true", help="Disable Ruff pass")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    packs = [p.strip() for p in args.packs.split(",") if p.strip()]
    use_ruff = not args.no_ruff

    if args.path:
        result = check_file(args.path, packs=packs, ruff=use_ruff)
    else:
        source = sys.stdin.read()
        result = check_source(source, path="<stdin>", packs=packs, ruff=use_ruff)

    if args.format == "json":
        print(result.to_json(indent=2))
    else:
        print(format_text(result))
    return exit_code_for_status(result.status)


if __name__ == "__main__":
    raise SystemExit(main())
