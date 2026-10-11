"""The checks. Every finding carries a plain-language reason (``why``).

Design rules:

* A check only flags something it can explain in one sentence.
* A check only offers a fix when the correct value can be worked out from the
  file itself. Otherwise the finding says so and leaves the cell alone.
* Column analysis (is this a date column? a category column?) is done once, on
  tidied values, with placeholders such as "N/A" left out.
"""
from __future__ import annotations

import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterator, Optional

from .core import Table

DELETE_ROW = "<delete-row>"  # suggestion meaning "remove this whole row"

MIN_VALUES = 5       # columns with fewer values are not typed or analysed
DOMINANCE = 0.8      # share of values that must look like numbers / dates
OUTLIER_Z = 4.0      # robust standard deviations from the median
MIN_OUTLIER_VALUES = 8
MAX_CATEGORIES = 30

RULES = {
    "whitespace": "Leading or trailing spaces or tabs in a cell.",
    "repeated_spaces": "Two or more spaces in a row inside a cell.",
    "missing_placeholder": "Text such as N/A, null or '-' standing in for a blank.",
    "duplicate_row": "A row identical to an earlier row.",
    "duplicate_header": "A blank or repeated column name.",
    "ragged_row": "A row with a different number of fields than the header.",
    "mixed_date_format": "A date written differently from the rest of its column.",
    "invalid_date": "A value in a date column that is not a real date.",
    "number_format": "A number with a currency symbol or thousands separator in a plain-number column.",
    "mixed_type": "Text in a column that is otherwise numbers.",
    "outlier": "A number far outside the typical range of its column.",
    "inconsistent_case": "A category spelled with different capitalisation from the rest.",
    "category_typo": "A rare category one or two edits away from a common one.",
    "spreadsheet_formula": "Text a spreadsheet may run as a formula when the file is opened (starts with =, or looks like +SUM(...)).",
}

PLACEHOLDERS = frozenset(
    {"n/a", "na", "n.a.", "null", "none", "nan", "nil", "-", "--", "?",
     "missing", "unknown", "#n/a", "undefined", "tbd"}
)


@dataclass(frozen=True)
class Finding:
    rule: str
    row: int                      # spreadsheet row number (header = 1)
    col: Optional[int]            # column index, None for whole-row findings
    column: Optional[str]         # column name
    value: Optional[str]          # the cell exactly as written in the file
    why: str                      # plain-language reason
    suggestion: Optional[str] = None  # replacement text, DELETE_ROW, or None

    @property
    def fixable(self) -> bool:
        return self.suggestion is not None

    def to_dict(self) -> dict:
        if self.suggestion is None:
            fix = None
        elif self.suggestion == DELETE_ROW:
            fix = {"action": "delete_row"}
        else:
            fix = {"action": "replace", "value": self.suggestion}
        return {
            "rule": self.rule,
            "row": self.row,
            "column": self.column,
            "value": self.value,
            "why": self.why,
            "fix": fix,
        }


def _no_fix(reason: str) -> str:
    return f" No automatic fix is offered: {reason}."


# --------------------------------------------------------------------------
# Tidying and column model
# --------------------------------------------------------------------------

_SPACES = re.compile(r"[ \t]{2,}")


def normalize(value: str) -> str:
    """Strip the ends and collapse runs of spaces. Idempotent."""
    return _SPACES.sub(" ", value.strip())


_FORMULA = re.compile(r"""
    =.+                                  # = followed by anything
  | [@+\-]\s*[A-Za-z_][A-Za-z0-9_.]*\(.*    # @SUM(  +SUM(  -cmd(
  | [=@+\-].*\|.*!.*                    # DDE style:  +cmd|'/c calc'!A0
""", re.VERBOSE | re.DOTALL)


def looks_like_formula(value: str) -> bool:
    """True for text that Excel, Sheets or LibreOffice may execute when a CSV is opened.

    Plain signed numbers such as "-123" or "+1.5" are not formulas.
    """
    return bool(_FORMULA.fullmatch(value.lstrip(" ")))


def is_placeholder(value: str) -> bool:
    return value.casefold() in PLACEHOLDERS


@dataclass
class Cell:
    row: int
    raw: str   # exactly as written in the file
    val: str   # tidied


