import unittest

from csv_doctor import DELETE_ROW, analyze
from csv_doctor.checks import build_columns, edit_distance, parse_number
from tests.helpers import by_value, column, make, only

AGES = [str(30 + i) for i in range(10)]                      # 30..39
ISO = [f"2021-03-{d:02d}" for d in range(1, 11)]             # ten ISO dates
DEPT = ["Engineering"] * 8 + ["Sales"] * 8 + ["Support"] * 8


class WhitespaceTests(unittest.TestCase):
    def test_edges_and_repeated_spaces(self):
        table = column(["ok", " lead", "trail ", "in  ner", "fine", "fine2"])
        found = only(analyze(table), "whitespace")
        self.assertEqual([(f.row, f.suggestion) for f in found],
                         [(3, "lead"), (4, "trail"), (5, "in ner")])

    def test_single_inner_tab_and_clean_cells_are_fine(self):
        self.assertEqual(only(analyze(column(["a\tb", "plain", "x y"])), "whitespace"), [])

    def test_whitespace_only_cell_becomes_blank(self):
        found = only(analyze(column(["a", "   ", "b"])), "whitespace")
        self.assertEqual([(f.row, f.suggestion) for f in found], [(3, "")])


class PlaceholderTests(unittest.TestCase):
    def test_flagged_case_insensitively_and_suggest_blank(self):
        table = column(["x", "N/A", "null", " none ", "-", "?", "Real", "NaN"])
        found = only(analyze(table), "missing_placeholder")
        self.assertEqual([f.row for f in found], [3, 4, 5, 6, 7, 9])
        self.assertTrue(all(f.suggestion == "" for f in found))

    def test_placeholders_do_not_break_column_typing(self):
        table = column(AGES + ["N/A", "n/a", "-"])
        rules = {f.rule for f in analyze(table)}
        self.assertEqual(rules, {"missing_placeholder"})


class RowTests(unittest.TestCase):
    def test_only_later_copies_are_duplicates(self):
        table = make(["a", "b"], [["1", "x"], ["2", "y"], ["1", "x"], ["3", "z"], ["1", "x"]])
        found = only(analyze(table), "duplicate_row")
        self.assertEqual([(f.row, f.suggestion) for f in found], [(4, DELETE_ROW), (6, DELETE_ROW)])
        self.assertIn("row 2", found[0].why)

    def test_spacing_is_ignored_but_case_is_not(self):
        table = make(["a", "b"], [["x", "y"], ["x ", " y"], ["X", "y"]])
        self.assertEqual([f.row for f in only(analyze(table), "duplicate_row")], [3])

    def test_empty_rows_are_not_duplicates(self):
        table = make(["a", "b"], [["", ""], ["", ""], ["1", "2"]])
        self.assertEqual(only(analyze(table), "duplicate_row"), [])

    def test_ragged_and_blank_rows(self):
        table = make(["a", "b", "c"], [["1", "2", "3"], ["1", "2"], ["1", "2", "3", "4"], []])
        found = only(analyze(table), "ragged_row")
        self.assertEqual([f.row for f in found], [3, 4, 5])
        self.assertTrue(all(f.suggestion is None for f in found))


class NumberTests(unittest.TestCase):
    def test_parse_number(self):
        self.assertEqual(parse_number("12")[2], True)
        self.assertEqual(parse_number("-1.5e3")[0], -1500.0)
        self.assertEqual(parse_number("$1,200.50")[1:], ("1200.50", False))
        self.assertEqual(parse_number("1,234")[1], "1234")
        for text in ("1,2", "12abc", "", "inf", "nan", "1e999", "1,23,456", "5%"):
            self.assertIsNone(parse_number(text), text)

    def test_words_in_a_numeric_column(self):
        found = only(analyze(column(AGES + ["twenty"])), "mixed_type")
        self.assertEqual(by_value(found), {"twenty": None})

    def test_letters_typed_for_digits_get_a_fix(self):
        found = only(analyze(column(AGES + ["4O", "3l"])), "mixed_type")
        self.assertEqual(by_value(found), {"4O": "40", "3l": "31"})

    def test_formatted_numbers_in_a_plain_column(self):
        values = [str(50000 + i * 1000) for i in range(9)] + ["$61,200", "62,500"]
        found = only(analyze(column(values)), "number_format")
        self.assertEqual(by_value(found), {"$61,200": "61200", "62,500": "62500"})

    def test_consistently_formatted_column_is_left_alone(self):
        values = [f"${50000 + i * 1000:,}" for i in range(10)]
        self.assertEqual(analyze(column(values)), [])

    def test_mostly_text_column_is_not_numeric(self):
        values = ["1", "2", "3", "apple", "pear", "plum", "fig", "kiwi", "lime", "date"]
        self.assertEqual(only(analyze(column(values)), "mixed_type"), [])

    def test_negative_and_scientific_numbers_are_numbers(self):
        cols = build_columns(column(["-5", "1e3", "2.5", "+7", "3", "4", "5", "6"]))
        self.assertEqual(cols[0].kind, "numeric")


