import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from csv_doctor import RULES
from csv_doctor.cli import main

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "employees_messy.csv"
CLEAN = "id,name,age\n" + "".join(f"{i},Person {i},{30 + i}\n" for i in range(1, 11))


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in args])
    return code, out.getvalue(), err.getvalue()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class CheckCommandTests(unittest.TestCase):
    def test_issues_give_exit_code_1_and_explanations(self):
        code, out, _ = run("check", EXAMPLE)
        self.assertEqual(code, 1)
        self.assertIn("why:", out)
        self.assertIn("fix:", out)
        self.assertIn("row 8, column 'department': 'Enginering'", out)

    def test_clean_file_gives_exit_code_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "clean.csv")
            path.write_text(CLEAN)
            code, out, _ = run("check", path)
        self.assertEqual(code, 0)
        self.assertIn("No issues found", out)

    def test_json_output_is_valid_and_complete(self):
        code, out, _ = run("check", EXAMPLE, "--format", "json")
        data = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(data["issue_count"], len(data["issues"]))
        self.assertEqual(data["data_rows"], 21)
        issue = next(i for i in data["issues"] if i["rule"] == "category_typo")
        self.assertEqual(issue["row"], 8)
        self.assertEqual(issue["fix"], {"action": "replace", "value": "Engineering"})
        dup = next(i for i in data["issues"] if i["rule"] == "duplicate_row")
        self.assertEqual(dup["fix"], {"action": "delete_row"})
        self.assertTrue(all(i["why"] for i in data["issues"]))

    def test_all_flag_lists_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "many.csv")
            path.write_text("id,v\n" + "".join(f"{i}, x{i}\n" for i in range(30)))
            _, short, _ = run("check", path)
            _, full, _ = run("check", path, "--all")
        self.assertIn("and 20 more", short)
        self.assertNotIn("more (use --all", full)

    def test_ignore_skips_rules(self):
        args = ["check", EXAMPLE]
        for rule in RULES:
            args += ["--ignore", rule]
        code, out, _ = run(*args)
        self.assertEqual(code, 0)
        self.assertIn("No issues found", out)

    def test_unknown_rule_is_an_error(self):
        code, _, err = run("check", EXAMPLE, "--ignore", "nonsense")
        self.assertEqual(code, 2)
        self.assertIn("nonsense", err)

    def test_missing_file_and_directory_are_errors_not_tracebacks(self):
        code, _, err = run("check", "/no/such/file.csv")
        self.assertEqual((code, "File not found" in err), (2, True))
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = run("check", tmp)
        self.assertEqual((code, "directory" in err), (2, True))

    def test_delimiters(self):
        with tempfile.TemporaryDirectory() as tmp:
            semi = Path(tmp, "semi.csv")
            semi.write_text("id;name\n1; Ann\n2;Bob\n3;Cy\n4;Di\n5;Ed\n6;Flo\n")
            code, out, _ = run("check", semi, "--delimiter", ";", "--format", "json")
            self.assertEqual(json.loads(out)["columns"], 2)
            tab = Path(tmp, "tab.tsv")
            tab.write_text("id\tname\n1\t Ann\n2\tBob\n")
            code, out, _ = run("check", tab, "--delimiter", "tab", "--format", "json")
            self.assertEqual(json.loads(out)["columns"], 2)
            code, _, err = run("check", tab, "--delimiter", "ab")
            self.assertEqual(code, 2)

    def test_delimiter_is_detected_when_not_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "pipes.csv")
            path.write_text("id|name|city\n1|Ann|Oslo\n2|Bob|Rome\n3|Cy|Lima\n4|Di|Bern\n")
            _, out, _ = run("check", path, "--format", "json")
        self.assertEqual(json.loads(out)["columns"], 3)


class FixCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name, "people.csv")
        self.source.write_bytes(EXAMPLE.read_bytes())
        self.original_hash = digest(self.source)

    def test_writes_a_new_file_and_never_changes_the_original(self):
        code, out, _ = run("fix", self.source)
        fixed = Path(self.tmp.name, "people.fixed.csv")
        self.assertEqual(code, 0)
        self.assertTrue(fixed.exists())
        self.assertEqual(digest(self.source), self.original_hash)
        self.assertIn("original file was not changed", out)
        self.assertIn("2020-02-30", out)                 # the unfixable issue is still listed
        # the output is itself clean apart from what only a person can resolve
        _, out2, _ = run("check", fixed, "--format", "json")
        rules = {i["rule"] for i in json.loads(out2)["issues"]}
        self.assertEqual(rules, {"invalid_date", "outlier"})

    def test_custom_output_path(self):
        target = Path(self.tmp.name, "clean_copy.csv")
        code, _, _ = run("fix", self.source, "-o", target)
        self.assertEqual(code, 0)
        self.assertTrue(target.exists())

    def test_refuses_to_write_over_the_input_even_with_force(self):
        code, _, err = run("fix", self.source, "-o", self.source, "--force")
        self.assertEqual(code, 2)
        self.assertIn("Refusing", err)
        self.assertEqual(digest(self.source), self.original_hash)

    def test_refuses_an_existing_output_unless_forced(self):
        target = Path(self.tmp.name, "out.csv")
        target.write_text("precious\n")
        code, _, err = run("fix", self.source, "-o", target)
        self.assertEqual((code, target.read_text()), (2, "precious\n"))
        self.assertIn("--force", err)
        code, _, _ = run("fix", self.source, "-o", target, "--force")
        self.assertEqual(code, 0)
        self.assertNotEqual(target.read_text(), "precious\n")
        self.assertEqual(digest(self.source), self.original_hash)

    def test_nothing_to_fix_writes_no_file(self):
        clean = Path(self.tmp.name, "clean.csv")
        clean.write_text(CLEAN)
        code, out, _ = run("fix", clean)
        self.assertEqual(code, 0)
        self.assertIn("nothing can be fixed", out)
        self.assertFalse(Path(self.tmp.name, "clean.fixed.csv").exists())

    def test_json_summary(self):
        code, out, _ = run("fix", self.source, "--format", "json")
        data = json.loads(out)
        self.assertEqual((code, data["rows_before"], data["rows_after"]), (0, 21, 20))
        self.assertEqual(len(data["applied"]), 9)
        self.assertEqual({i["rule"] for i in data["remaining"]}, {"invalid_date", "outlier"})

    def test_no_temporary_files_are_left_behind(self):
        run("fix", self.source)
        names = sorted(p.name for p in Path(self.tmp.name).iterdir())
        self.assertEqual(names, ["people.csv", "people.fixed.csv"])


class OtherCommandTests(unittest.TestCase):
    def test_rules_lists_every_rule(self):
        code, out, _ = run("rules")
        self.assertEqual(code, 0)
        for rule in RULES:
            self.assertIn(rule, out)

    def test_version(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit) as ctx:
                main(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("csv-doctor", out.getvalue())

    def test_python_dash_m_entry_point(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        proc = subprocess.run([sys.executable, "-m", "csv_doctor", "check", str(EXAMPLE)],
                              capture_output=True, text=True, env=env, cwd=ROOT)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("issue(s) found", proc.stdout)


if __name__ == "__main__":
    unittest.main()


class OnlyOptionTests(unittest.TestCase):
    def fix(self, text, *args):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp, "in.csv"), Path(tmp, "out.csv")
            src.write_text(text, newline="")
            code, stdout, stderr = run("fix", src, "-o", out, *args)
            return code, (out.read_text() if out.exists() else None), stderr

    TEXT = "id,note\n1, a  b \n2,N/A\n3,x\n4,y\n5,z\n6,w\n"

    def test_only_one_rule_runs(self):
        code, out, _ = self.fix(self.TEXT, "--only", "whitespace")
        self.assertEqual((code, out), (0, "id,note\n1,a  b\n2,N/A\n3,x\n4,y\n5,z\n6,w\n"))

    def test_only_can_be_repeated_and_combined_with_ignore(self):
        _, out, _ = self.fix(self.TEXT, "--only", "whitespace", "--only", "repeated_spaces")
        self.assertEqual(out, "id,note\n1,a b\n2,N/A\n3,x\n4,y\n5,z\n6,w\n")
        _, out, _ = self.fix(self.TEXT, "--only", "whitespace", "--only", "repeated_spaces", "--ignore", "repeated_spaces")
        self.assertEqual(out, "id,note\n1,a  b\n2,N/A\n3,x\n4,y\n5,z\n6,w\n")

    def test_unknown_rule_name_is_an_error(self):
        code, _, err = self.fix(self.TEXT, "--only", "nonsense")
        self.assertEqual(code, 2)
        self.assertIn("Unknown rule", err)

    def test_check_accepts_only_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp, "in.csv")
            src.write_text(self.TEXT)
            code, out, _ = run("check", src, "--only", "missing_placeholder")
        self.assertEqual(code, 1)
        self.assertIn("missing_placeholder", out)
        self.assertNotIn("whitespace (", out)
