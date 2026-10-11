"""Apply the safe fixes, and never touch the original file."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .checks import DELETE_ROW, Finding, analyze
from .core import CsvDoctorError, Table


SPACING_RULES = ("whitespace", "repeated_spaces")


@dataclass
class FixResult:
    table: Table          # the cleaned data
    applied: list         # findings whose fix was applied (original row numbers)
    remaining: list       # findings still needing a person
    passes: int
    converged: bool       # False only if max_passes ran out with fixes pending


def fix_table(table: Table, ignore=(), max_passes: int = 6) -> FixResult:
    """Apply every finding that has a suggestion, repeating until none are left.

    Fixing can expose new issues (two rows become identical once a typo is
    corrected, say), so the checks are re-run after each pass. The input table
    is never modified.
    """
    ids = [table.row_number(i) for i in range(len(table.rows))]
    rows = [list(r) for r in table.rows]
    applied = []
    passes = 0

    while passes < max_passes:
        current = Table(table.header, rows, table.delimiter, ids)
        fixable = [f for f in analyze(current, ignore) if f.fixable]
        if not fixable:
            break
        passes += 1
        index_of = {rid: k for k, rid in enumerate(ids)}
        deleted = {f.row for f in fixable if f.suggestion == DELETE_ROW}
        winners = {}
        # Spacing fixes go first; a more specific fix for the same cell then overrides them.
        for f in sorted(fixable, key=lambda f: (f.rule not in SPACING_RULES, f.rule != "whitespace")):
            if f.suggestion != DELETE_ROW and f.row not in deleted:
                winners[(f.row, f.col)] = f
        for (row_number, col), f in winners.items():
            rows[index_of[row_number]][col] = f.suggestion
            applied.append(f)
        applied.extend(f for f in fixable if f.suggestion == DELETE_ROW)
        keep = [k for k, rid in enumerate(ids) if rid not in deleted]
        rows = [rows[k] for k in keep]
        ids = [ids[k] for k in keep]

    final = Table(table.header, rows, table.delimiter, ids)
    remaining = analyze(final, ignore)
    converged = not any(f.fixable for f in remaining)
    applied.sort(key=lambda f: (f.row, -1 if f.col is None else f.col))
    return FixResult(Table(list(table.header), rows, table.delimiter), applied,
                     remaining, passes, converged)


def default_output_path(source) -> Path:
    source = Path(source)
    return source.with_name(f"{source.stem}.fixed{source.suffix or '.csv'}")


def check_output_allowed(source, output, force: bool = False) -> None:
    """Refuse to overwrite the input, or an existing file without --force."""
    source, output = Path(source), Path(output)
    same = source.resolve() == output.resolve()
    if not same and output.exists():
        try:
            same = os.path.samefile(source, output)
        except OSError:
            pass
    if same:
        raise CsvDoctorError(
            f"Refusing to write over the input file ({source}). Choose a different --output."
        )
    if output.exists() and not force:
        raise CsvDoctorError(f"{output} already exists. Use --force to replace it.")
