"""Quality floors on the injected-error benchmark, plus a guard on valid-but-tricky data."""
import unittest
from pathlib import Path

from benchmark.evaluate import evaluate
from benchmark.hard_cases import run_cases
from benchmark.run import render

RESULTS = Path(__file__).resolve().parent.parent / "benchmark" / "RESULTS.md"


class BenchmarkTests(unittest.TestCase):
    def test_quality_floors_hold_on_many_random_files(self):
        for seed in range(1, 13):
            with self.subTest(seed=seed):
                m = evaluate(seed)
                self.assertGreaterEqual(m["precision"], 0.95, m["false_positive_keys"])
                self.assertGreaterEqual(m["recall"], 0.95, m["missed_keys"])
                self.assertEqual(m["fix_wrong"], 0, "a suggested fix was wrong")
                self.assertEqual(m["regressions"], 0, "fix broke a cell that was correct")
                self.assertEqual(m["wrongly_deleted_rows"], 0)
                self.assertLessEqual(m["clean_file_findings"], 1)
                self.assertTrue(m["converged"])
                self.assertLess(m["cells_wrong_after"], m["cells_wrong_before"])

    def test_tricky_but_valid_data_that_must_stay_clean(self):
        for case, rules in run_cases():
            if case.expect_clean:
                with self.subTest(case=case.name):
                    self.assertEqual(rules, [], f"false positive: {rules}")

    def test_committed_results_match_the_code(self):
        """If this fails, run `python -m benchmark.run` and commit the new RESULTS.md."""
        self.assertEqual(RESULTS.read_text(encoding="utf-8"), render())


if __name__ == "__main__":
    unittest.main()
