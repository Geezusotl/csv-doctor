from csv_doctor import Table


def make(header, rows):
    return Table(list(header), [list(r) for r in rows])


def column(values, name="x"):
    """A one-column table."""
    return make([name], [[v] for v in values])


def only(findings, rule):
    return [f for f in findings if f.rule == rule]


def by_value(findings):
    """{cell value: suggested replacement}"""
    return {f.value: f.suggestion for f in findings}
