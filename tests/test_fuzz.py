"""Seeded random tables: the tool must never crash and fixing must be safe.

No third-party library is needed. Every table comes from a seed, so a failure
prints the seed and can be reproduced exactly.
"""
import copy
import random
import unittest

from csv_doctor import DELETE_ROW, Table, analyze, fix_table, parse_text
from csv_doctor.core import dumps

JUNK = ["a", "B", "é", " ", "  ", "\t", "1", "0", "-", "/", ".", ",", "$", "N/A", "?", "x\ny",
        '"', "'", " ", "\x1f", "2021-03-05", "03/05/2021", "Mar 5, 2021", "null", "O", "l"]
WORDS = ["Sales", "Support", "Engineering", "Finance"]


def cell(rng, flavor):
    roll = rng.random()
    if flavor == "num":
        return str(rng.randint(0, 100)) if roll < 0.9 else rng.choice(["$1,000", "4O", "twenty", "N/A", " 7 ", "999999"])
    if flavor == "date":
        d = f"2021-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        return d if roll < 0.85 else rng.choice(["03/05/2021", "14 Mar 2021", "2021-02-30", "soon", "25/12/2021"])
    if flavor == "cat":
        w = rng.choice(WORDS)
        return w if roll < 0.85 else rng.choice([w.lower(), w[:-1], w + w[-1], " " + w, "N/A"])
    return "".join(rng.choice(JUNK) for _ in range(rng.randint(0, 4)))


def random_table(seed):
    rng = random.Random(seed)
    n_cols = rng.randint(1, 6)
    flavors = [rng.choice(["num", "date", "cat", "mess"]) for _ in range(n_cols)]
    header = ["".join(rng.choice(JUNK) for _ in range(rng.randint(0, 3))) for _ in range(n_cols)]
    rows = []
    for _ in range(rng.randint(0, 45)):
        row = [cell(rng, f) for f in flavors]
        if rng.random() < 0.05 and n_cols > 1:
            row = row[: rng.randint(0, n_cols - 1)]            # ragged (or blank)
        rows.append(row)
    if rows and rng.random() < 0.3:
        rows.append(list(rng.choice(rows)))                      # an exact duplicate
    return Table(header, rows, ",")


class FuzzTests(unittest.TestCase):
    def test_random_tables(self):
        for seed in range(600):
            with self.subTest(seed=seed):
                table = random_table(seed)
                snapshot = copy.deepcopy(table)
                n_rows = table.n_rows

                findings = analyze(table)
                for f in findings:
                    self.assertTrue(2 <= f.row <= n_rows + 1, f)
                    self.assertTrue(f.col is None or 0 <= f.col < table.n_cols, f)
                    self.assertTrue(f.why, f)

                result = fix_table(table)
                self.assertEqual(table, snapshot, "input was modified")
                self.assertTrue(result.converged)

                deleted = sum(1 for f in result.applied if f.suggestion == DELETE_ROW)
                self.assertEqual(result.table.n_rows, n_rows - deleted)

                again = fix_table(result.table)
                self.assertEqual(again.applied, [], "fixing is not idempotent")
                self.assertEqual(again.table.rows, result.table.rows)

                # What we write must read back as the same table.
                text = dumps(result.table.header, result.table.rows, ",")
                back = parse_text(text, ",")
                if result.table.header or result.table.rows:
                    self.assertEqual(back.header, result.table.header)
                    expected_rows = result.table.rows
                    if len(back.header) == 1:
                        expected_rows = [r if r else [""] for r in expected_rows]
                    self.assertEqual(back.rows, expected_rows)

    def test_clean_cells_are_never_changed(self):
        """Any cell the checks did not flag must come through untouched."""
        for seed in range(600, 900):
            with self.subTest(seed=seed):
                table = random_table(seed)
                result = fix_table(table)
                touched = {(f.row, f.col) for f in result.applied}
                deleted = {f.row for f in result.applied if f.suggestion == DELETE_ROW}
                kept = [(i, r) for i, r in enumerate(table.rows) if i + 2 not in deleted]
                self.assertEqual(len(kept), result.table.n_rows)
                for (i, original), fixed in zip(kept, result.table.rows):
                    self.assertEqual(len(original), len(fixed))
                    for j, value in enumerate(original):
                        if (i + 2, j) not in touched:
                            self.assertEqual(fixed[j], value)


if __name__ == "__main__":
    unittest.main()
