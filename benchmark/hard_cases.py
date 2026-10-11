"""Correct data that csv-doctor might wrongly flag.

Each case is a column of *valid* values. Anything reported here is a false
positive, and the README lists them as known limitations. ``expect_clean`` marks
the cases the tool is supposed to handle; the rest are documented failures.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from csv_doctor import Table, analyze


@dataclass(frozen=True)
class HardCase:
    name: str
    column: list
    expect_clean: bool
    note: str


def cases() -> list:
    rng = random.Random(11)
    skewed_salaries = [str(int(rng.lognormvariate(11, 0.6))) for _ in range(300)]
    return [
        HardCase("zip codes with leading zeros",
                 [rng.choice(["02134", "10001", "94105", "60601", "73301", "02139"]) for _ in range(60)],
                 True, "numeric-looking text must never be rewritten"),
        HardCase("version-like labels",
                 ["Plan A"] * 20 + ["Plan B"] * 20 + ["Plan C"] * 20 + ["Plan 10"],
                 True, "labels that differ by one tag are not typos"),
        HardCase("numbered groups",
                 ["Group 1"] * 20 + ["Group 2"] * 20 + ["Group 3"] * 2,
                 True, "digits that differ are different labels"),
        HardCase("phone numbers",
                 [f"555-01{rng.randrange(10, 99)}" for _ in range(40)],
                 True, "dashes inside digits are not dates or numbers"),
        HardCase("ids with a gap",
                 [str(i) for i in range(1, 60)] + [str(i) for i in range(61, 101)],
                 True, "sequential ids with one missing value"),
        HardCase("signs and @ in ordinary text",
                 ["+44 20 7946 0958", "+1 (555) 010-0199", "-12", "+7", "@alice", "@bob_smith", "a=b", "x+y"] * 6,
                 True, "a leading +, - or @ is not a formula unless it looks like one"),
        HardCase("free-text names",
                 [f"{a} {b}" for a in ("Ana", "Ben", "Cy", "Di") for b in ("Lee", "Kim", "Roy", "Fox", "Yu")] * 2,
                 True, "names repeat but are not categories to correct"),
        HardCase("skewed salaries (lognormal)",
                 skewed_salaries, False,
                 "real-world skew makes the median/MAD rule flag legitimate high earners"),
        HardCase("country code NA (Namibia)",
                 ["US"] * 30 + ["CA"] * 20 + ["DE"] * 20 + ["NA"] * 3,
                 False, "'NA' is a valid country code but is also a common missing-value marker"),
        HardCase("Ireland among Iceland",
                 ["Iceland"] * 40 + ["Norway"] * 40 + ["Ireland"],
                 False, "a real, rare value one edit from a common one looks like a typo"),
    ]


def run_cases() -> list:
    """Return ``(case, set_of_rules_that_fired)`` for every hard case."""
    out = []
    for case in cases():
        table = Table(["value"], [[v] for v in case.column])
        rules = sorted({f.rule for f in analyze(table) if f.rule != "duplicate_row"})
        out.append((case, rules))
    return out
