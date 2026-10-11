import copy
import os
import tempfile
import unittest
from pathlib import Path

from csv_doctor import DELETE_ROW, CsvDoctorError, Table, analyze, fix_table, load_table
from csv_doctor.fixer import check_output_allowed, default_output_path
from benchmark.dataset import build
from tests.helpers import column, make

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "employees_messy.csv"


class PackRegressionTests(unittest.TestCase):
    ROWS = [["1", "Maya", "10"], ["1", "Maya", "10"], ["1", "maya", "10"], ["1", "Maya ", "10"],
            ["2", "Noah", "0"], ["2", "Noah", "0"]]

    def test_only_exact_duplicates_are_removed_when_whitespace_is_not_cleaned(self):
        table = Table(["id", "name", "amount"], [list(r) for r in self.ROWS], ",")
        result = fix_table(table, ignore=["whitespace", "inconsistent_case"])
        self.assertEqual(result.table.rows, [["1", "Maya", "10"], ["1", "maya", "10"],
                                              ["1", "Maya ", "10"], ["2", "Noah", "0"]])

    def test_trim_then_dedupe_in_one_run(self):
        rows = [[" 001 ", " Maya "], ["001", "Maya"], ["002", "Noah"], [" 002", "Noah "], ["002", "noah"]]
        result = fix_table(Table(["id", "name"], rows, ","), ignore=["inconsistent_case"])
        self.assertEqual(result.table.rows, [["001", "Maya"], ["002", "Noah"], ["002", "noah"]])


class ExampleFileTests(unittest.TestCase):
    def setUp(self):
        self.table = load_table(EXAMPLE)
        self.before = copy.deepcopy(self.table)
        self.result = fix_table(self.table)
        self.by_id = {row[0]: row for row in self.result.table.rows}

    def test_input_is_not_modified(self):
        self.assertEqual(self.table, self.before)

    def test_each_safe_fix_is_applied(self):
        r = self.by_id
        self.assertEqual(r["4"][1], "Dev Patel")                       # whitespace
        self.assertEqual(r["5"][3:], ["2021-03-14", "54500", "45"])    # date + $ formatting
        self.assertEqual(r["6"][4], "")                                 # N/A -> blank
        self.assertEqual(r["7"][2], "Engineering")                      # typo
        self.assertEqual(r["9"][5], "20")                               # 2O -> 20
        self.assertEqual(r["10"][2], "Sales")                           # case
        self.assertEqual(r["11"][3], "2021-03-14")                      # 14 Mar 2021

    def test_duplicate_row_is_removed(self):
        self.assertEqual(self.result.table.n_rows, 20)
        self.assertEqual(sum(1 for row in self.result.table.rows if row[0] == "20"), 1)

    def test_unfixable_problems_are_left_alone_and_reported(self):
        self.assertEqual(self.by_id["8"][3], "2020-02-30")
        self.assertEqual(self.by_id["14"][5], "250")
        self.assertEqual({f.rule for f in self.result.remaining}, {"invalid_date", "outlier"})

    def test_cells_that_were_not_flagged_are_byte_for_byte_unchanged(self):
        changed = {(f.row, f.col) for f in self.result.applied if f.suggestion != DELETE_ROW}
        deleted = {f.row for f in self.result.applied if f.suggestion == DELETE_ROW}
        kept = [(i, row) for i, row in enumerate(self.table.rows) if i + 2 not in deleted]
        self.assertEqual(len(kept), len(self.result.table.rows))
        for (i, original), fixed in zip(kept, self.result.table.rows):
            for j, value in enumerate(original):
                if (i + 2, j) not in changed:
                    self.assertEqual(fixed[j], value, f"row {i + 2} col {j}")

    def test_fixing_twice_changes_nothing_more(self):
        again = fix_table(self.result.table)
        self.assertEqual(again.applied, [])
        self.assertEqual(again.table.rows, self.result.table.rows)
        self.assertTrue(self.result.converged and again.converged)

    def test_applied_findings_use_row_numbers_from_the_original_file(self):
        rows = {(f.rule, f.row) for f in self.result.applied}
        self.assertIn(("duplicate_row", 22), rows)
        self.assertIn(("whitespace", 5), rows)
        self.assertIn(("category_typo", 8), rows)