@dataclass
class Column:
    index: int
    name: str
    cells: list
    data: list = field(default_factory=list)  # non-empty, non-placeholder cells
    kind: str = "text"                        # numeric | date | categorical | text


# --- numbers ---------------------------------------------------------------

_PLAIN = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$", re.ASCII)
_FORMATTED = re.compile(
    r"^[+-]?[$€£](\d{1,3}(,\d{3})+|\d+)(\.\d+)?$"
    r"|^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$",
    re.ASCII,
)


def parse_number(value: str):
    """Return ``(number, clean_text, is_plain)`` or None if not a number."""
    if _PLAIN.match(value):
        number, text, plain = float(value), value, True
    elif _FORMATTED.match(value):
        text = re.sub(r"[$€£,]", "", value)
        number, plain = float(text), False
    else:
        return None
    if not math.isfinite(number):
        return None
    return number, text, plain


_OCR = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1"})


def _ocr_fix(value: str) -> Optional[str]:
    """'4O' -> '40', '3l' -> '31': letters typed where digits were meant."""
    if len(value) > 12 or not any(ch in "0123456789" for ch in value):
        return None
    fixed = value.translate(_OCR)
    parsed = parse_number(fixed)
    return fixed if parsed is not None and parsed[2] else None


# --- dates -----------------------------------------------------------------


@dataclass(frozen=True)
class DateShape:
    name: str
    pattern: "re.Pattern"
    parse_formats: tuple
    out_format: str
    label: str
    sep: Optional[str] = None  # set for the two ambiguous numeric shapes

    @property
    def numeric(self) -> bool:
        return self.sep is not None


SHAPES = (
    DateShape("iso", re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$", re.ASCII),
              ("%Y-%m-%d",), "%Y-%m-%d", "YYYY-MM-DD"),
    DateShape("ymd_slash", re.compile(r"^\d{4}/\d{1,2}/\d{1,2}$", re.ASCII),
              ("%Y/%m/%d",), "%Y/%m/%d", "YYYY/MM/DD"),
    DateShape("slash", re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$", re.ASCII),
              (), "", "", sep="/"),
    DateShape("dash", re.compile(r"^\d{1,2}-\d{1,2}-\d{4}$", re.ASCII),
              (), "", "", sep="-"),
    DateShape("dotted", re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$", re.ASCII),
              ("%d.%m.%Y",), "%d.%m.%Y", "DD.MM.YYYY"),
    DateShape("dmy_text", re.compile(r"^\d{1,2}[ -][A-Za-z]{3,9}[ -]\d{4}$", re.ASCII),
              ("%d %b %Y", "%d-%b-%Y", "%d %B %Y", "%d-%B-%Y"), "%d %b %Y", "DD Mon YYYY"),
    DateShape("mdy_text", re.compile(r"^[A-Za-z]{3,9} \d{1,2}, \d{4}$", re.ASCII),
              ("%b %d, %Y", "%B %d, %Y"), "%b %d, %Y", "Mon DD, YYYY"),
)
SHAPES_BY_NAME = {s.name: s for s in SHAPES}


def date_shape(value: str) -> Optional[DateShape]:
    for shape in SHAPES:
        if shape.pattern.match(value):
            return shape
    return None


def _infer_order(values) -> Optional[str]:
    """For DD/MM vs MM/DD: look for a part above 12, which settles the order."""
    dmy = mdy = 0
    for value in values:
        a, b = (int(x) for x in re.split(r"[/-]", value)[:2])
        if a > 12 and b <= 12:
            dmy += 1
        elif b > 12 and a <= 12:
            mdy += 1
    if dmy > mdy:
        return "dmy"
    if mdy > dmy:
        return "mdy"
    return None


def parse_date(value: str, shape: DateShape, order: Optional[str]):
    """Return ``(date or None, status)``; status is ok, invalid or ambiguous."""
    if shape.numeric:
        a, b, year = (int(x) for x in value.split(shape.sep))
        if order is None:
            if a == b:
                order = "mdy"
            elif a > 12 and b > 12:
                return None, "invalid"
            elif a > 12:
                order = "dmy"
            elif b > 12:
                order = "mdy"
            else:
                return None, "ambiguous"
        month, day = (a, b) if order == "mdy" else (b, a)
        try:
            return date(year, month, day), "ok"
        except ValueError:
            return None, "invalid"
    for fmt in shape.parse_formats:
        try:
            return datetime.strptime(value, fmt).date(), "ok"
        except ValueError:
            continue
    return None, "invalid"


