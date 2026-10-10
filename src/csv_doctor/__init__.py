"""CSV Doctor: find problems in CSV files and explain every flag."""

__version__ = "0.1.0"

from .checks import DELETE_ROW, RULES, Finding, analyze  # noqa: E402
from .core import CsvDoctorError, Table, load_table, parse_text  # noqa: E402
from .fixer import FixResult, fix_table  # noqa: E402

__all__ = [
    "DELETE_ROW", "RULES", "Finding", "analyze",
    "CsvDoctorError", "Table", "load_table", "parse_text",
    "FixResult", "fix_table", "__version__",
]