class MultiPassTests(unittest.TestCase):
    def test_a_fix_can_reveal_a_duplicate_that_is_then_removed(self):
        rows = ([["Zed", "Engineering"]] + [[f"n{i}", "Engineering"] for i in range(7)]
                + [[f"s{i}", "Sales"] for i in range(4)] + [["Zed", "Enginering"]])
        table = make(["name", "dept"], rows)
        result = fix_table(table)
        self.assertEqual(result.passes, 2)
        self.assertEqual(result.table.n_rows, 12)
        last_row_number = len(rows) + 1
        self.assertIn(("duplicate_row", last_row_number), {(f.rule, f.row) for f in result.applied})
        self.assertTrue(result.converged)

    def test_specific_fix_beats_whitespace_fix_on_the_same_cell(self):
        result = fix_table(column(["x", "y", " N/A ", "z", "w", "v"]))
        self.assertEqual(result.table.rows[2], [""])
        # one change per cell, made in one pass: not "trim it, then blank it"
        self.assertEqual([(f.rule, f.row) for f in result.applied], [("missing_placeholder", 4)])
        self.assertEqual(result.passes, 1)

    def test_ignored_rules_are_not_applied(self):
        table = column(["a", " b ", "N/A"])
        result = fix_table(table, ignore=["whitespace"])
        self.assertEqual(result.table.rows, [["a"], [" b "], [""]])


class CleanDataTests(unittest.TestCase):
    def test_clean_data_comes_back_identical(self):
        ds = build(5)
        table = Table(ds.header, ds.clean)
        result = fix_table(table)
        self.assertEqual(result.applied, [])
        self.assertEqual(result.table.rows, ds.clean)
        self.assertEqual(result.passes, 0)


class OutputPathTests(unittest.TestCase):
    def test_default_name_sits_next_to_the_input(self):
        self.assertEqual(default_output_path("data/people.csv"), Path("data/people.fixed.csv"))
        self.assertEqual(default_output_path("people"), Path("people.fixed.csv"))

    def test_refuses_to_overwrite_the_input_or_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp, "in.csv")
            source.write_text("a\n1\n")
            other = Path(tmp, "out.csv")
            other.write_text("old\n")
            with self.assertRaises(CsvDoctorError):
                check_output_allowed(source, source, force=True)     # never, even with --force
            with self.assertRaises(CsvDoctorError):
                check_output_allowed(source, Path(tmp, ".", "in.csv"), force=True)
            with self.assertRaises(CsvDoctorError):
                check_output_allowed(source, other, force=False)
            check_output_allowed(source, other, force=True)           # fine
            check_output_allowed(source, Path(tmp, "new.csv"))        # fine

    def test_refuses_a_symlink_to_the_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp, "in.csv")
            source.write_text("a\n1\n")
            link = Path(tmp, "link.csv")
            try:
                os.symlink(source, link)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks not available")
            with self.assertRaises(CsvDoctorError):
                check_output_allowed(source, link, force=True)


if __name__ == "__main__":
    unittest.main()


class SpacingRuleSwitchTests(unittest.TestCase):
    ROWS = [[" 001 ", " Maya ", "two  spaces"], ["002", "\tNoah\t", " keep internal  spaces "]]

    def fixed(self, **kw):
        return fix_table(Table(["id", "name", "note"], [list(r) for r in self.ROWS], ","), **kw).table.rows

    def test_everything_by_default(self):
        self.assertEqual(self.fixed(), [["001", "Maya", "two spaces"], ["002", "Noah", "keep internal spaces"]])

    def test_trim_only_keeps_inner_spacing(self):
        rows = self.fixed(ignore=["repeated_spaces"])
        self.assertEqual(rows, [["001", "Maya", "two  spaces"], ["002", "Noah", "keep internal  spaces"]])

    def test_collapse_only_keeps_the_outer_spaces(self):
        rows = self.fixed(ignore=["whitespace"])
        self.assertEqual(rows[1][2], " keep internal spaces ")
        self.assertEqual(rows[0][0], " 001 ")

    def test_formula_text_is_never_changed(self):
        table = Table(["a"], [["=1+1"], ["@SUM(1,2)"], ["a"], ["b"], ["c"], ["d"]], ",")
        self.assertEqual(fix_table(table).table.rows, table.rows)