def _label(shape: DateShape, order: Optional[str]) -> str:
    if not shape.numeric:
        return shape.label
    s = shape.sep
    if order == "mdy":
        return f"MM{s}DD{s}YYYY"
    if order == "dmy":
        return f"DD{s}MM{s}YYYY"
    return f"MM{s}DD{s}YYYY or DD{s}MM{s}YYYY"


def _output_format(shape: DateShape, order: Optional[str]) -> Optional[str]:
    if not shape.numeric:
        return shape.out_format
    s = shape.sep
    if order == "mdy":
        return f"%m{s}%d{s}%Y"
    if order == "dmy":
        return f"%d{s}%m{s}%Y"
    return None


# --- column typing ---------------------------------------------------------


def infer_kind(data: list) -> str:
    n = len(data)
    if n < MIN_VALUES:
        return "text"
    numbers = sum(1 for c in data if parse_number(c.val) is not None)
    if numbers / n >= DOMINANCE:
        return "numeric"
    dates = sum(1 for c in data if date_shape(c.val) is not None)
    if dates / n >= DOMINANCE:
        return "date"
    distinct = len({c.val for c in data})
    if distinct <= MAX_CATEGORIES and distinct / n <= 0.5:
        return "categorical"
    return "text"


def build_columns(table: Table) -> list:
    columns = []
    for j, name in enumerate(table.header):
        cells = []
        for i, row in enumerate(table.rows):
            if j < len(row):
                cells.append(Cell(table.row_number(i), row[j], normalize(row[j])))
        col = Column(j, name, cells)
        col.data = [c for c in cells if c.val and not is_placeholder(c.val)]
        col.kind = infer_kind(col.data)
        columns.append(col)
    return columns


def _finding(rule: str, cell: Cell, col: Column, why: str, suggestion=None) -> Finding:
    return Finding(rule, cell.row, col.index, col.name, cell.raw, why, suggestion)


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------


def check_cells(col: Column) -> Iterator[Finding]:
    """Rules that apply to any column: whitespace and missing-value placeholders."""
    for c in col.cells:
        if c.raw != c.raw.strip():
            yield _finding(
                "whitespace", c, col,
                f"{c.raw!r} in column {col.name!r} has leading or trailing spaces.",
                c.raw.strip(),
            )
        if c.raw.strip() and _SPACES.search(c.raw.strip()):
            yield _finding(
                "repeated_spaces", c, col,
                f"{c.raw!r} in column {col.name!r} has two or more spaces in a row inside the text "
                f"(use --ignore repeated_spaces if the spacing is meaningful here).",
                # Only the inner runs are collapsed; any outer spaces belong to the whitespace rule.
                _SPACES.sub(" ", c.raw),
            )
        if looks_like_formula(c.raw):
            yield _finding(
                "spreadsheet_formula", c, col,
                f"{c.raw!r} in column {col.name!r} may be run as a formula when this file is opened in "
                f"Excel, Google Sheets or LibreOffice (CSV injection)."
                + _no_fix("adding a prefix would change the data, and it may be intentional"),
            )
        if c.val and is_placeholder(c.val):
            yield _finding(
                "missing_placeholder", c, col,
                f"{c.raw!r} in column {col.name!r} stands in for a missing value. Blank cells "
                f"are the standard way to write that in CSV (use --ignore missing_placeholder "
                f"if it is meaningful here).",
                "",
            )


def check_numbers(col: Column) -> Iterator[Finding]:
    n = len(col.data)
    parsed, bad = [], []
    for c in col.data:
        p = parse_number(c.val)
        if p is None:
            bad.append(c)
        else:
            parsed.append((c,) + p)
    k = len(parsed)

    for c in bad:
        fixed = _ocr_fix(c.val)
        why = f"Column {col.name!r} is numeric ({k} of {n} values are numbers) but {c.raw!r} is not a number."
        if fixed is not None:
            why += " It looks like letters were typed in place of digits."
        else:
            why += _no_fix("it is not clear which number was meant")
        yield _finding("mixed_type", c, col, why, fixed)

    plain_count = sum(1 for p in parsed if p[3])
    if plain_count * 2 >= k:  # only when plain numbers are the norm
        for c, _number, text, plain in parsed:
            if not plain:
                yield _finding(
                    "number_format", c, col,
                    f"{c.raw!r} in column {col.name!r} has a currency symbol or thousands "
                    f"separator, but {plain_count} of the column's {k} numbers are plain digits.",
                    text,
                )

    yield from _outliers(col, [(p[0], p[1]) for p in parsed])


