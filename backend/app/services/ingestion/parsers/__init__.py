"""
Parser registry: pick a parser by file extension.

To support a new format, write a function `parse_xxx(path) -> ParsedDocument`
and add it to PARSERS + MIME_TYPES. (Scanned PDFs needing OCR, and Excel files,
are deliberately left for when a client actually needs them.)
"""

from collections.abc import Callable
from pathlib import Path

from app.services.ingestion.parsers.base import Block, ParsedDocument, ParseError
from app.services.ingestion.parsers.csv import parse_csv
from app.services.ingestion.parsers.docx import parse_docx
from app.services.ingestion.parsers.html import parse_html
from app.services.ingestion.parsers.pdf import parse_pdf
from app.services.ingestion.parsers.text import parse_markdown, parse_text

PARSERS: dict[str, Callable[[Path], ParsedDocument]] = {
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".md": parse_markdown,
    ".markdown": parse_markdown,
    ".txt": parse_text,
    ".html": parse_html,
    ".htm": parse_html,
    ".csv": parse_csv,
}

# We decide the MIME type from the extension ourselves; the Content-Type header a
# browser sends is client-controlled and can't be trusted.
MIME_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
    ".csv": "text/csv",
}

SUPPORTED_EXTENSIONS = frozenset(PARSERS)


def parse_file(path: Path, filename: str) -> ParsedDocument:
    """`path` is where the bytes are stored; `filename` (the original name) picks the parser."""
    ext = Path(filename).suffix.lower()
    parser = PARSERS.get(ext)
    if parser is None:
        raise ParseError(f"Unsupported file type: {ext or '(none)'}")
    return parser(path)


__all__ = [
    "MIME_TYPES",
    "SUPPORTED_EXTENSIONS",
    "Block",
    "ParseError",
    "ParsedDocument",
    "parse_file",
]
