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
    """Decode file bytes: UTF-16 (BOM or obvious NUL pattern), UTF-8 (BOM allowed),
    then Windows-1252, then Latin-1."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16")  # the BOM picks the byte order and is dropped
        except UnicodeDecodeError:
            raise CsvDoctorError("The file starts like UTF-16 text but is damaged, so it cannot be read safely.") from None
    if b"\x00" in raw:
        sample = raw[:4096]
        even, odd = sample[0::2].count(0), sample[1::2].count(0)
        half = max(1, len(sample) // 2)
        for nul_share, name in ((even / half, "utf-16-be"), (odd / half, "utf-16-le")):
            if nul_share > 0.3:  # text that is mostly ASCII has a NUL in every other byte
                try:
                    return raw.decode(name)
                except UnicodeDecodeError:
                    break
        raise CsvDoctorError("The file contains NUL bytes and is not recognisable UTF-16 text, "
                             "so it is probably not a CSV file.")
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


def _read_records(text: str, delimiter: str) -> list:
    """Parse records, refusing to guess when a quoted field is never closed.

    Python's reader quietly folds the rest of the file into one cell in that
    case, which would hide every later row. Other quoting oddities (a stray
    quote inside a value, say) keep the reader's lenient behaviour.
    """
    strict = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    records, start_line = [], 1
    try:
        while True:
            start_line = strict.line_num + 1
            try:
                records.append(next(strict))
            except StopIteration:
                return records
    except csv.Error as exc:
        if "unexpected end of data" in str(exc):
            raise CsvDoctorError(
                f"A quoted field that starts on line {start_line} is never closed, so everything "
                "after it would be read as one cell. Add the missing closing quote and try again."
            ) from None
    return list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))


def parse_text(text: str, delimiter: Optional[str] = None) -> Table:
    """Parse CSV text. The first record is the header."""
    if delimiter is None:
        delimiter = sniff_delimiter(text)
    if not text.strip():
        raise CsvDoctorError("The file is empty (no header and no data), so there is nothing to check.")
    records = _read_records(text, delimiter)
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
