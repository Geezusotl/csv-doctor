import tempfile
import time
import unittest
from pathlib import Path

from csv_doctor import Table, analyze, fix_table, load_table, parse_text
from csv_doctor.checks import build_columns
from csv_doctor.core import CsvDoctorError, decode, dumps, write_table
from tests.helpers import column, make, only


class ParsingTests(unittest.TestCase):
    def test_empty_file(self):
        # An empty file must be reported, never passed off as a clean one.
        for text in ("", "   \n\r\n  "):
            with self.assertRaises(CsvDoctorError):
                parse_text(text)

    def test_header_only(self):
        table = parse_text("a,b,c\n")
        self.assertEqual((table.header, table.rows), (["a", "b", "c"], []))
        self.assertEqual(analyze(table), [])

    def test_single_column_blank_lines_are_blank_values(self):
        table = parse_text("name\nAnn\n\nBob\n", ",")
        self.assertEqual(table.rows, [["Ann"], [""], ["Bob"]])
        self.assertEqual(only(analyze(table), "ragged_row"), [])

    def test_blank_line_in_a_multi_column_file_is_reported(self):
        table = parse_text("a,b\n1,2\n\n3,4\n", ",")
        self.assertEqual([f.row for f in only(analyze(table), "ragged_row")], [3])

    def test_utf8_bom_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "bom.csv")
            path.write_bytes(b"\xef\xbb\xbfname,age\nJo,3\n")
            self.assertEqual(load_table(path).header, ["name", "age"])

    def test_windows_1252_files_are_decoded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "latin.csv")
            path.write_bytes("name\ncaf\xe9\n".encode("latin-1"))
            self.assertEqual(load_table(path).rows, [["café"]])

    def test_crlf_line_endings(self):
        table = parse_text("a,b\r\n1,2\r\n3,4\r\n", ",")
        self.assertEqual(table.rows, [["1", "2"], ["3", "4"]])

    def test_delimiter_detection(self):
        rows = "".join(f"{i}{{d}}name{i}{{d}}city{i}\n" for i in range(6))
        for delimiter in (",", ";", "\t", "|"):
            text = f"id{delimiter}name{delimiter}city\n" + rows.format(d=delimiter)
            self.assertEqual(parse_text(text).n_cols, 3, repr(delimiter))

    def test_quoted_newlines_and_unicode_survive_fixing(self):
        text = 'id,note\n1,"line one\nline two"\n2,Zoë\n3,李雷\n4,  trim me  \n5,x\n'
        table = parse_text(text, ",")
        result = fix_table(table)
        self.assertEqual(result.table.rows[0], ["1", "line one\nline two"])
        self.assertEqual(result.table.rows[1:4], [["2", "Zoë"], ["3", "李雷"], ["4", "trim me"]])
        again = parse_text(dumps(result.table.header, result.table.rows), ",")
        self.assertEqual(again.rows, result.table.rows)

    def test_write_then_read_round_trip(self):
        rows = [["1", 'quote " inside'], ["2", "comma, inside"], ["3", ""], ["4", "tab\tinside"]]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "out.csv")
            write_table(path, ["id", "text"], rows)
            self.assertEqual(load_table(path).rows, rows)
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["out.csv"])


