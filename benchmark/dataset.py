"""Deterministic generator for a messy CSV whose errors are all recorded.

``build(seed)`` returns a clean table, a dirty copy, and an answer key listing
every injected error: which row, which column, which rule should catch it, and
what a perfect fixer would write. Errors never share a row, so the answer key
is unambiguous.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from csv_doctor import DELETE_ROW

HEADER = ["id", "name", "email", "age", "department", "signup_date", "salary", "country"]
COL = {name: i for i, name in enumerate(HEADER)}

FIRST = ["Alice", "Bruno", "Chen", "Dana", "Elif", "Farid", "Grace", "Hiro", "Imani", "Jonas",
         "Keiko", "Liam", "Maya", "Noor", "Omar", "Priya", "Quinn", "Rosa", "Sven", "Tariq"]
LAST = ["Anders", "Baker", "Costa", "Diaz", "Evans", "Fischer", "Garcia", "Haddad", "Ito", "Jensen",
        "Khan", "Lopez", "Meyer", "Nguyen", "Okafor", "Patel", "Rossi", "Silva", "Tanaka", "Walker"]
DEPARTMENTS = ["Engineering", "Marketing", "Sales", "Support", "Finance", "Operations"]
COUNTRIES = ["United States", "Canada", "Germany", "Brazil", "India"]
PLACEHOLDERS = ["N/A", "null", "none", "-", "?", "missing", "NA", "unknown"]
NOT_A_NUMBER = ["twenty", "forty-two", "abc", "???"]
NOT_A_DATE = ["2021-02-30", "2022-13-05", "2021-04-31", "2021-00-10", "next week"]

# How many errors of each kind to inject into a 300-row file.
COUNTS = {
    "whitespace": 12, "missing_placeholder": 12, "mixed_date_format": 12, "invalid_date": 4,
    "number_format": 6, "mixed_type": 6, "outlier": 5, "inconsistent_case": 6,
    "category_typo": 8, "duplicate_row": 8, "ragged_row": 3, "spreadsheet_formula": 3,
}

# Cells that a spreadsheet may execute. There is no automatic fix; the rule only warns.
FORMULAS = ["=HYPERLINK(\"http://example.invalid\",\"click\")", "@SUM(1,2)", "+cmd|' /c calc'!A0"]


@dataclass(frozen=True)
class Injected:
    rule: str
    row: int                   # spreadsheet row number (header = 1)
    column: Optional[str]      # None for whole-row errors
    original: Optional[str]    # the clean value
    dirty: Optional[str]       # what was written instead
    expected_fix: Optional[str]  # what a perfect fixer writes; None = no fix expected

    @property
    def key(self):
        return (self.rule, self.row, self.column)


@dataclass
class Dataset:
    header: list
    clean: list
    dirty: list
    truth: list


def _clean_rows(rng: random.Random, n: int) -> list:
    rows = []
    for i in range(n):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        age = int(min(75, max(18, rng.gauss(40, 11))))
        signup = date(2020, 1, 1) + timedelta(days=rng.randrange(0, 1400))
        salary = int(round(rng.gauss(65000, 9000), -2))
        rows.append([
            str(i + 1), f"{first} {last}", f"{first}.{last}.{i + 1}@example.com".lower(),
            str(age), rng.choice(DEPARTMENTS), signup.isoformat(), str(salary),
            rng.choice(COUNTRIES),
        ])
    return rows


def _typo(rng: random.Random, value: str) -> str:
    i = rng.randrange(1, len(value) - 1)
    kind = rng.choice(["delete", "double", "swap", "substitute"])
    if kind == "delete":
        out = value[:i] + value[i + 1:]
    elif kind == "double":
        out = value[:i] + value[i] + value[i:]
    elif kind == "swap":
        out = value[:i] + value[i + 1] + value[i] + value[i + 2:]
    else:
        out = value[:i] + ("x" if value[i] != "x" else "y") + value[i + 1:]
    return out if out.casefold() != value.casefold() else value[:-1]


def _garble_age(rng: random.Random, age: str, k: int):
    """Return ``(dirty, expected_fix)``: letters for digits when possible, else junk."""
    if k % 3 == 0 and "0" in age:
        return age.replace("0", "O", 1), age
    if k % 3 == 1 and "1" in age:
        return age.replace("1", "l", 1), age
    return rng.choice(NOT_A_NUMBER), None


def _other_date_format(d: date, k: int) -> str:
    return [d.strftime("%m/%d/%Y"), d.strftime("%d.%m.%Y"),
            d.strftime("%d %b %Y"), d.strftime("%b %d, %Y")][k % 4]


def build(seed: int = 7, n: int = 300) -> Dataset:
    rng = random.Random(seed)
    clean = _clean_rows(rng, n)
    dirty = [r[:] for r in clean]
    pool = list(range(n))
    rng.shuffle(pool)

    def take(k):
        return [pool.pop() for _ in range(k)]

    truth = []

    def inject(rule, i, column, dirty_value, expected_fix, original=None):
        c = COL[column]
        original = clean[i][c] if original is None else original
        dirty[i][c] = dirty_value
        truth.append(Injected(rule, i + 2, column, original, dirty_value, expected_fix))

    dup_sources = take(COUNTS["duplicate_row"])
    ragged = take(COUNTS["ragged_row"])

    for k, i in enumerate(take(COUNTS["whitespace"])):
        column = rng.choice(["name", "email", "department", "country"])
        v = clean[i][COL[column]]
        variant = ["lead", "trail", "both", "inner"][k % 4]
        if variant == "inner" and " " not in v:
            variant = "trail"
        dirty_value = {"lead": "  " + v, "trail": v + "  ", "both": " " + v + " ",
                       "inner": v.replace(" ", "   ", 1) if " " in v else v}[variant]
        inject("repeated_spaces" if variant == "inner" else "whitespace", i, column, dirty_value, v)

    for i in take(COUNTS["missing_placeholder"]):
        column = rng.choice(["name", "email", "age", "department", "signup_date", "salary", "country"])
        inject("missing_placeholder", i, column, rng.choice(PLACEHOLDERS), "")

    for k, i in enumerate(take(COUNTS["mixed_date_format"])):
        d = date.fromisoformat(clean[i][COL["signup_date"]])
        inject("mixed_date_format", i, "signup_date", _other_date_format(d, k),
               clean[i][COL["signup_date"]])

    for i in take(COUNTS["invalid_date"]):
        inject("invalid_date", i, "signup_date", rng.choice(NOT_A_DATE), None)

    for i in take(COUNTS["number_format"]):
        v = clean[i][COL["salary"]]
        inject("number_format", i, "salary",
               rng.choice([f"${int(v):,}", f"{int(v):,}", f"${v}"]), v)

    for k, i in enumerate(take(COUNTS["mixed_type"])):
        dirty_value, fix = _garble_age(rng, clean[i][COL["age"]], k)
        inject("mixed_type", i, "age", dirty_value, fix)

    outliers = [("age", "250"), ("age", "999"), ("age", "180"), ("salary", "9500000"), ("salary", "4000000")]
    for i, (column, v) in zip(take(COUNTS["outlier"]), outliers):
        inject("outlier", i, column, v, None)

    for k, i in enumerate(take(COUNTS["inconsistent_case"])):
        column = ["department", "country"][k % 2]
        v = clean[i][COL[column]]
        inject("inconsistent_case", i, column, v.lower() if k % 3 else v.upper(), v)

    # The typo rule ignores values under 5 letters, so only inject into longer ones.
    typos = 0
    while typos < COUNTS["category_typo"]:
        i = pool.pop()
        usable = [c for c in ("department", "country") if len(clean[i][COL[c]]) >= 6]
        if usable:
            column = rng.choice(usable)
            inject("category_typo", i, column, _typo(rng, clean[i][COL[column]]), clean[i][COL[column]])
            typos += 1

    for k, i in enumerate(take(COUNTS["spreadsheet_formula"])):
        inject("spreadsheet_formula", i, "name", FORMULAS[k % len(FORMULAS)], None)

    for i in ragged:
        truth.append(Injected("ragged_row", i + 2, None, None, None, None))
        dirty[i] = dirty[i][:-1]

    for src in dup_sources:
        dirty.append(clean[src][:])
        truth.append(Injected("duplicate_row", len(dirty) + 1, None, None, None, DELETE_ROW))

    return Dataset(HEADER, clean, dirty, truth)