def _outliers(col: Column, parsed: list) -> Iterator[Finding]:
    if len(parsed) < MIN_OUTLIER_VALUES:
        return
    if any(re.fullmatch(r"0\d+", c.val, re.ASCII) for c, _ in parsed):
        return  # leading zeros ("02134") mean codes such as ZIPs, not quantities
    xs = [x for _, x in parsed]
    median = statistics.median(xs)
    mad = statistics.median(abs(x - median) for x in xs)
    if mad > 0:
        scale = 1.4826 * mad
    else:
        mean_ad = statistics.fmean(abs(x - median) for x in xs)
        if mean_ad == 0:
            return
        scale = 1.253314 * mean_ad
    for c, x in parsed:
        z = abs(x - median) / scale
        if z > OUTLIER_Z:
            yield _finding(
                "outlier", c, col,
                f"{c.raw!r} in column {col.name!r} is {z:.1f} robust standard deviations from "
                f"the column median ({median:g}); anything beyond {OUTLIER_Z:g} is flagged. "
                f"It may be a genuine value."
                + _no_fix("only a person can tell whether it is an error"),
            )


def check_dates(col: Column) -> Iterator[Finding]:
    shaped = [(c, date_shape(c.val)) for c in col.data]
    counts = Counter(s.name for _, s in shaped if s is not None)
    if not counts:
        return
    dominant_name, dominant_count = counts.most_common(1)[0]
    dominant = SHAPES_BY_NAME[dominant_name]
    n_dates = sum(counts.values())
    orders = {
        name: _infer_order([c.val for c, s in shaped if s is not None and s.name == name])
        for name in counts if SHAPES_BY_NAME[name].numeric
    }
    out_format = _output_format(dominant, orders.get(dominant_name))
    dominant_label = _label(dominant, orders.get(dominant_name))

    for c, shape in shaped:
        if shape is None:
            yield _finding(
                "invalid_date", c, col,
                f"Column {col.name!r} holds dates, but {c.raw!r} does not match any recognised date format."
                + _no_fix("the intended date is unknown"),
            )
            continue
        order = orders.get(shape.name)
        parsed, status = parse_date(c.val, shape, order)
        if status == "invalid":
            yield _finding(
                "invalid_date", c, col,
                f"{c.raw!r} in column {col.name!r} is written like a {_label(shape, order)} date "
                f"but is not a real calendar date." + _no_fix("the intended date is unknown"),
            )
        elif shape.name != dominant_name:
            why = (f"Column {col.name!r} mostly uses {dominant_label} ({dominant_count} of "
                   f"{n_dates} dates), but {c.raw!r} uses {_label(shape, order)}.")
            suggestion = None
            if status == "ambiguous":
                why += _no_fix("the day and month could be read either way")
            elif out_format is None:
                why += _no_fix("the column's own day/month order is ambiguous")
            else:
                suggestion = parsed.strftime(out_format)
                why += " It can be rewritten in the column's main format."
            yield _finding("mixed_date_format", c, col, why, suggestion)


def edit_distance(a: str, b: str, limit: int) -> int:
    """Optimal-string-alignment distance (insert, delete, substitute, swap).

    Returns ``limit + 1`` early when the strings are certainly further apart.
    """
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous2 = None
    previous = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        current = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                current[j] = min(current[j], previous2[j - 2] + 1)
        previous2, previous = previous, current
    return previous[-1]


def _labels_differ_only_by_a_tag(a: str, b: str) -> bool:
    """'Class A' vs 'Class B', 'Group 1' vs 'Group 2': different labels, not typos."""
    if re.findall(r"\d+", a) != re.findall(r"\d+", b):
        return True
    ta, tb = a.split(), b.split()
    if len(ta) == len(tb):
        differing = [(x, y) for x, y in zip(ta, tb) if x != y]
        if len(differing) == 1 and len(differing[0][0]) <= 2 and len(differing[0][1]) <= 2:
            return True
    return False


