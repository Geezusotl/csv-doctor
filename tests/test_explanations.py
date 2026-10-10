"""Every flag must explain itself: that is the point of the tool."""
import unittest

from csv_doctor import DELETE_ROW, RULES, analyze, parse_text
from csv_doctor.core import dumps
from benchmark.dataset import build


class ExplanationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ds = build(3)
        cls.findings = analyze(parse_text(dumps(ds.header, ds.dirty), ","))

    def test_the_sample_exercises_every_rule(self):
        self.assertEqual({f.rule for f in self.findings}, set(RULES))

    def test_every_finding_names_its_value_and_column_in_the_reason(self):
        for f in self.findings:
            with self.subTest(rule=f.rule, row=f.row):
                self.assertGreaterEqual(len(f.why), 25)
                if f.value is not None:
                    self.assertIn(repr(f.value), f.why)
                if f.column is not None:
                    self.assertIn(repr(f.column), f.why)

    def test_findings_without_a_fix_say_why_there_is_none(self):
        unfixable = [f for f in self.findings if f.suggestion is None]
        self.assertTrue(unfixable)
        for f in unfixable:
            with self.subTest(rule=f.rule, row=f.row):
                self.assertIn("No automatic fix is offered", f.why)

    def test_only_duplicate_rows_are_deleted_and_replacements_really_change_the_cell(self):
        for f in self.findings:
            if f.suggestion == DELETE_ROW:
                self.assertEqual(f.rule, "duplicate_row")
            elif f.suggestion is not None:
                self.assertNotEqual(f.suggestion, f.value, f"{f.rule} row {f.row}")

    def test_json_form_carries_the_same_information(self):
        for f in self.findings:
            d = f.to_dict()
            self.assertEqual(set(d), {"rule", "row", "column", "value", "why", "fix"})
            self.assertEqual(d["fix"] is None, f.suggestion is None)

    def test_findings_are_ordered_by_row(self):
        rows = [f.row for f in self.findings]
        self.assertEqual(rows, sorted(rows))


if __name__ == "__main__":
    unittest.main()