class OutlierTests(unittest.TestCase):
    def test_flagged_but_never_fixed(self):
        found = only(analyze(column(AGES + ["999"])), "outlier")
        self.assertEqual(by_value(found), {"999": None})
        self.assertIn("No automatic fix", found[0].why)

    def test_needs_enough_values(self):
        self.assertEqual(only(analyze(column(["30", "31", "32", "999"])), "outlier"), [])

    def test_sequential_ids_are_not_outliers(self):
        self.assertEqual(only(analyze(column([str(i) for i in range(1, 80)])), "outlier"), [])

    def test_zero_mad_falls_back_to_mean_deviation(self):
        found = only(analyze(column(["10"] * 12 + ["500"])), "outlier")
        self.assertEqual([f.value for f in found], ["500"])

    def test_constant_column_is_fine(self):
        self.assertEqual(only(analyze(column(["7"] * 10)), "outlier"), [])

    def test_codes_with_leading_zeros_are_not_quantities(self):
        zips = ["02134", "10001", "94105", "60601", "73301", "02139", "02134", "10001", "94105", "60601"]
        self.assertEqual(only(analyze(column(zips)), "outlier"), [])


class DateTests(unittest.TestCase):
    def test_other_formats_are_rewritten_to_the_main_one(self):
        values = ISO + ["03/17/2021", "14 Mar 2021", "Mar 15, 2021", "16.03.2021"]
        found = by_value(only(analyze(column(values)), "mixed_date_format"))
        self.assertEqual(found, {"03/17/2021": "2021-03-17", "14 Mar 2021": "2021-03-14",
                                 "Mar 15, 2021": "2021-03-15", "16.03.2021": "2021-03-16"})

    def test_ambiguous_slash_date_is_flagged_without_a_guess(self):
        found = only(analyze(column(ISO + ["03/04/2021"])), "mixed_date_format")
        self.assertEqual(by_value(found), {"03/04/2021": None})
        self.assertIn("either way", found[0].why)

    def test_other_slash_dates_settle_the_order(self):
        found = only(analyze(column(ISO + ["03/04/2021", "25/12/2021"])), "mixed_date_format")
        self.assertEqual(by_value(found), {"03/04/2021": "2021-04-03", "25/12/2021": "2021-12-25"})

    def test_conflicting_evidence_is_resolved_cell_by_cell(self):
        # one DD/MM and one MM/DD cell in the same column: each says what it is
        found = only(analyze(column(ISO + ["25/12/2021", "12/25/2021"])), "mixed_date_format")
        self.assertEqual(by_value(found), {"25/12/2021": "2021-12-25", "12/25/2021": "2021-12-25"})

    def test_dash_separated_numeric_dates(self):
        found = only(analyze(column(ISO + ["17-03-2021"])), "mixed_date_format")
        self.assertEqual(by_value(found), {"17-03-2021": "2021-03-17"})

    def test_impossible_and_non_dates_are_invalid(self):
        found = only(analyze(column(ISO + ["2021-02-30", "soon"])), "invalid_date")
        self.assertEqual(by_value(found), {"2021-02-30": None, "soon": None})

    def test_whatever_format_dominates_is_kept(self):
        values = [f"{d:02d} Mar 2021" for d in range(1, 11)] + ["2021-03-11"]
        found = only(analyze(column(values)), "mixed_date_format")
        self.assertEqual(by_value(found), {"2021-03-11": "11 Mar 2021"})

    def test_ambiguous_dominant_format_gives_no_suggestions(self):
        values = ["01/02/2021", "03/04/2021", "05/06/2021", "07/08/2021", "09/10/2021", "2021-03-17"]
        found = only(analyze(column(values)), "mixed_date_format")
        self.assertEqual(by_value(found), {"2021-03-17": None})

    def test_consistent_dates_have_no_findings(self):
        self.assertEqual(analyze(column(ISO)), [])

    def test_text_column_with_one_date_is_not_a_date_column(self):
        values = ["2021-03-01", "hello", "world", "foo", "bar", "baz"]
        self.assertEqual(only(analyze(column(values)), "invalid_date"), [])


