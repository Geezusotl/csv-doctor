"""Human-readable and JSON reports."""
from __future__ import annotations

import json
from collections import Counter
from typing import Optional

from .checks import DELETE_ROW, RULES, Finding


def _where(f: Finding) -> str:
    if f.column is None:
        return f"row {f.row}"
    return f"row {f.row}, column {f.column!r}"


def render_finding(f: Finding) -> list:
    """The lines for one finding: where it is, why it was flagged, what the fix is."""
    head = f"  {_where(f)}"
    if f.value is not None:
        head += f": {f.value!r}"
    if f.suggestion is None:
        fix = "no automatic fix"
    elif f.suggestion == DELETE_ROW:
        fix = "delete this row"
    elif f.suggestion == "":
        fix = "leave the cell blank"
    else:
        fix = repr(f.suggestion)
    return [head, f"      why: {f.why}", f"      fix: {fix}"]


def _grouped(findings: list) -> list:
    by_rule = {}
    for f in findings:
        by_rule.setdefault(f.rule, []).append(f)
    order = list(RULES)
    return sorted(by_rule.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99)


def format_findings(findings: list, limit: Optional[int] = 10) -> list:
    lines = []
    for rule, items in _grouped(findings):
        lines.append(f"{rule} ({len(items)}): {RULES.get(rule, '')}")
        shown = items if limit is None else items[:limit]
        for f in shown:
            lines.extend(render_finding(f))
        if len(shown) < len(items):
            lines.append(f"  ... and {len(items) - len(shown)} more (use --all to list every one)")
        lines.append("")
    return lines


def format_check(findings: list, source: str, table, limit: Optional[int] = 10) -> str:
    head = f"{source}: {table.n_rows} data rows, {table.n_cols} columns"
    if not findings:
        return f"{head}\nNo issues found."
    kinds = len({f.rule for f in findings})
    lines = [head, f"{len(findings)} issue(s) found across {kinds} rule(s).", ""]
    lines.extend(format_findings(findings, limit))
    return "\n".join(lines).rstrip()


def check_json(findings: list, source: str, table) -> str:
    return json.dumps(
        {
            "file": source,
            "data_rows": table.n_rows,
            "columns": table.n_cols,
            "issue_count": len(findings),
            "issues": [f.to_dict() for f in findings],
        },
        indent=2,
        ensure_ascii=False,
    )


def _summary_counts(findings: list) -> str:
    counts = Counter(f.rule for f in findings)
    order = list(RULES)
    parts = [f"{rule} {n}" for rule, n in sorted(counts.items(), key=lambda kv: order.index(kv[0]))]
    return ", ".join(parts)


def format_fix(source: str, output: Optional[str], result, rows_before: int,
               limit: Optional[int] = 5) -> str:
    lines = []
    if not result.applied:
        lines.append(f"{source}: nothing can be fixed automatically.")
    else:
        lines.append(f"Wrote a cleaned copy to {output} (the original file was not changed).")
        lines.append(f"Applied {len(result.applied)} fix(es) in {result.passes} pass(es): "
                     f"{_summary_counts(result.applied)}.")
        lines.append(f"Data rows: {rows_before} -> {result.table.n_rows}.")
    if result.remaining:
        lines.append("")
        lines.append(f"{len(result.remaining)} issue(s) still need a person to look at them:")
        lines.append("")
        lines.extend(format_findings(result.remaining, limit))
    return "\n".join(lines).rstrip()


def fix_json(source: str, output: Optional[str], result, rows_before: int) -> str:
    return json.dumps(
        {
            "file": source,
            "output": output,
            "rows_before": rows_before,
            "rows_after": result.table.n_rows,
            "passes": result.passes,
            "applied": [f.to_dict() for f in result.applied],
            "remaining": [f.to_dict() for f in result.remaining],
        },
        indent=2,
        ensure_ascii=False,
    )


def format_rules() -> str:
    width = max(len(r) for r in RULES)
    return "\n".join(f"{rule.ljust(width)}  {desc}" for rule, desc in RULES.items())