def check_categories(col: Column) -> Iterator[Finding]:
    spellings = defaultdict(Counter)           # casefolded value -> {spelling: count}
    for c in col.data:
        spellings[c.val.casefold()][c.val] += 1
    totals = {fold: sum(counter.values()) for fold, counter in spellings.items()}
    best = {
        fold: sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        for fold, counter in spellings.items()
    }

    # Rare values that sit one or two edits from a common value are typos.
    typo_target = {}
    for fold, total in totals.items():
        if total > 2 or len(fold) < 5:
            continue
        limit = 1 if len(fold) < 9 else 2
        candidates = []
        for other, other_total in totals.items():
            if other == fold or other_total < max(5, 5 * total):
                continue
            if _labels_differ_only_by_a_tag(fold, other):
                continue
            distance = edit_distance(fold, other, limit)
            if distance <= limit:
                candidates.append((distance, -other_total, other))
        if candidates:
            typo_target[fold] = min(candidates)[2]

    for c in col.data:
        fold = c.val.casefold()
        if fold in typo_target:
            target = typo_target[fold]
            distance = edit_distance(fold, target, 2)
            edits = "one edit" if distance == 1 else f"{distance} edits"
            yield _finding(
                "category_typo", c, col,
                f"{c.raw!r} appears {totals[fold]} time(s) in column {col.name!r}, while "
                f"{best[target]!r} appears {totals[target]} times and is {edits} away, "
                f"so this is probably a typo.",
                best[target],
            )
        elif c.val != best[fold]:
            yield _finding(
                "inconsistent_case", c, col,
                f"{c.raw!r} in column {col.name!r} differs only in capitalisation from "
                f"{best[fold]!r}, the most common spelling ({spellings[fold][best[fold]]} of "
                f"{totals[fold]} uses).",
                best[fold],
            )


def check_header(table: Table) -> Iterator[Finding]:
    seen = {}
    for col, name in enumerate(table.header):
        label = name.strip()
        if not label:
            yield Finding("duplicate_header", 1, col, name, name,
                          f"Column {col + 1} has no name." + _no_fix("a name cannot be guessed"))
        elif label.casefold() in seen:
            first = seen[label.casefold()]
            yield Finding("duplicate_header", 1, col, name, name,
                          f"Column {col + 1} is called {name!r}, the same name as column {first + 1}, "
                          "so tools that look columns up by name will mix them up."
                          + _no_fix("the right new name is a human decision"))
        else:
            seen[label.casefold()] = col


def check_rows(table: Table) -> Iterator[Finding]:
    width = len(table.header)
    seen = {}
    for i, row in enumerate(table.rows):
        number = table.row_number(i)
        if not row:
            yield Finding("ragged_row", number, None, None, None,
                          "This row is blank, so it has no values for the header's "
                          f"{width} columns." + _no_fix("the missing values are unknown"))
            continue
        if len(row) != width:
            yield Finding("ragged_row", number, None, None, None,
                          f"Row {number} has {len(row)} field(s) but the header has {width}."
                          + _no_fix("it is unclear which values are missing or extra"))
        if not any(normalize(x) for x in row):
            continue
        # Exact comparison: "Maya " and "Maya" are different values. Rows that only
        # differ by whitespace become identical after the whitespace fix, and the
        # next pass of `fix` then removes them.
        key = tuple(row)
        if key in seen:
            yield Finding("duplicate_row", number, None, None, None,
                          f"Row {number} has exactly the same values as row {seen[key]}, "
                          f"so it adds no new information.", DELETE_ROW)
        else:
            seen[key] = number


def analyze(table: Table, ignore=()) -> list:
    """Run every check and return findings ordered by row, then column."""
    findings = list(check_header(table)) + list(check_rows(table))
    for col in build_columns(table):
        findings.extend(check_cells(col))
        if col.kind == "numeric":
            findings.extend(check_numbers(col))
        elif col.kind == "date":
            findings.extend(check_dates(col))
        elif col.kind == "categorical":
            findings.extend(check_categories(col))
    ignored = set(ignore)
    findings = [f for f in findings if f.rule not in ignored]
    findings.sort(key=lambda f: (f.row, -1 if f.col is None else f.col, f.rule))
    return findings
