"""Score csv-doctor against a generated file with a known answer key."""
from __future__ import annotations

from collections import Counter, defaultdict

from csv_doctor import DELETE_ROW, Table, analyze, fix_table, parse_text
from csv_doctor.core import dumps

from .dataset import build


def _pad(row, width):
    return list(row) + [""] * (width - len(row))


def evaluate(seed: int = 7) -> dict:
    ds = build(seed)
    width = len(ds.header)

    # Go through real CSV text so the parser is part of what is measured.
    dirty = parse_text(dumps(ds.header, ds.dirty), ",")
    clean = parse_text(dumps(ds.header, ds.clean), ",")

    found = {(f.rule, f.row, f.column): f for f in analyze(dirty)}
    truth = {t.key: t for t in ds.truth}
    tp = set(found) & set(truth)
    fp = set(found) - set(truth)
    fn = set(truth) - set(found)

    per_rule = defaultdict(lambda: Counter())
    for key in truth:
        per_rule[key[0]]["injected"] += 1
        per_rule[key[0]]["found"] += key in found
    for key in fp:
        per_rule[key[0]]["false_positives"] += 1

    # Quality of the suggested fixes, on the errors that were found.
    fix_correct = fix_none = fix_wrong = 0
    for key in tp:
        suggestion, expected = found[key].suggestion, truth[key].expected_fix
        if suggestion == expected:
            fix_correct += 1
        elif suggestion is None:
            fix_none += 1  # expected a fix, none offered (e.g. ambiguous date)
        else:
            fix_wrong += 1

    # End to end: run the fixer and compare with the clean original.
    result = fix_table(dirty)
    deleted = {f.row for f in result.applied if f.suggestion == DELETE_ROW}
    kept = [i for i in range(len(dirty.rows)) if i + 2 not in deleted]
    wrongly_deleted = sum(1 for i in deleted if i - 2 < len(ds.clean))
    wrong_before = wrong_after = regressions = 0
    for pos, i in enumerate(kept):
        if i >= len(ds.clean) or pos >= len(result.table.rows):
            continue
        for j in range(width):
            want = clean.rows[i][j]
            before = _pad(dirty.rows[i], width)[j]
            after = _pad(result.table.rows[pos], width)[j]
            wrong_before += before != want
            wrong_after += after != want
            regressions += before == want and after != want

    clean_findings = analyze(clean)
    n_tp, n_found, n_truth = len(tp), len(found), len(truth)
    return {
        "seed": seed,
        "rows": len(ds.dirty),
        "injected": n_truth,
        "reported": n_found,
        "true_positives": n_tp,
        "false_positives": len(fp),
        "missed": len(fn),
        "precision": n_tp / n_found if n_found else 1.0,
        "recall": n_tp / n_truth if n_truth else 1.0,
        "per_rule": {r: dict(c) for r, c in per_rule.items()},
        "false_positive_keys": sorted(fp),
        "missed_keys": sorted(fn),
        "fix_correct": fix_correct,
        "fix_not_offered": fix_none,
        "fix_wrong": fix_wrong,
        "clean_file_findings": len(clean_findings),
        "clean_file_rules": sorted({f.rule for f in clean_findings}),
        "cells_wrong_before": wrong_before,
        "cells_wrong_after": wrong_after,
        "regressions": regressions,
        "wrongly_deleted_rows": wrongly_deleted,
        "converged": result.converged,
    }
