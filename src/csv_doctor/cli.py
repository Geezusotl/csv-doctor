"""Command-line interface.

Exit codes: 0 = clean (or fix written), 1 = ``check`` found issues, 2 = error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import __version__
from .checks import RULES, analyze
from .core import CsvDoctorError, load_table, write_table
from .fixer import check_output_allowed, default_output_path, fix_table
from .report import (check_json, fix_json, format_check, format_fix, format_rules)


def _delimiter(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if value.lower() in ("tab", "\\t"):
        return "\t"
    if len(value) != 1:
        raise CsvDoctorError(f"--delimiter must be a single character (or 'tab'), got {value!r}")
    return value


def _ignored(values) -> list:
    unknown = [v for v in values if v not in RULES]
    if unknown:
        raise CsvDoctorError(
            f"Unknown rule {unknown[0]!r}. Run 'csv-doctor rules' to see the available rules."
        )
    return list(values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="csv-doctor",
        description="Find problems in a CSV file and explain why each one was flagged.",
    )
    parser.add_argument("--version", action="version", version=f"csv-doctor {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("file", help="path to the CSV file")
        p.add_argument("--delimiter", help="field delimiter (default: detected); use 'tab' for tabs")
        p.add_argument("--ignore", action="append", default=[], metavar="RULE",
                       help="skip a rule (repeatable); see 'csv-doctor rules'")
        p.add_argument("--format", choices=("text", "json"), default="text",
                       help="output format (default: text)")

    check = sub.add_parser("check", help="report problems without changing anything")
    common(check)
    check.add_argument("--all", action="store_true", help="list every finding, not just the first few per rule")

    fix = sub.add_parser("fix", help="write a cleaned copy (the original is never modified)")
    common(fix)
    fix.add_argument("-o", "--output", help="where to write the cleaned copy (default: <name>.fixed.csv)")
    fix.add_argument("--force", action="store_true", help="replace the output file if it already exists")
    fix.add_argument("--all", action="store_true", help="list every remaining issue")

    sub.add_parser("rules", help="list what csv-doctor checks for")
    return parser


def _check(args) -> int:
    table = load_table(args.file, _delimiter(args.delimiter))
    findings = analyze(table, _ignored(args.ignore))
    if args.format == "json":
        print(check_json(findings, args.file, table))
    else:
        print(format_check(findings, args.file, table, None if args.all else 10))
    return 1 if findings else 0


def _fix(args) -> int:
    source = Path(args.file)
    table = load_table(source, _delimiter(args.delimiter))
    result = fix_table(table, _ignored(args.ignore))
    output = None
    if result.applied:
        output = Path(args.output) if args.output else default_output_path(source)
        check_output_allowed(source, output, args.force)
        write_table(output, result.table.header, result.table.rows, table.delimiter)
    out_name = str(output) if output else None
    if args.format == "json":
        print(fix_json(args.file, out_name, result, table.n_rows))
    else:
        print(format_fix(args.file, out_name, result, table.n_rows, None if args.all else 5))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    try:
        if args.command == "check":
            return _check(args)
        if args.command == "fix":
            return _fix(args)
        print(format_rules())
        return 0
    except CsvDoctorError as exc:
        print(f"csv-doctor: error: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0
