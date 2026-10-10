"""Loading and saving CSV files as a simple in-memory table."""
from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

CANDIDATE_DELIMITERS = ",;\t|"


class CsvDoctorError(Exception):
    """A problem worth telling the user about (bad path, unreadable file, ...)."""


@dataclass
class Table:
    """A CSV file held in memory.

    ``rows`` are the data rows exactly as read, so they may be ragged.

    Row numbers are spreadsheet-style everywhere in this project: the header is
    row 1 and the first data row is row 2. ``row_ids`` optionally maps each row
    to its row number in the *original* file, so findings keep pointing at the
    right place after earlier passes removed some rows.
    """

    header: list
    rows: list
    delimiter: str = ","
    row_ids: Optional[list] = None

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    @property
    def n_cols(self) -> int:
        return len(self.header)

    def row_number(self, index: int) -> int:
        if self.row_ids is not None:
            return self.row_ids[index]
        return index + 2


def decode(raw: bytes) -> str:
    """Decode file bytes: UTF-8 (BOM allowed), then Windows-1252, then Latin-1."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def sniff_delimiter(text: str) -> str:
    sample = text[:16384]
    try:
        return csv.Sniffer().sniff(sample, delimiters=CANDIDATE_DELIMITERS).delimiter
    except csv.Error:
        return ","


def parse_text(text: str, delimiter: Optional[str] = None) -> Table:
    """Parse CSV text. The first record is the header."""
    if delimiter is None:
        delimiter = sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    records = list(reader)
    if not records:
        return Table([], [], delimiter)
    header, rows = records[0], records[1:]
    if len(header) == 1:
        # In a one-column file a blank line is a blank value, not a blank row.
        rows = [r if r else [""] for r in rows]
    return Table(header, rows, delimiter)


def load_table(path, delimiter: Optional[str] = None) -> Table:
    p = Path(path)
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        raise CsvDoctorError(f"File not found: {p}") from None
    except IsADirectoryError:
        raise CsvDoctorError(f"{p} is a directory, not a CSV file") from None
    except OSError as exc:
        raise CsvDoctorError(f"Cannot read {p}: {exc.strerror or exc}") from None
    try:
        return parse_text(decode(raw), delimiter)
    except csv.Error as exc:
        raise CsvDoctorError(f"{p} could not be parsed as CSV: {exc}") from None


def dumps(header, rows, delimiter: str = ",") -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\n")
    if header or rows:
        writer.writerow(header)
        writer.writerows(rows)
    return buffer.getvalue()


def write_table(path, header, rows, delimiter: str = ",") -> None:
    """Write UTF-8 CSV atomically: a half-written file is never left behind."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "x", encoding="utf-8", newline="") as handle:
            handle.write(dumps(header, rows, delimiter))
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
