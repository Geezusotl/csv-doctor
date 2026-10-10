"""Tests use only the standard library.

Run them from the repository root with:  python -m unittest discover -s tests -t .
(pytest can run them too.) Importing this package makes ``src/`` importable so no
install step is needed.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _path in (ROOT / "src", ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
