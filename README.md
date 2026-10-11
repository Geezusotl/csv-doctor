# CSV Doctor

Find the problems in a CSV file, **explain why each one was flagged**, and write a cleaned copy without ever touching the original.

Most cleaning tools silently "fix" your data. CSV Doctor shows its evidence instead: every finding says which cell, which rule fired, the reasoning in plain language, and exactly what it would change. Anything it can't know for certain (an impossible date, a suspicious number) is reported and left alone.

- **Zero dependencies.** Standard-library Python 3.10+.
- **Safe by design.** `fix` writes a new file and refuses to overwrite the input, even with `--force`.
- **Measured, not claimed.** See [Benchmark](#benchmark), including the cases where it gets things wrong.

## Quick start

```bash
git clone https://github.com/Geezusotl/csv-doctor.git
cd csv-doctor
pip install .

csv-doctor check examples/employees_messy.csv
csv-doctor fix   examples/employees_messy.csv     # writes examples/employees_messy.fixed.csv
```

You can also run it without installing: `PYTHONPATH=src python -m csv_doctor check yourfile.csv`.

## What it looks like

```text
$ csv-doctor check examples/employees_messy.csv
examples/employees_messy.csv: 21 data rows, 6 columns
11 issue(s) found across 10 rule(s).

mixed_date_format (2): A date written differently from the rest of its column.
  row 6, column 'hire_date': '03/14/2021'
      why: Column 'hire_date' mostly uses YYYY-MM-DD (19 of 21 dates), but '03/14/2021' uses MM/DD/YYYY. It can be rewritten in the column's main format.
      fix: '2021-03-14'

invalid_date (1): A value in a date column that is not a real date.
  row 9, column 'hire_date': '2020-02-30'
      why: '2020-02-30' in column 'hire_date' is written like a YYYY-MM-DD date but is not a real calendar date. No automatic fix is offered: the intended date is unknown.
      fix: no automatic fix

category_typo (1): A rare category one or two edits away from a common one.
  row 8, column 'department': 'Enginering'
      why: 'Enginering' appears 1 time(s) in column 'department', while 'Engineering' appears 5 times and is one edit away, so this is probably a typo.
      fix: 'Engineering'
...
```

(Excerpt; the full run reports 11 issues.) Fixing:

```text
$ csv-doctor fix examples/employees_messy.csv
Wrote a cleaned copy to examples/employees_messy.fixed.csv (the original file was not changed).
Applied 9 fix(es) in 1 pass(es): whitespace 1, missing_placeholder 1, duplicate_row 1, mixed_date_format 2, number_format 1, mixed_type 1, inconsistent_case 1, category_typo 1.
Data rows: 21 -> 20.

2 issue(s) still need a person to look at them:

invalid_date (1): A value in a date column that is not a real date.
  row 9, column 'hire_date': '2020-02-30'
  ...
outlier (1): A number far outside the typical range of its column.
  row 15, column 'age': '250'
  ...
```

Use `--format json` on either command for machine-readable output (each issue has `rule`, `row`, `column`, `value`, `why` and `fix`).

## Commands

| Command | What it does | Exit code |
|---|---|---|
| `csv-doctor check FILE` | Report problems. Changes nothing. | `0` clean, `1` issues found, `2` error |
| `csv-doctor fix FILE [-o OUT] [--force]` | Write a cleaned copy (default `FILE.fixed.csv`). | `0` on success, `2` on error |
| `csv-doctor rules` | List every rule. | `0` |

Useful flags: `--ignore RULE` (repeatable), `--delimiter ";"` or `tab` (default: detected), `--all` (list every finding instead of the first few per rule), `--format json`. Because `check` exits with `1` when it finds problems, it works as a CI gate for data files.

Rows are numbered like a spreadsheet: the header is row 1, the first data row is row 2.

## The rules

| Rule | Flags | Offers a fix? |
|---|---|---|
| `whitespace` | Leading, trailing or repeated spaces | Yes, trims them |
| `missing_placeholder` | `N/A`, `null`, `none`, `-`, `?`, ... standing in for a blank | Yes, blanks the cell |
| `duplicate_row` | A row exactly identical to an earlier one (spacing and capitalisation count; rows that differ only by spaces become duplicates after `fix` trims them) | Yes, deletes the later copy |
| `duplicate_header` | A blank or repeated column name | No |
| `ragged_row` | A row with more or fewer fields than the header, or a blank row | No |
| `mixed_date_format` | A date written differently from the rest of its column | Yes, rewrites it in the column's main format, unless day/month is ambiguous |
| `invalid_date` | `2021-02-30`, or text in a date column | No |
| `number_format` | `$1,200` in a column of plain numbers | Yes |
| `mixed_type` | Text in a numeric column | Only for letter-for-digit typos (`4O` becomes `40`) |
| `outlier` | A number far from the column median | No, never |
| `inconsistent_case` | `sales` among `Sales` | Yes |
| `category_typo` | A rare value one or two edits from a common one | Yes |

## How it decides

- **Columns are typed first.** A column is numeric (or a date column) when at least 80% of its non-blank values look like numbers (or dates), and categorical when it has at most 30 distinct values making up at most half the rows. Placeholders such as `N/A` are left out of that count. Columns with fewer than 5 values are not analysed.
- **Outliers** use the median and median absolute deviation, so one huge value can't hide itself by inflating the average. A value more than 4 robust standard deviations out is flagged. Columns containing leading-zero values such as `02134` are treated as codes and skipped.
- **Day/month ambiguity.** `03/04/2021` could be March 4 or April 3. If any other date in the column has a part above 12 (`25/12/2021`), that settles the order. Otherwise the value is flagged with no suggested fix.
- **Typos** need a rare value (at most 2 occurrences, at least 5 letters) within 1 edit of a common one (2 edits for values of 9+ letters) that appears at least 5 times and at least 5 times as often. Labels that differ only by a tag (`Class A` vs `Class B`, `Room 101` vs `Room 102`) are never treated as typos.
- **Fixing repeats until stable.** Correcting a typo can make two rows identical, so `fix` re-runs the checks after each pass (up to 6) and reports every change using the row numbers of your original file.

## Safety guarantees

These are enforced by tests, not just documented:

- The input file is never modified. `fix` refuses to write over it, including through a symlink, even with `--force`.
- An existing output file is never replaced unless you pass `--force`.
- Output is written atomically, so a crash can't leave a half-written file.
- A cell that isn't flagged comes through untouched.
- Running `fix` on its own output changes nothing more.
- If nothing can be fixed automatically, no output file is created.

Fixed files are written as UTF-8 with `\n` line endings. Input encodings tried: UTF-16 (BOM, or NUL-byte pattern), UTF-8 (with or without BOM), Windows-1252, then Latin-1. An empty file, a file with an unclosed quote, and binary data are refused with a message (exit code `2`) instead of being reported as clean.

## Benchmark

`python -m benchmark.run` builds a clean 300-row table, injects 82 known errors (12 whitespace, 12 placeholders, 12 odd date formats, 6 messy numbers, 8 typos, 8 duplicate rows, and so on) and scores csv-doctor against that answer key. A finding only counts if its rule, row and column all match.

| | Result (seed 7) |
|---|---|
| Errors found | 82 of 82 |
| Precision / recall | 100% / 100% |
| Suggested fixes that matched a perfect fixer | 82 of 82 (0 wrong) |
| False alarms on the original clean file | 0 |
| Cells differing from the clean original, before and after `fix` | 74 to 30 |
| Correct cells broken by `fix` | 0 |

Across 30 random files the numbers hold (the lowest score was still 100%), with one clean file in 30 getting a false outlier flag on a genuine value.

**Read that with a caveat.** I wrote both the generator and the checks, so every injected error is a kind the tool was designed to catch. The perfect score shows the parts agree with each other and will flag regressions. It does not predict accuracy on your files. The remaining 30 wrong cells after `fix` are the ones that need a person: blanks that can't be restored, impossible dates, outliers and unreadable numbers.

### Known limitations

The same benchmark also runs *valid* data that looks suspicious. Full table in [`benchmark/RESULTS.md`](benchmark/RESULTS.md). The failures are real:

- **Skewed numbers** such as salaries get legitimate high earners flagged as outliers. They are flagged only, never changed.
- **`NA`** is a valid country code (Namibia) but is treated as a missing-value marker. Use `--ignore missing_placeholder`.
- **Rare real categories** close to common ones (`Ireland` among many `Iceland`) look like typos. Review before accepting, or use `--ignore category_typo`.
- Short category values (under 5 letters) are never checked for typos.
- Dates are recognised in seven common layouts. Anything else in a date column is reported as `invalid_date`.

## Tests

```bash
python -m unittest discover -s tests -t .     # 104 tests, about 8 seconds, no installs needed
```

The suite covers each rule, the quality of every explanation (each must name its value and column, and unfixable ones must say why), the safety guarantees above, empty and malformed files, encodings and delimiters, a 40,000-row file, 900 seeded random tables checking that nothing crashes and `fix` is idempotent, and the benchmark floors. To check the tests themselves, deliberate bugs were planted in the code (overwriting the input, flipping day/month, breaking a rule, and so on) and all 13 were caught. pytest can also run the suite if you prefer it.

CI (GitHub Actions) runs the suite on Python 3.10 to 3.13 and fails if `benchmark/RESULTS.md` is out of date.

## Project layout

```text
src/csv_doctor/
  core.py      load/parse/write CSV (encodings, delimiters, atomic writes)
  checks.py    every rule; each finding carries its own explanation
  fixer.py     multi-pass fixing and the never-overwrite checks
  report.py    text and JSON output
  cli.py       the command line
benchmark/     generator, scorer, valid-but-tricky cases, RESULTS.md
tests/         unittest suite
examples/      a small messy file to try
```

## License

MIT
