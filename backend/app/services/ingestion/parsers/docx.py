"""
Word (.docx) parser (python-docx).

Word documents carry explicit structure: paragraphs have *styles*, and headings
use the built-in "Heading 1", "Heading 2", ... (and "Title") styles. So unlike
PDFs we don't need to guess headings from font sizes.

DOCX has no fixed pages (pagination depends on the viewer's fonts/printer), so
`page` is always None and citations show the section heading instead.

Tables become one line per row with column names attached, e.g.
    "Repair type: Burst pipe; Response time: 4 hours"
because "4 hours" alone is meaningless to an embedding model or an LLM — the
column name gives it context (the plan uses the same idea for CSV in Week 4).
"""

import re
import zipfile
from pathlib import Path

import docx
from docx.document import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.services.ingestion.parsers.base import Block, ParsedDocument, ParseError

_HEADING_STYLE = re.compile(r"^Heading (\d)$")


def parse_docx(path: Path) -> ParsedDocument:
    try:
        document: DocxDocument = docx.Document(str(path))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise ParseError(f"Could not open DOCX: {exc}") from exc

    blocks: list[Block] = []
    # iter_inner_content() yields paragraphs AND tables in document order.
    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            block = _paragraph_to_block(item)
            if block:
                blocks.append(block)
        elif isinstance(item, Table):
            blocks.extend(_table_to_blocks(item))

    if not blocks:
        raise ParseError("Document contains no text")

    parsed = ParsedDocument(blocks=blocks, page_count=None)
    parsed.title = document.core_properties.title or parsed.first_heading()
    return parsed


def _paragraph_to_block(paragraph: Paragraph) -> Block | None:
    text = paragraph.text.strip()
    if not text:
        return None
    style = paragraph.style.name if paragraph.style is not None else ""
    if style == "Title":
        return Block(text, kind="heading", level=1)
    match = _HEADING_STYLE.match(style)
    if match:
        # "Title" takes level 1 when present, so Heading N sits at N + 1.
        # Levels only need to be *relative* for the chunker's heading path.
        return Block(text, kind="heading", level=int(match.group(1)) + 1)
    return Block(text)


def _table_to_blocks(table: Table) -> list[Block]:
    rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
    rows = [r for r in rows if any(r)]
    if not rows:
        return []
    header, body = rows[0], rows[1:]
    if not body:  # a one-row table: just keep the text
        return [Block(" | ".join(header))]
    lines = []
    for row in body:
        pairs = [f"{h}: {v}" for h, v in zip(header, row, strict=False) if v]
        lines.append("; ".join(pairs))
    # One block for the whole table keeps related rows together in one chunk.
    return [Block("\n".join(lines))]