class CategoryTests(unittest.TestCase):
    def test_case_variants_follow_the_most_common_spelling(self):
        found = only(analyze(column(DEPT + ["sales", "ENGINEERING"])), "inconsistent_case")
        self.assertEqual(by_value(found), {"sales": "Sales", "ENGINEERING": "Engineering"})

    def test_rare_value_one_edit_from_a_common_one_is_a_typo(self):
        found = only(analyze(column(DEPT + ["Enginering"])), "category_typo")
        self.assertEqual(by_value(found), {"Enginering": "Engineering"})

    def test_swapped_letters_are_a_typo(self):
        found = only(analyze(column(DEPT + ["Suppotr"])), "category_typo")
        self.assertEqual(by_value(found), {"Suppotr": "Support"})

    def test_long_values_allow_two_edits_short_ones_do_not(self):
        long_typo = only(analyze(column(DEPT + ["Engneerng"])), "category_typo")
        short_typo = only(analyze(column(DEPT + ["Suppt"])), "category_typo")
        self.assertEqual(by_value(long_typo), {"Engneerng": "Engineering"})
        self.assertEqual(short_typo, [])

    def test_typo_in_the_wrong_case_gets_one_finding_with_the_right_spelling(self):
        found = analyze(column(DEPT + ["enginering"]))
        self.assertEqual([(f.rule, f.suggestion) for f in found if f.value == "enginering"],
                         [("category_typo", "Engineering")])

    def test_labels_that_differ_by_a_tag_are_not_typos(self):
        for values in (["Class A"] * 8 + ["Class B"] * 8 + ["Class C"],
                       ["Group 1"] * 8 + ["Group 2"] * 8 + ["Group 3"],
                       ["Room 101"] * 8 + ["Room 102"] * 8 + ["Room 103"]):
            self.assertEqual(only(analyze(column(values)), "category_typo"), [], values[0])

    def test_needs_a_clear_majority_to_call_something_a_typo(self):
        values = ["Engineering"] * 4 + ["Enginering"] + ["Sales"] * 8 + ["Support"] * 8
        self.assertEqual(only(analyze(column(values)), "category_typo"), [])

    def test_values_under_five_letters_are_not_checked(self):
        self.assertEqual(only(analyze(column(["Sales"] * 8 + ["Sale"] + ["Ops"] * 8)), "category_typo"), [])

    def test_free_text_is_not_treated_as_categories(self):
        names = [f"person {i}" for i in range(30)] + ["person 3x"]
        self.assertEqual(only(analyze(column(names)), "category_typo"), [])


class EditDistanceTests(unittest.TestCase):
    def test_distances(self):
        self.assertEqual(edit_distance("abc", "abc", 2), 0)
        self.assertEqual(edit_distance("abc", "abd", 2), 1)
        self.assertEqual(edit_distance("abc", "acb", 2), 1)        # swap counts as one
        self.assertEqual(edit_distance("kitten", "sitting", 5), 3)
        self.assertGreater(edit_distance("a", "abcdef", 2), 2)     # early exit


class IgnoreTests(unittest.TestCase):
    def test_ignored_rules_are_dropped(self):
        table = column(AGES + ["N/A", " 5 "])
        self.assertTrue(only(analyze(table), "missing_placeholder"))
        self.assertEqual(only(analyze(table, ignore=["missing_placeholder"]), "missing_placeholder"), [])


if __name__ == "__main__":
    unittest.main()