class ShapeTests(unittest.TestCase):
    def test_all_empty_and_all_placeholder_columns(self):
        table = make(["a", "b"], [["1", ""], ["2", ""], ["3", "N/A"], ["4", ""], ["5", ""], ["6", ""]])
        rules = {f.rule for f in analyze(table)}
        self.assertEqual(rules, {"missing_placeholder"})

    def test_duplicate_header_names_still_point_at_the_right_column(self):
        table = make(["a", "a"], [["x", " y"]] * 1 + [["p", "q"], ["r", "s"]])
        found = only(analyze(table), "whitespace")
        self.assertEqual([(f.col, f.column) for f in found], [(1, "a")])

    def test_very_wide_file(self):
        header = [f"c{i}" for i in range(300)]
        rows = [[str(i + j) for j in range(300)] for i in range(40)]
        self.assertEqual(only(analyze(make(header, rows)), "whitespace"), [])

    def test_huge_cell(self):
        table = column(["x" * 200_000, "y", "z"])
        self.assertEqual(analyze(table), [])

    def test_extra_fields_do_not_crash_cell_checks(self):
        table = make(["a"], [["1"], ["2", "extra", "fields"], ["3"], ["4"], ["5"], ["6"]])
        self.assertEqual([f.row for f in only(analyze(table), "ragged_row")], [3])

    def test_numeric_kinds(self):
        self.assertEqual(build_columns(column(["1,200", "3,400", "5,600", "7", "8", "9"]))[0].kind, "numeric")
        self.assertEqual(build_columns(column(["a", "b", "c"]))[0].kind, "text")  # too few values to type


class PerformanceTests(unittest.TestCase):
    def test_a_large_file_is_handled_in_reasonable_time(self):
        header = ["id", "name", "dept", "when", "pay", "age"]
        depts = ["Engineering", "Sales", "Support", "Finance"]
        rows = [[str(i), f"Person {i}", depts[i % 4], f"2021-{i % 12 + 1:02d}-{i % 27 + 1:02d}",
                 str(40000 + (i * 37) % 30000), str(20 + i % 45)] for i in range(40_000)]
        start = time.perf_counter()
        table = Table(header, rows)
        findings = analyze(table)
        fix_table(table)
        elapsed = time.perf_counter() - start
        self.assertEqual(findings, [])
        self.assertLess(elapsed, 30, f"took {elapsed:.1f}s")


if __name__ == "__main__":
    unittest.main()


class FileFormatTests(unittest.TestCase):
    """Regressions found by the external CSV Doctor test pack."""

    def test_unclosed_quote_is_reported_with_its_line(self):
        with self.assertRaises(CsvDoctorError) as ctx:
            parse_text('id,note\r\n1,"unfinished\r\n2,next\r\n')
        self.assertIn("line 2", str(ctx.exception))

    def test_unclosed_quote_on_a_later_line(self):
        with self.assertRaises(CsvDoctorError) as ctx:
            parse_text('a,b\n1,2\n3,"x\n4,5\n')
        self.assertIn("line 3", str(ctx.exception))

    def test_stray_quote_inside_a_value_is_still_read_leniently(self):
        table = parse_text('a,b\n5" pipe,2\nok,3\n')
        self.assertEqual(table.rows[1], ["ok", "3"])

    def test_multiline_quoted_field_is_not_mistaken_for_unclosed(self):
        table = parse_text('id,note\n1,"line one\nline two"\n2,x\n')
        self.assertEqual(table.rows, [["1", "line one\nline two"], ["2", "x"]])

    def test_utf16_with_bom_both_byte_orders(self):
        for enc in ("utf-16-le", "utf-16-be"):
            raw = b"\xff\xfe" if enc.endswith("le") else b"\xfe\xff"
            raw += "id,name\r\n001,Jos\u00e9\r\n002,\u6771\u4eac\r\n".encode(enc)
            table = parse_text(decode(raw))
            self.assertEqual(table.rows, [["001", "Jos\u00e9"], ["002", "\u6771\u4eac"]], enc)

    def test_utf16_without_bom(self):
        raw = "id,name\n1,abc\n2,def\n".encode("utf-16-le")
        self.assertEqual(parse_text(decode(raw)).rows, [["1", "abc"], ["2", "def"]])

    def test_binary_with_nul_bytes_is_refused_not_garbled(self):
        with self.assertRaises(CsvDoctorError):
            decode(bytes(range(256)) * 8)  # arbitrary binary, not UTF-16

    def test_header_only_file_is_fine(self):
        table = parse_text("id,name,amount\r\n")
        self.assertEqual((table.n_rows, table.n_cols), (0, 3))
        self.assertEqual(analyze(table), [])
